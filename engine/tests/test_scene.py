"""Scene (#43): several named objects in one design, nested together.

A stand is a counter plus stools plus a back wall. Each object has its own
model, position and construction method, and its parts carry the object's
prefix (C-X1, S1-X1). Nesting, cost and export work on the whole scene.
A design with no objects added works exactly as before (one model, no prefix).
"""

import json
import os

import pytest

from cardboard_engine.design import TOOLS, DesignSession
from cardboard_engine.project import MANIFEST


@pytest.fixture
def home(tmp_path):
    return tmp_path


@pytest.fixture
def session(home):
    s = DesignSession(home=str(home))
    s.settings.set("sheet_price", 12.5, scope="project", set_by="test")
    s.settings.set("machine_rate", 60, scope="project", set_by="test")
    return s


def run(session, tool_name, **args):
    r = session.run_tool(tool_name, args)
    assert not r.is_error, r.text
    return r.text


def fails(session, tool_name, **args):
    r = session.run_tool(tool_name, args)
    assert r.is_error, r.text
    return r.text


def stand(session):
    """The issue's example: a counter and 4 cylinder stools."""
    run(session, "create_shape", object="counter", shape="box", width_mm=1800, depth_mm=600, height_mm=1100)
    run(session, "waffle_slice", object="counter", x_ribs=4, y_ribs=2, thickness_mm=6)
    run(session, "create_shape", object="stool 1", shape="cylinder", width_mm=350, depth_mm=350, height_mm=450,
        segments=24)
    run(session, "waffle_slice", object="stool 1", x_ribs=2, y_ribs=2, thickness_mm=6)
    for n, x in ((2, 500), (3, 1000), (4, 1500)):
        run(session, "copy_object", name="stool 1", new_name=f"stool {n}", x_mm=x, y_mm=-800)


def sheet_ids(session):
    return [p.id for p in session._need_parts().parts]


# ---- tools ---------------------------------------------------------------


def test_scene_tools_are_offered():
    names = {t["name"] for t in TOOLS}
    assert {"add_object", "select_object", "remove_object", "move_object", "copy_object", "list_objects"} <= names
    by_name = {t["name"]: t for t in TOOLS}
    for tool in ("create_shape", "load_model_file", "scale_model", "model_info", "waffle_slice",
                 "contour_stack", "unfold", "clear_rhino_override"):
        assert by_name[tool]["input_schema"]["properties"]["object"]["type"] == "string", tool


def test_one_model_without_objects_works_as_before(session):
    run(session, "create_shape", shape="box")
    run(session, "waffle_slice", x_ribs=2, y_ribs=2)
    assert all("-" not in pid for pid in sheet_ids(session))
    assert session.state()["objects"] == []


# ---- objects and labels ----------------------------------------------------


def test_the_stand_has_five_objects_with_unique_prefixes(session):
    stand(session)
    objs = session.state()["objects"]
    assert [o["name"] for o in objs] == ["counter", "stool 1", "stool 2", "stool 3", "stool 4"]
    assert [o["prefix"] for o in objs] == ["C", "S1", "S2", "S3", "S4"]
    assert [o["method"] for o in objs] == ["waffle"] * 5
    assert objs[3]["position"] == [1000.0, -800.0, 0.0]
    assert sum(o["active"] for o in objs) == 1


def test_nesting_the_whole_scene_gives_unique_labels(session):
    stand(session)
    text = run(session, "nest_sheets", sheet_width_mm=2500, sheet_height_mm=1250)
    ids = sheet_ids(session)
    assert len(ids) == len(set(ids))
    assert "C-X1" in ids and "S1-X1" in ids and "S4-Y2" in ids
    counter = [i for i in ids if i.startswith("C-")]
    stools = [i for i in ids if i.startswith("S")]
    assert len(counter) == 6 and len(stools) == 16
    labels = [p.label for p in session._need_parts().parts]
    assert all(l.split("-")[0] in ("C", "S1", "S2", "S3", "S4") for l in labels)
    assert "22 parts" in text
    assert session.state()["nested"] is True
    assert len(session.state()["sheets"]) >= 1


def test_cost_and_export_cover_the_whole_scene(session, home):
    stand(session)
    run(session, "nest_sheets", sheet_width_mm=2500, sheet_height_mm=1250)
    run(session, "estimate_cost")
    whole = session.last_estimate["cut_length_mm"]
    run(session, "remove_object", name="stool 4")
    # The nest and the estimate are worked out again without stool 4's parts.
    assert not any(i.startswith("S4-") for i in sheet_ids(session))
    assert len(sheet_ids(session)) == 18
    assert session.nested is not None
    assert session.last_estimate["cut_length_mm"] < whole
    out = run(session, "export_cut_files", format="svg", name="stand", folder=str(home / "cut"))
    svg = "".join(open(os.path.join(home / "cut", f)).read() for f in os.listdir(home / "cut"))
    assert "C-X1" in svg and "S3-Y1" in svg and "S4-" not in svg
    assert "Wrote" in out


def test_removing_an_object_keeps_the_others(session):
    stand(session)
    run(session, "remove_object", name="stool 2")
    names = [o["name"] for o in session.state()["objects"]]
    assert names == ["counter", "stool 1", "stool 3", "stool 4"]
    assert "S2-" not in " ".join(sheet_ids(session))
    assert "unknown" in fails(session, "remove_object", name="stool 9").lower()


def test_tools_act_on_the_named_object_only(session):
    stand(session)
    run(session, "waffle_slice", object="counter", x_ribs=6, y_ribs=2, thickness_mm=6)
    ids = sheet_ids(session)
    assert len([i for i in ids if i.startswith("C-")]) == 8
    assert len([i for i in ids if i.startswith("S1-")]) == 4
    assert session.state()["active_object"] == "counter"


def test_select_object_then_tools_without_a_name(session):
    stand(session)
    run(session, "select_object", name="stool 3")
    run(session, "scale_model", height_mm=500)
    objs = {o["name"]: o for o in session.state()["objects"]}
    assert objs["stool 3"]["size_mm"][2] == 500.0
    assert objs["stool 2"]["size_mm"][2] == 450.0
    assert objs["stool 3"]["method"] is None, "scaling clears that object's parts only"
    assert objs["stool 2"]["method"] == "waffle"


def test_add_object_wraps_the_existing_design_first(session):
    run(session, "create_shape", shape="cylinder", height_mm=450)
    run(session, "waffle_slice", x_ribs=2, y_ribs=2)
    text = run(session, "add_object", name="back wall", prefix="W")
    objs = session.state()["objects"]
    assert [o["name"] for o in objs] == ["cylinder", "back wall"]
    assert objs[0]["method"] == "waffle"
    assert objs[1]["active"] and objs[1]["method"] is None
    assert "no model yet" in text.lower()


def test_names_and_prefixes_are_checked(session):
    run(session, "add_object", name="counter")
    assert "already" in fails(session, "add_object", name="Counter")
    assert "already" in fails(session, "add_object", name="till", prefix="C")
    assert "prefix" in fails(session, "add_object", name="x", prefix="a b").lower()
    assert "unknown" in fails(session, "select_object", name="nope").lower()
    assert "unknown" in fails(session, "waffle_slice", object="nope").lower()


def test_move_object_moves_it_in_the_preview_only(session):
    stand(session)
    before = sheet_ids(session)
    run(session, "move_object", name="counter", x_mm=0, y_mm=500, z_mm=0)
    assert sheet_ids(session) == before
    objs = {o["name"]: o for o in session.state()["objects"]}
    assert objs["counter"]["position"] == [0.0, 500.0, 0.0]
    counter_mesh = next(o for o in session.state()["scene"]["objects"] if o["id"] == "C:model")
    ys = counter_mesh["vertices"][1::3]
    assert min(ys) == pytest.approx(200.0) and max(ys) == pytest.approx(800.0)


def test_preview_shows_every_object(session):
    stand(session)
    ids = {o["id"] for o in session.state()["scene"]["objects"]}
    assert {"C:model", "S1:model", "S4:model"} <= ids
    assert "S2-X1" in ids


def test_list_objects_and_model_info(session):
    stand(session)
    text = run(session, "list_objects")
    assert "counter (C)" in text and "stool 4 (S4)" in text
    info = run(session, "model_info", object="stool 1")
    assert "cylinder" in info


def test_reset_clears_the_scene(session):
    stand(session)
    session.reset()
    assert session.state()["objects"] == []
    assert session.state()["model"] is None


# ---- save and open ---------------------------------------------------------


def test_save_and_reopen_restores_all_five_objects(session, home):
    stand(session)
    run(session, "move_object", name="counter", x_mm=0, y_mm=400)
    run(session, "nest_sheets", sheet_width_mm=2500, sheet_height_mm=1250)
    run(session, "estimate_cost")
    before_ids = sheet_ids(session)
    before_cost = session.last_estimate
    out = session.save_project(name="Acme Stand")
    folder = out["folder"]
    m = json.loads(open(os.path.join(folder, MANIFEST)).read())
    assert [o["name"] for o in m["scene"]["objects"]] == ["counter", "stool 1", "stool 2", "stool 3", "stool 4"]
    assert m["summary"]["part_count"] == 22
    for prefix in ("C", "S1", "S4"):
        assert os.path.isfile(os.path.join(folder, "model", f"{prefix}.obj"))

    fresh = DesignSession(home=str(home))
    fresh.open_project(folder)
    objs = fresh.state()["objects"]
    assert [o["name"] for o in objs] == ["counter", "stool 1", "stool 2", "stool 3", "stool 4"]
    assert objs[0]["position"] == [0.0, 400.0, 0.0]
    assert sheet_ids(fresh) == before_ids
    assert fresh.last_estimate == before_cost
    assert fresh.state()["project"]["unsaved_changes"] is False


def test_an_object_without_a_model_survives_save_and_open(session, home):
    stand(session)
    run(session, "add_object", name="back wall", prefix="W")
    folder = session.save_project(name="Stand")["folder"]
    fresh = DesignSession(home=str(home))
    fresh.open_project(folder)
    assert [o["name"] for o in fresh.state()["objects"]][-1] == "back wall"
