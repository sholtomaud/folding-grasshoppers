"""The Design Assistant's tools: what Claude can do to a design, step by step.

Claude (in the app's chat panel) calls these tools by name. Each call
changes the one design the engine holds, and the preview pane shows it.
Tool results are short text for Claude; bad inputs come back as errors
Claude can read and fix, never as exceptions.
"""

import json
import os

import pytest

from cardboard_engine.design import SYSTEM_PROMPT, TOOLS, DesignSession
from tests.meshes import box


@pytest.fixture
def session(tmp_path):
    return DesignSession(home=str(tmp_path))


def run(session, tool_name, **args):
    r = session.run_tool(tool_name, args)
    assert not r.is_error, r.text
    return r.text


# ---- tool definitions -----------------------------------------------------


def test_tool_definitions_are_valid_anthropic_tools():
    names = [t["name"] for t in TOOLS]
    assert len(names) == len(set(names))
    for t in TOOLS:
        assert set(t) == {"name", "description", "input_schema"}
        assert t["name"].replace("_", "").isalnum() and len(t["name"]) <= 64
        assert len(t["description"]) > 40, "Claude needs a real description"
        schema = t["input_schema"]
        assert schema["type"] == "object"
        assert schema.get("additionalProperties") is False
        for req in schema.get("required", []):
            assert req in schema["properties"]
    json.dumps(TOOLS)  # serialisable as-is


def test_expected_tools_are_offered():
    names = {t["name"] for t in TOOLS}
    assert {
        "create_shape", "load_model_file", "scale_model", "model_info", "waffle_slice",
        "contour_stack", "unfold", "nest_sheets", "estimate_cost", "export_cut_files",
    } <= names


def test_system_prompt_explains_the_job_and_units():
    assert "mm" in SYSTEM_PROMPT
    assert "Fold Theory" in SYSTEM_PROMPT
    assert "preview" in SYSTEM_PROMPT.lower()


# ---- errors are for Claude to read ----------------------------------------


def test_unknown_tool_is_an_error_result(session):
    r = session.run_tool("make_coffee", {})
    assert r.is_error and "make_coffee" in r.text


def test_methods_need_a_model_first(session):
    r = session.run_tool("waffle_slice", {})
    assert r.is_error and "model" in r.text.lower()


def test_nest_and_export_need_parts_first(session):
    run(session, "create_shape", shape="box", width_mm=100, depth_mm=100, height_mm=100)
    for name in ("nest_sheets", "export_cut_files", "estimate_cost"):
        r = session.run_tool(name, {})
        assert r.is_error and "parts" in r.text.lower(), name


def test_bad_argument_types_are_error_results(session):
    r = session.run_tool("create_shape", {"shape": "box", "width_mm": "big"})
    assert r.is_error and "width" in r.text
    r = session.run_tool("create_shape", {"shape": "box", "width_mm": 100, "colour": "red"})
    assert r.is_error and "colour" in r.text
    r = session.run_tool("create_shape", "not an object")
    assert r.is_error


# ---- the happy path ---------------------------------------------------------


def test_create_shape_then_waffle_then_nest_then_export(session, tmp_path):
    text = run(session, "create_shape", shape="cylinder", width_mm=300, depth_mm=200, height_mm=400)
    assert "300" in text and "400" in text
    v1 = session.version

    text = run(session, "waffle_slice", x_ribs=4, y_ribs=3, thickness_mm=6)
    assert "7 parts" in text
    assert session.version > v1
    assert session.method == "waffle"

    text = run(session, "nest_sheets", sheet_width_mm=600, sheet_height_mm=900, gap_mm=5, margin_mm=10)
    assert "sheet" in text
    assert session.nested is not None

    text = run(session, "export_cut_files", format="dxf", name="My Lamp")
    folder = tmp_path / "Documents" / "Fold Theory"
    files = sorted(os.listdir(folder))
    assert files and all(f.startswith("My-Lamp-sheet") and f.endswith(".dxf") for f in files)
    assert str(folder) in text


def test_a_new_method_replaces_the_previous_parts_and_nesting(session):
    run(session, "create_shape", shape="box", width_mm=200, depth_mm=150, height_mm=100)
    run(session, "waffle_slice", x_ribs=3, y_ribs=3, thickness_mm=3)
    run(session, "nest_sheets", sheet_width_mm=600, sheet_height_mm=400)
    run(session, "contour_stack", thickness_mm=10)
    assert session.method == "contour"
    assert session.nested is None
    assert len(session.parts.parts) == 10


def test_unfold_a_box(session):
    run(session, "create_shape", shape="box", width_mm=100, depth_mm=60, height_mm=40)
    text = run(session, "unfold", tab_width_mm=8)
    assert "net" in text.lower()
    assert session.method == "unfold"


def test_engine_errors_come_back_readable(session):
    run(session, "create_shape", shape="box", width_mm=100, depth_mm=100, height_mm=100)
    r = session.run_tool("waffle_slice", {"x_ribs": 60, "y_ribs": 2, "thickness_mm": 3})
    assert r.is_error and "rib spacing" in r.text


def test_scale_model_to_a_height(session):
    run(session, "create_shape", shape="box", width_mm=100, depth_mm=50, height_mm=20)
    run(session, "scale_model", height_mm=200)
    (lx, ly, lz), (hx, hy, hz) = session.model.bbox()
    assert hz - lz == pytest.approx(200)
    assert hx - lx == pytest.approx(1000)
    run(session, "scale_model", factor=0.5)
    assert session.model.bbox()[1][2] == pytest.approx(100)
    r = session.run_tool("scale_model", {"factor": 2, "height_mm": 5})
    assert r.is_error and "one" in r.text


def test_scaling_clears_parts_made_from_the_old_size(session):
    run(session, "create_shape", shape="box", width_mm=200, depth_mm=200, height_mm=200)
    run(session, "contour_stack", thickness_mm=20)
    run(session, "scale_model", factor=2)
    assert session.parts is None


def test_load_model_file(session, tmp_path):
    v, faces = box(100, 60, 40)
    lines = [f"v {x} {y} {z}" for x, y, z in v] + [f"f {a + 1} {b + 1} {c + 1}" for a, b, c in faces]
    (tmp_path / "chair.obj").write_text("\n".join(lines))
    text = run(session, "load_model_file", path="~/chair.obj")
    assert "chair.obj" in text and "12" in text
    assert session.model_name == "chair.obj"
    r = session.run_tool("load_model_file", {"path": "/etc/hosts.obj"})
    assert r.is_error


def test_model_info_reports_size(session):
    assert "no model" in run(session, "model_info").lower()
    run(session, "create_shape", shape="sphere", width_mm=250, depth_mm=250, height_mm=250)
    text = run(session, "model_info")
    assert "250" in text


def test_estimate_cost_measures_the_design(session):
    run(session, "create_shape", shape="box", width_mm=100, depth_mm=60, height_mm=40)
    run(session, "unfold", tab_width_mm=0)
    run(session, "nest_sheets", sheet_width_mm=600, sheet_height_mm=400)
    # Prices are settings (#28), never estimate_cost inputs.
    session.settings.set("sheet_price", 4.5, scope="project", set_by="test")
    session.settings.set("machine_rate", 60, scope="project", set_by="test")
    text = run(session, "estimate_cost")
    assert "Total" in text
    est = session.last_estimate
    # The net of a 100 x 60 x 40 box: 6 faces, 7 folds; perimeter is at least the faces' outline.
    assert est["cut_length_mm"] > 2 * (100 + 60) + 4 * 40
    assert est["score_length_mm"] > 0
    assert est["sheet_count"] == 1
    assert est["total_cost"] == pytest.approx(est["material_cost"] + est["machine_cost"])
    assert est["material_cost"] == pytest.approx(4.5)


def test_reset_starts_over(session):
    run(session, "create_shape", shape="box", width_mm=100, depth_mm=100, height_mm=100)
    v = session.version
    session.reset()
    assert session.model is None and session.parts is None
    assert session.version > v


# ---- the preview ------------------------------------------------------------


def test_state_without_a_model(session):
    s = session.state()
    assert s["model"] is None
    assert s["sheets"] == []
    assert s["scene"]["objects"] == []
    assert s["version"] == session.version


def test_state_shows_the_model_in_3d(session):
    run(session, "create_shape", shape="box", width_mm=100, depth_mm=60, height_mm=40)
    s = session.state()
    assert s["model"]["name"] == "box"
    assert s["model"]["size_mm"] == [100, 60, 40]
    (obj,) = s["scene"]["objects"]
    assert obj["kind"] == "model"
    assert len(obj["indices"]) % 3 == 0
    assert len(obj["vertices"]) % 3 == 0
    assert max(obj["indices"]) < len(obj["vertices"]) // 3


def test_state_shows_waffle_ribs_as_boards_and_flat_sheets(session):
    run(session, "create_shape", shape="box", width_mm=200, depth_mm=150, height_mm=100)
    run(session, "waffle_slice", x_ribs=2, y_ribs=2, thickness_mm=5)
    s = session.state()
    kinds = [o["kind"] for o in s["scene"]["objects"]]
    assert kinds.count("part") == 4
    assert kinds.count("model") == 1
    ribs = [o for o in s["scene"]["objects"] if o["kind"] == "part"]
    xs = ribs[0]["vertices"][0::3]
    assert max(xs) - min(xs) == pytest.approx(5, abs=1e-6), "X ribs are boards 5 mm thick"
    assert s["nested"] is False
    (sheet,) = s["sheets"]
    assert sheet["svg"].lstrip().startswith("<?xml")
    assert "<svg" in sheet["svg"]


def test_state_after_nesting_has_one_svg_per_sheet(session):
    run(session, "create_shape", shape="box", width_mm=400, depth_mm=300, height_mm=200)
    run(session, "contour_stack", thickness_mm=20)
    run(session, "nest_sheets", sheet_width_mm=600, sheet_height_mm=400, rotations=[0, 90])
    s = session.state()
    assert s["nested"] is True
    assert len(s["sheets"]) == session.nested["sheet_count"] >= 2
    assert all('width="600mm"' in sh["svg"] for sh in s["sheets"])
    assert all(0 < sh["utilisation"] <= 1 for sh in s["sheets"])


def test_state_after_unfold_shows_the_folded_model(session):
    run(session, "create_shape", shape="pyramid", width_mm=100, depth_mm=100, height_mm=80)
    run(session, "unfold")
    s = session.state()
    assert [o["kind"] for o in s["scene"]["objects"]] == ["assembly"]


def test_steps_record_what_happened(session):
    run(session, "create_shape", shape="box", width_mm=100, depth_mm=100, height_mm=100)
    session.run_tool("waffle_slice", {"x_ribs": 60})
    run(session, "contour_stack", thickness_mm=25)
    steps = session.state()["steps"]
    assert [s["tool"] for s in steps] == ["create_shape", "waffle_slice", "contour_stack"]
    assert [s["ok"] for s in steps] == [True, False, True]
