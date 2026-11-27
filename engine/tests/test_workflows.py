"""Workflows: the steps the Design Assistant follows, and the values it uses.

A workflow is a named list of steps (make the model, make parts, nest on
sheets, estimate cost, ...) plus saved values for tool inputs (board size,
gap, prices). The Standard workflow is built in, so nesting and costing
happen after every design without being asked. With "apply defaults" on,
missing inputs are filled from the workflow; with it off, Claude has to ask
the designer first. Claude can save new workflows from a conversation.
Workflows are plain JSON files in ~/Documents/Fold Theory/Workflows/.
"""

import json
import os

import pytest

from cardboard_engine.design import TOOLS, DesignSession
from cardboard_engine.server import create_app
from cardboard_engine.workflow import STANDARD_NAME, WORKFLOW_SCHEMA, workflows_dir
from tests.conftest import TOKEN


@pytest.fixture
def session(tmp_path):
    return DesignSession(home=str(tmp_path))


@pytest.fixture
def client(tmp_path, monkeypatch, lifecycle):
    """An engine whose home folder is a temporary one, so no real workflows are touched."""
    monkeypatch.setenv("HOME", str(tmp_path))
    app = create_app(shutdown_token=TOKEN, lifecycle=lifecycle)
    app.testing = True
    return app.test_client()


def run(session, tool_name, **args):
    r = session.run_tool(tool_name, args)
    assert not r.is_error, r.text
    return r.text


def make_parts(session):
    run(session, "create_shape", shape="box", width_mm=200, depth_mm=150, height_mm=100)
    return run(session, "unfold")


def save_board_workflow(session, apply=True, **extra):
    args = dict(
        name="Big board",
        description="Honeycomb jobs on 2400 x 1200 sheets",
        steps=[
            {"title": "Model", "tools": ["create_shape", "load_model_file", "scale_model"],
             "instructions": "Make the model at its real size."},
            {"title": "Parts", "tools": ["waffle_slice", "unfold"], "instructions": "Waffle for big forms."},
            {"title": "Nest", "tools": ["nest_sheets"], "instructions": "Nest on the big sheets."},
            {"title": "Quote", "tools": ["estimate_cost"], "instructions": "Cost it."},
            {"title": "Files", "tools": ["export_cut_files"], "instructions": "Only when asked.", "optional": True},
        ],
        # Prices are settings now (#28), so a workflow saved by Claude holds no prices.
        defaults={"nest_sheets": {"sheet_width_mm": 2400, "sheet_height_mm": 1200, "gap_mm": 8}},
        questions=["Who is the client?"],
    )
    args.update(extra)
    text = run(session, "save_workflow", **args)
    if not apply:
        session.workflows.set_settings(apply_defaults=False)
    return text


# ---- the built-in workflow -----------------------------------------------


def test_standard_workflow_is_active_with_board_size_and_nesting_by_default(session):
    info = session.workflows.listing()
    assert info["active"] == STANDARD_NAME
    assert info["apply_defaults"] is True
    std = session.workflows.active()
    tools_in_steps = [t for s in std["steps"] for t in s["tools"]]
    assert tools_in_steps.index("nest_sheets") < tools_in_steps.index("estimate_cost")
    assert std["defaults"]["nest_sheets"]["sheet_width_mm"] == 1200
    assert std["defaults"]["nest_sheets"]["sheet_height_mm"] == 800
    # Prices are not known until the designer gives them: Claude must ask (request_inputs).
    assert "sheet_price" not in std["settings"]
    assert any(w["name"] == STANDARD_NAME and w["builtin"] for w in info["workflows"])


def test_workflow_tools_are_offered_to_claude():
    names = {t["name"] for t in TOOLS}
    assert {"list_workflows", "save_workflow", "use_workflow", "set_workflow_defaults"} <= names


def test_system_prompt_carries_the_active_workflow(session):
    prompt = session.system_prompt()
    assert "Fold Theory" in prompt
    assert f"Workflow: {STANDARD_NAME}" in prompt
    assert "nest_sheets" in prompt and "1200" in prompt
    assert "apply defaults is on" in prompt.lower()
    session.workflows.set_settings(apply_defaults=False)
    prompt = session.system_prompt()
    assert "apply defaults is off" in prompt.lower()
    assert "ask" in prompt.lower()


# ---- filling inputs from the workflow ------------------------------------


def test_missing_inputs_come_from_the_workflow_and_are_reported(session):
    save_board_workflow(session)
    make_parts(session)
    text = run(session, "nest_sheets")
    assert "2400 x 1200" in text
    assert "workflow" in text.lower() and "gap_mm 8" in text
    assert session.nested["sheet_width"] == 2400
    # The recipe keeps the values actually used, so a saved project reopens the same.
    assert session.recipe["nest"]["sheet_width_mm"] == 2400


def test_given_inputs_beat_the_workflow(session):
    save_board_workflow(session)
    make_parts(session)
    text = run(session, "nest_sheets", sheet_width_mm=1000, sheet_height_mm=700)
    assert "1000 x 700" in text
    assert session.nested["sheet_width"] == 1000


def test_without_defaults_claude_must_ask_before_nesting(session):
    save_board_workflow(session, apply=False)
    make_parts(session)
    r = session.run_tool("nest_sheets", {})
    assert r.is_error
    low = r.text.lower()
    assert "ask the designer" in low
    assert "sheet_width_mm" in r.text and "2400" in r.text  # the saved value is offered as a suggestion
    assert session.nested is None
    text = run(session, "nest_sheets", sheet_width_mm=1200, sheet_height_mm=800, gap_mm=5)
    assert "1200 x 800" in text


def test_a_value_marked_ask_is_asked_for_even_with_defaults_on(session):
    save_board_workflow(session)
    make_parts(session)
    run(session, "nest_sheets")
    r = session.run_tool("estimate_cost", {})
    assert r.is_error
    assert "machine_rate" in r.text
    assert "ask the designer" in r.text.lower() and "request_inputs" in r.text


def test_after_each_step_claude_is_told_the_next_one(session):
    run(session, "create_shape", shape="box", width_mm=200, depth_mm=150, height_mm=100)
    text = run(session, "unfold")
    assert "next step" in text.lower() and "nest_sheets" in text
    text = run(session, "nest_sheets")
    assert "next step" in text.lower() and "estimate_cost" in text


def test_steps_outside_the_workflow_get_no_hint(session):
    run(session, "create_shape", shape="box", width_mm=200, depth_mm=150, height_mm=100)
    assert "next step" not in run(session, "model_info").lower()


# ---- saving workflows from a conversation --------------------------------


def test_save_workflow_writes_a_json_file_and_makes_it_active(session, tmp_path):
    text = save_board_workflow(session)
    assert "Big board" in text
    path = os.path.join(workflows_dir(str(tmp_path)), "Big board.json")
    assert workflows_dir(str(tmp_path)) == os.path.join(str(tmp_path), "Documents", "Fold Theory", "Workflows")
    with open(path, encoding="utf-8") as fh:
        saved = json.load(fh)
    assert saved["schema"] == WORKFLOW_SCHEMA
    assert saved["name"] == "Big board"
    assert saved["questions"] == ["Who is the client?"]
    assert session.workflows.listing()["active"] == "Big board"
    assert "Who is the client?" in session.system_prompt()


def test_saved_workflows_and_settings_survive_a_restart(session, tmp_path):
    save_board_workflow(session, apply=False)
    again = DesignSession(home=str(tmp_path))
    info = again.workflows.listing()
    assert info["active"] == "Big board"
    assert info["apply_defaults"] is False
    assert {w["name"] for w in info["workflows"]} >= {STANDARD_NAME, "Big board"}


@pytest.mark.parametrize("bad, message", [
    ({"steps": [{"title": "X", "tools": ["make_coffee"]}]}, "make_coffee"),
    ({"steps": []}, "at least one step"),
    ({"defaults": {"nest_sheets": {"sheet_colour": 3}}}, "sheet_colour"),
    ({"defaults": {"nest_sheets": {"sheet_width_mm": "big"}}}, "sheet_width_mm"),
    ({"defaults": {"bake": {"x": 1}}}, "bake"),
])
def test_bad_workflows_are_refused_with_a_reason(session, bad, message):
    args = dict(name="Bad", steps=[{"title": "Nest", "tools": ["nest_sheets"]}])
    args.update(bad)
    r = session.run_tool("save_workflow", args)
    assert r.is_error
    assert message in r.text


def test_workflow_names_cannot_escape_the_folder(session, tmp_path):
    run(session, "save_workflow", name="../../evil", steps=[{"title": "Nest", "tools": ["nest_sheets"]}])
    folder = workflows_dir(str(tmp_path))
    assert not os.path.exists(os.path.join(str(tmp_path), "Documents", "evil.json"))
    assert len([f for f in os.listdir(folder) if f.endswith(".json") and not f.startswith(".")]) == 1


def test_use_workflow_switches_and_unknown_names_are_errors(session):
    save_board_workflow(session)
    run(session, "use_workflow", name=STANDARD_NAME)
    assert session.workflows.listing()["active"] == STANDARD_NAME
    r = session.run_tool("use_workflow", {"name": "Nope"})
    assert r.is_error and "Big board" in r.text


def test_set_workflow_defaults_remembers_prices(session, tmp_path):
    # Prices are settings the designer types in a form (#28): Claude cannot save them in a workflow.
    r = session.run_tool("set_workflow_defaults",
                         {"defaults": {"estimate_cost": {"sheet_price": 12.5, "machine_rate": 60}}})
    assert r.is_error and "request_inputs" in r.text
    text = run(session, "set_workflow_defaults", defaults={"nest_sheets": {"gap_mm": 6}})
    assert STANDARD_NAME in text
    std = session.workflows.active()
    assert std["defaults"]["nest_sheets"]["gap_mm"] == 6
    # The board size from the built-in workflow is kept.
    assert std["defaults"]["nest_sheets"]["sheet_width_mm"] == 1200
    assert os.path.isfile(os.path.join(workflows_dir(str(tmp_path)), f"{STANDARD_NAME}.json"))


def test_a_broken_workflow_file_is_skipped(session, tmp_path):
    folder = workflows_dir(str(tmp_path))
    os.makedirs(folder)
    with open(os.path.join(folder, "Broken.json"), "w") as fh:
        fh.write("{not json")
    names = {w["name"] for w in session.workflows.listing()["workflows"]}
    assert STANDARD_NAME in names and "Broken" not in names


def test_workflow_tools_do_not_unsave_a_project(session):
    run(session, "create_shape", shape="box", width_mm=100, depth_mm=100, height_mm=100)
    run(session, "save_project", name="Stool")
    assert session._is_saved()
    run(session, "list_workflows")
    run(session, "set_workflow_defaults", defaults={"nest_sheets": {"gap_mm": 6}})
    assert session._is_saved()


def test_project_records_the_workflow_it_was_made_with(session, tmp_path):
    save_board_workflow(session)
    make_parts(session)
    out = session.save_project(name="Stool")
    with open(os.path.join(out["folder"], "project.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    assert manifest["workflow"] == {"name": "Big board", "apply_defaults": True}


def test_reopening_a_project_ignores_workflow_settings(session):
    make_parts(session)
    run(session, "nest_sheets", sheet_width_mm=1000, sheet_height_mm=700)
    out = session.save_project(name="Box")
    session.workflows.set_settings(apply_defaults=False)
    opened = session.open_project(out["folder"])
    assert opened["name"] == "Box"
    assert session.nested["sheet_width"] == 1000


# ---- HTTP routes for the app ---------------------------------------------


def test_workflow_routes_list_and_change_settings(client):
    body = client.get("/api/workflows").get_json()
    assert body["active"] == STANDARD_NAME
    assert body["apply_defaults"] is True
    assert body["folder"].endswith(os.path.join("Documents", "Fold Theory", "Workflows"))
    r = client.post("/api/workflows/settings", json={"apply_defaults": False})
    assert r.status_code == 200
    assert r.get_json()["apply_defaults"] is False
    assert "apply defaults is off" in client.get("/api/design/tools").get_json()["system"].lower()
    r = client.post("/api/workflows/settings", json={"active": "Nope"})
    assert r.status_code == 422
    r = client.post("/api/workflows/settings", json={"apply_defaults": "yes"})
    assert r.status_code == 400


def test_tool_route_says_when_claude_needs_a_fresh_setup(client):
    r = client.post("/api/design/tool", json={"name": "use_workflow", "input": {"name": STANDARD_NAME}})
    assert r.get_json()["setup_changed"] is True
    r = client.post("/api/design/tool", json={"name": "model_info", "input": {}})
    assert r.get_json()["setup_changed"] is False
