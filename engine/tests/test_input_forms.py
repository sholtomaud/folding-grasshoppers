"""Input forms in the chat (#27) and Claude never calculating (#28).

Prices reach the engine only from settings. When one is missing the engine
tells Claude to call request_inputs, which shows a form in the chat. The
designer's answer is written to settings by the app; Claude is only told
which values were set, never the numbers.
"""

import json

import pytest

from cardboard_engine.design import SYSTEM_PROMPT, TOOLS, DesignSession


@pytest.fixture
def session(tmp_path):
    s = DesignSession(home=str(tmp_path))
    r = s.run_tool("create_shape", {"shape": "box", "width_mm": 200, "depth_mm": 100, "height_mm": 80})
    assert not r.is_error
    assert not s.run_tool("unfold", {}).is_error
    return s


@pytest.fixture
def client(tmp_path, monkeypatch, lifecycle):
    from cardboard_engine.server import create_app
    from tests.conftest import TOKEN

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path / "data"))
    app = create_app(shutdown_token=TOKEN, lifecycle=lifecycle)
    app.testing = True
    return app.test_client()


def tool(name):
    return next(t for t in TOOLS if t["name"] == name)


def test_estimate_cost_takes_no_prices():
    props = tool("estimate_cost")["input_schema"]["properties"]
    assert "sheet_price" not in props
    assert "machine_rate" not in props


def test_estimate_cost_with_a_price_argument_is_rejected(session):
    r = session.run_tool("estimate_cost", {"sheet_price": 5})
    assert r.is_error
    assert "settings" in r.text.lower()
    assert "request_inputs" in r.text


def test_missing_price_tells_claude_to_ask_with_a_form(session):
    r = session.run_tool("estimate_cost", {})
    assert r.is_error
    assert "request_inputs" in r.text
    assert "sheet_price" in r.text and "machine_rate" in r.text
    assert session.last_estimate is None


def test_request_inputs_returns_a_form(session):
    r = session.run_tool("request_inputs", {
        "question": "What does a sheet of 10 mm honeycomb cost?",
        "fields": [{"key": "sheet_price"}]})
    assert not r.is_error, r.text
    form = r.form
    assert form["id"]
    assert form["question"] == "What does a sheet of 10 mm honeycomb cost?"
    f = form["fields"][0]
    assert (f["key"], f["type"], f["unit"], f["label"]) == ("sheet_price", "currency", "AUD/sheet", "Sheet price")
    assert f["minimum"] == 0
    assert form["scopes"] == ["project", "all"]
    assert form["scope"] == "project"
    assert "wait" in r.text.lower()


def test_request_inputs_can_define_a_new_field(session):
    r = session.run_tool("request_inputs", {"fields": [
        {"key": "glue_per_joint", "label": "Glue per joint", "type": "currency", "unit": "AUD", "minimum": 0}]})
    assert not r.is_error, r.text
    assert r.form["fields"][0]["key"] == "glue_per_joint"


def test_request_inputs_rejects_a_new_field_without_label_or_type(session):
    r = session.run_tool("request_inputs", {"fields": [{"key": "wibble"}]})
    assert r.is_error
    assert "label" in r.text


def test_submitting_the_form_writes_settings_and_cost_runs(session, tmp_path):
    r = session.run_tool("request_inputs", {"fields": [{"key": "sheet_price"}, {"key": "machine_rate"}]})
    out = session.submit_form(r.form["id"], {"sheet_price": "38.50", "machine_rate": 60}, scope="project",
                              set_by="SM")
    assert out["ok"]
    # Claude is told what was set, never the number
    assert "sheet_price" in out["message"]
    assert "38.5" not in out["message"]
    assert session.settings.resolve("sheet_price")["value"] == 38.5
    e = session.run_tool("estimate_cost", {})
    assert not e.is_error, e.text
    assert session.last_estimate["material_cost"] == pytest.approx(38.5 * session.last_estimate["sheet_count"])


@pytest.mark.parametrize("bad", ["abc", -5, ""])
def test_a_bad_answer_shows_an_inline_error_and_writes_nothing(session, bad):
    r = session.run_tool("request_inputs", {"fields": [{"key": "sheet_price"}, {"key": "machine_rate"}]})
    out = session.submit_form(r.form["id"], {"sheet_price": bad, "machine_rate": 60}, scope="project", set_by="SM")
    assert not out["ok"]
    assert "sheet_price" in out["errors"]
    assert "machine_rate" not in out["errors"]
    assert session.settings.resolve("machine_rate")["value"] is None  # all or nothing


def test_all_projects_scope_writes_the_defaults(session, tmp_path):
    r = session.run_tool("request_inputs", {"fields": [{"key": "sheet_price"}]})
    session.submit_form(r.form["id"], {"sheet_price": 42}, scope="all", set_by="SM")
    assert DesignSession(home=str(tmp_path)).settings.resolve("sheet_price")["value"] == 42


def test_unknown_form_is_rejected(session):
    out = session.submit_form("nope", {"sheet_price": 1}, scope="project", set_by="SM")
    assert not out["ok"]


def test_quote_comes_from_formulas_with_settings(session):
    session.settings.set("sheet_price", 38.5, scope="project", set_by="SM")
    session.settings.set("machine_rate", 60, scope="project", set_by="SM")
    r = session.run_tool("estimate_cost", {})
    assert not r.is_error
    q = session.last_quote
    lines = {l["key"]: l for l in q["lines"]}
    est = session.last_estimate
    assert lines["material"]["value"] == pytest.approx(round(est["material_cost"], 2))
    assert lines["machine"]["value"] == pytest.approx(round(est["machine_cost"], 2))
    assert q["total"] == lines["total"]["value"]
    assert f"{q['total']:.2f}" in r.text
    assert q["settings"]["sheet_price"]["layer"] == "project"


def test_system_prompt_says_claude_never_calculates():
    low = SYSTEM_PROMPT.lower()
    assert "never calculate" in low or "never do arithmetic" in low
    assert "request_inputs" in SYSTEM_PROMPT


def test_set_workflow_defaults_refuses_prices(session):
    r = session.run_tool("set_workflow_defaults", {"defaults": {"estimate_cost": {"sheet_price": 3}}})
    assert r.is_error
    assert "request_inputs" in r.text


def test_old_project_with_prices_in_its_recipe_still_opens(tmp_path):
    """Projects saved before settings kept the prices in the estimate step; they become project settings."""
    s = DesignSession(home=str(tmp_path))
    s.settings.set("sheet_price", 12.5, scope="project", set_by="SM")
    s.settings.set("machine_rate", 60, scope="project", set_by="SM")
    s.run_tool("create_shape", {"shape": "box"})
    s.run_tool("unfold", {})
    s.run_tool("estimate_cost", {})
    out = s.save_project(name="Old")
    import os
    path = os.path.join(out["folder"], "project.json")
    m = json.load(open(path))
    m["recipe"]["estimate"] = {"sheet_price": 12.5, "machine_rate": 60, "cut_speed": 20}
    json.dump(m, open(path, "w"))
    os.remove(os.path.join(out["folder"], "settings.json"))
    t = DesignSession(home=str(tmp_path))
    t.open_project(out["folder"])
    assert t.settings.resolve("sheet_price")["value"] == 12.5
    assert t.settings.resolve("sheet_price")["layer"] == "project"
    assert t.last_estimate is not None


def test_tool_api_returns_the_form(client):
    client.post("/api/design/tool", json={"name": "create_shape", "input": {"shape": "box"}})
    r = client.post("/api/design/tool", json={"name": "request_inputs",
                                              "input": {"fields": [{"key": "sheet_price"}]}})
    body = r.get_json()
    assert body["is_error"] is False
    form = body["form"]
    r = client.post("/api/settings/form", json={"form_id": form["id"], "values": {"sheet_price": "abc"},
                                                "scope": "project", "set_by": "SM"})
    assert r.status_code == 422
    assert "sheet_price" in r.get_json()["errors"]
    r = client.post("/api/settings/form", json={"form_id": form["id"], "values": {"sheet_price": "38.50"},
                                                "scope": "project", "set_by": "SM"})
    assert r.status_code == 200
    assert "sheet_price" in r.get_json()["message"]


def test_quote_api(client):
    client.post("/api/settings", json={"key": "sheet_price", "value": 38.5, "scope": "project"})
    client.post("/api/settings", json={"key": "machine_rate", "value": 60, "scope": "project"})
    client.post("/api/design/tool", json={"name": "create_shape", "input": {"shape": "box"}})
    client.post("/api/design/tool", json={"name": "unfold", "input": {}})
    client.post("/api/design/tool", json={"name": "estimate_cost", "input": {}})
    q = client.get("/api/quote").get_json()
    assert q["total"] > 0
    # the number a guard in the app can check Claude's reply against
    assert f"{q['total']:.2f}" in client.get("/api/design/numbers").get_json()["numbers"]


def test_changing_a_setting_recomputes_the_quote(session):
    session.settings.set("sheet_price", 38.5, scope="project", set_by="SM")
    session.settings.set("machine_rate", 60, scope="project", set_by="SM")
    session.run_tool("estimate_cost", {})
    before = session.last_quote["total"]
    session.set_setting("sheet_price", 50, scope="project", set_by="SM")
    assert session.last_quote["total"] > before
