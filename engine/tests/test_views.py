"""Configurable views (#30) and the Project tab (#34).

Screens are JSON documents (schema cardboardcad.view/1) drawn by a fixed set
of native building blocks. A view is checked against the schema before it is
used: unknown blocks, unknown bindings and unregistered actions are rejected
with a reason, leaving the current view unchanged. Saving keeps the old file
so one undo restores it exactly.
"""

import json
import os

import pytest

from cardboard_engine.design import DesignSession
from cardboard_engine.views import BLOCK_TYPES, VIEW_SCHEMA, ACTIONS, ViewError, validate_view, views_dir


@pytest.fixture
def session(tmp_path):
    return DesignSession(home=str(tmp_path))


def costs_view():
    return {"schema": VIEW_SCHEMA, "id": "costs", "title": "Costs", "blocks": [
        {"type": "section", "title": "Quote", "blocks": [
            {"type": "table", "bind": "quote.lines", "columns": ["label", "value", "unit"]},
            {"type": "total", "bind": "quote.total", "label": "Total"},
        ]},
    ]}


def test_block_types_include_the_building_blocks():
    for t in ("section", "text", "field", "table", "total", "checklist", "image", "model3d", "cut_sheets",
              "button", "settings"):
        assert t in BLOCK_TYPES


def test_a_costs_view_validates_and_renders_data(session):
    v = validate_view(costs_view())
    assert v["id"] == "costs"
    out = session.views.save(costs_view(), scope="all", set_by="SM")
    assert out["id"] == "costs"
    data = session.views.render("costs")
    assert data["view"]["title"] == "Costs"
    assert "quote.lines" in data["data"]
    assert "quote.total" in data["data"]


def test_a_bad_binding_is_rejected_with_the_reason(session):
    bad = costs_view()
    bad["blocks"][0]["blocks"][0]["bind"] = "quote.nonexistent"
    with pytest.raises(ViewError) as e:
        session.views.save(bad, scope="all", set_by="Claude")
    assert "quote.nonexistent" in str(e.value)
    assert "blocks[0].blocks[0]" in str(e.value)


def test_an_unknown_block_type_is_rejected(session):
    bad = costs_view()
    bad["blocks"].append({"type": "iframe", "src": "https://example.com"})
    with pytest.raises(ViewError) as e:
        session.views.save(bad, scope="all", set_by="Claude")
    assert "iframe" in str(e.value)


def test_a_rejected_view_leaves_the_current_one_unchanged(session):
    session.views.save(costs_view(), scope="all", set_by="SM")
    path = os.path.join(views_dir(session.home), "costs.json")
    before = open(path, "rb").read()
    bad = costs_view()
    bad["title"] = "Broken"
    bad["blocks"][0]["blocks"][0]["bind"] = "quote.nonexistent"
    with pytest.raises(ViewError):
        session.views.save(bad, scope="all", set_by="Claude")
    assert open(path, "rb").read() == before


def test_unregistered_actions_are_rejected():
    v = costs_view()
    v["blocks"].append({"type": "button", "label": "Delete everything", "action": "rm_rf"})
    with pytest.raises(ViewError) as e:
        validate_view(v)
    assert "rm_rf" in str(e.value)
    v["blocks"][-1]["action"] = "export_cut_files"
    assert "export_cut_files" in ACTIONS
    validate_view(v)


def test_unknown_keys_are_rejected():
    v = costs_view()
    v["blocks"][0]["onclick"] = "alert(1)"
    with pytest.raises(ViewError) as e:
        validate_view(v)
    assert "onclick" in str(e.value)


def test_undo_restores_the_previous_file_exactly(session):
    session.views.save(costs_view(), scope="all", set_by="SM")
    path = os.path.join(views_dir(session.home), "costs.json")
    first = open(path, "rb").read()
    v2 = costs_view()
    v2["title"] = "Costs and totals"
    session.views.save(v2, scope="all", set_by="Claude")
    assert open(path, "rb").read() != first
    session.views.undo("costs", scope="all")
    assert open(path, "rb").read() == first


def test_undo_of_a_new_view_removes_it(session):
    session.views.save(costs_view(), scope="all", set_by="SM")
    session.views.undo("costs", scope="all")
    assert "costs" not in {v["id"] for v in session.views.listing()}


def test_the_project_view_ships_as_a_default(session):
    ids = [v["id"] for v in session.views.listing()]
    assert ids[0] == "project"
    v = session.views.get("project")
    assert v["title"] == "Project"
    binds = json.dumps(v)
    for b in ("project.name", "settings", "quote.lines", "workflow.steps"):
        assert b in binds


def test_project_view_shows_settings_with_their_layer(session):
    session.settings.set("sheet_price", 38.5, scope="project", set_by="SM")
    data = session.views.render("project")["data"]
    row = next(r for r in data["settings"] if r["key"] == "sheet_price")
    assert row["value"] == 38.5
    assert row["layer"] == "project"
    assert row["set_by"] == "SM"


def test_project_views_override_defaults(tmp_path):
    s = DesignSession(home=str(tmp_path))
    s.run_tool("create_shape", {"shape": "box"})
    out = s.save_project(name="Acme")
    v = costs_view()
    s.views.save(v, scope="project", set_by="SM")
    assert os.path.isfile(os.path.join(out["folder"], "views", "costs.json"))
    assert next(x for x in s.views.listing() if x["id"] == "costs")["source"] == "project"


def test_project_scope_needs_a_saved_project(session):
    with pytest.raises(ViewError) as e:
        session.views.save(costs_view(), scope="project", set_by="SM")
    assert "save" in str(e.value).lower()


def test_save_view_tool(session):
    r = session.run_tool("save_view", {"view": costs_view()})
    assert not r.is_error, r.text
    bad = costs_view()
    bad["blocks"][0]["blocks"][0]["bind"] = "quote.nonexistent"
    r = session.run_tool("save_view", {"view": bad})
    assert r.is_error
    assert "quote.nonexistent" in r.text


def test_views_api(client):
    r = client.get("/api/views")
    assert r.status_code == 200
    assert r.get_json()["views"][0]["id"] == "project"
    r = client.get("/api/views/project")
    assert r.status_code == 200
    assert r.get_json()["view"]["id"] == "project"
    bad = costs_view()
    bad["blocks"][0]["blocks"][0]["bind"] = "quote.nonexistent"
    r = client.post("/api/views", json={"view": bad, "scope": "all"})
    assert r.status_code == 422
    assert "quote.nonexistent" in r.get_json()["message"]
    r = client.post("/api/views", json={"view": costs_view(), "scope": "all"})
    assert r.status_code == 200
    r = client.post("/api/views/costs/undo", json={"scope": "all"})
    assert r.status_code == 200
    assert client.get("/api/views/nope").status_code == 404


@pytest.fixture
def client(tmp_path, monkeypatch, lifecycle):
    from cardboard_engine.server import create_app
    from tests.conftest import TOKEN

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path / "data"))
    app = create_app(shutdown_token=TOKEN, lifecycle=lifecycle)
    app.testing = True
    return app.test_client()
