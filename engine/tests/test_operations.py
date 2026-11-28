"""Claude adds geometry tools (#71): part operations defined in JSON.

A part operation (foldtheory.operation/1) is data built only from the
engine's tested primitives: profiles (rectangle, slot, circle, polyline, arc,
barb, tooth), placed along an edge, at a point of a part or on the matching
edge of the neighbouring part, and added to the outline, cut out of it, scored
or etched. Its parameters are expressions over the board (thickness,
clearance, kerf) and the edge, with min and max. Every operation carries a
worked example that the engine checks before saving, and every result is
checked (closed outlines, slots no narrower than the board). Applied
operations go into the recipe, so a saved project rebuilds the same parts,
and Rhino hand edits still win.
"""

import copy
import json
import math
import os
import shutil

import pytest

from cardboard_engine import operations as ops
from cardboard_engine.components.export import to_dxf
from cardboard_engine.design import DesignSession
from cardboard_engine.rhino_sync import Override, shape_of


def lock_op(**changes):
    """A Claude-defined lock tab for a skin: a flap, a barbed head, a score and a slit in the next panel."""
    op = {
        "key": "skin_lock",
        "label": "Skin lock tab",
        "description": "Barbed lock tab with a slit in the neighbouring panel",
        "target": "edge",
        "params": {
            "flap": {"unit": "mm", "default": "3 * thickness", "min": "2 * thickness"},
            "neck": {"unit": "mm", "default": "4 * thickness", "min": "2 * thickness", "max": "edge_length / 2"},
            "barb": {"unit": "mm", "default": "thickness / 2", "min": "0.5"},
        },
        "steps": [
            {"combine": "add", "profile": {"shape": "rectangle", "width": "neck + 2 * barb + 2 * thickness",
                                           "height": "flap"}},
            {"combine": "add", "profile": {"shape": "barb", "neck": "neck + kerf",
                                           "neck_length": "thickness + clearance",
                                           "head": "neck + 2 * barb + kerf", "head_length": "thickness"},
             "place": {"offset": "-flap"}},
            {"combine": "score", "profile": {"shape": "polyline", "points": [
                ["-(neck + 2 * barb + 2 * thickness) / 2", 0], ["(neck + 2 * barb + 2 * thickness) / 2", 0]]}},
            {"combine": "cut", "on": "mate",
             "profile": {"shape": "slot", "length": "neck + clearance - kerf",
                         "width": "thickness + clearance - kerf"},
             "place": {"side": "in", "offset": "flap + thickness"}},
        ],
        # On a 200 x 100 test part, 3 mm board: flap 9 x 21 (189), barb 12 x 3.2 plus a 15 -> 12 head
        # 3 deep (78.9); the slit is 12.2 x 3.2 (39.04).
        "example": {"part": {"width": 200, "height": 100},
                    "board": {"thickness": 3, "clearance": 0.2, "kerf": 0},
                    "expect": {"area_mm2": 20267.9, "cut_loops": 1, "mate_area_mm2": 19960.96,
                               "mate_cut_loops": 2}},
    }
    op.update(changes)
    return op


HOLE = {
    "key": "cable_hole",
    "label": "Cable hole",
    "target": "part",
    "params": {"diameter": {"unit": "mm", "default": "30", "min": "10", "max": "100"}},
    "steps": [{"combine": "cut", "profile": {"shape": "circle", "diameter": "diameter", "segments": 64}}],
    "example": {"part": {"width": 200, "height": 100},
                "expect": {"cut_loops": 2, "area_mm2": 20000 - 64 / 2 * 15 ** 2 * math.sin(2 * math.pi / 64)}},
}


@pytest.fixture
def session(tmp_path):
    return DesignSession(home=str(tmp_path))


def run(session, tool_name, **args):
    r = session.run_tool(tool_name, args)
    assert not r.is_error, r.text
    return r.text


def plain_box(session, thickness=3):
    run(session, "create_shape", shape="box", width_mm=100, depth_mm=100, height_mm=100)
    run(session, "unfold", tab_width_mm=0, thickness_mm=thickness, clearance_mm=0.2)


def area(loops):
    """Area inside the cut loops (outer loops minus holes, even-odd)."""
    def a2(p):
        return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(p, p[1:] + p[:1])) / 2
    loops = sorted(loops, key=lambda p: -abs(a2(p)))
    return abs(a2(loops[0])) - sum(abs(a2(p)) for p in loops[1:])


def cuts(session):
    return {p.id: [list(map(tuple, l)) for l in p.cut] for p in session.parts.parts}


def slot_sizes(session):
    """(short, long) side of every 4-corner hole in the parts."""
    out = []
    for p in session.parts.parts:
        for loop in p.cut[1:]:
            if len(loop) == 4:
                sides = sorted(math.dist(a, b) for a, b in zip(loop, loop[1:] + loop[:1]))
                out.append((round(sides[0], 6), round(sides[-1], 6)))
    return out


# ---- the operation document ---------------------------------------------------------------


def test_a_lock_tab_operation_checks_out_and_gets_a_version():
    op = ops.validate_operation(lock_op())
    assert op["schema"] == ops.OPERATION_SCHEMA == "foldtheory.operation/1"
    assert len(op["version"]) == 12
    assert ops.validate_operation(lock_op())["version"] == op["version"]
    changed = copy.deepcopy(lock_op())
    changed["params"]["flap"]["default"] = "4 * thickness"
    changed["example"]["expect"] = {"cut_loops": 1}
    assert ops.validate_operation(changed)["version"] != op["version"]


def test_built_in_operations_check_out():
    keys = [o["key"] for o in ops.BUILTIN_OPERATIONS]
    assert "lock_tab" in keys and "hand_hole" in keys
    for o in ops.BUILTIN_OPERATIONS:
        assert ops.validate_operation(o)["version"] == o["version"]


def test_an_example_that_does_not_come_out_is_refused_with_the_real_numbers():
    bad = lock_op(example={"part": {"width": 200, "height": 100}, "expect": {"area_mm2": 20000}})
    with pytest.raises(ops.OperationError, match=r"area_mm2.*20267\.9"):
        ops.validate_operation(bad)


def test_a_slot_narrower_than_the_board_is_refused():
    op = lock_op()
    op["steps"][3]["profile"]["width"] = "thickness - 1"
    with pytest.raises(ops.OperationError, match="narrower than the board"):
        ops.validate_operation(op)


@pytest.mark.parametrize("change, reason", [
    ({"target": "seam"}, "target"),
    ({"steps": []}, "step"),
    ({"steps": [{"combine": "add", "profile": {"shape": "spline"}}]}, "shape"),
    ({"steps": [{"combine": "melt", "profile": {"shape": "circle", "diameter": "5"}}]}, "combine"),
    ({"params": {"flap": {"unit": "mm", "default": "import os"}}}, "flap"),
    ({"params": {"flap": {"unit": "mm", "default": "depth * 2"}}}, "depth"),
    ({"params": {"flap": {"unit": "parsec", "default": "3"}}}, "unit"),
    ({"example": None}, "worked example"),
])
def test_bad_documents_are_refused_with_a_reason(change, reason):
    with pytest.raises(ops.OperationError, match=reason):
        ops.validate_operation(lock_op(**change))


def test_a_part_operation_cuts_a_hole():
    op = ops.validate_operation(HOLE)
    assert op["target"] == "part"


# ---- applying operations in the design ------------------------------------------------------


def test_claude_saves_an_operation_and_it_is_listed(session):
    text = run(session, "add_operation", operation=lock_op())
    assert "skin_lock" in text and "worked example" in text
    listed = run(session, "list_operations")
    assert "skin_lock" in listed and "lock_tab" in listed and "flap" in listed


def test_lock_tabs_on_an_unfolded_box_reach_the_dxf_and_resize_with_the_board(session):
    run(session, "add_operation", operation=lock_op())
    plain_box(session)
    before = {p.id: area(p.cut) for p in session.parts.parts}
    dxf_before = sum(to_dxf(s).count("\nCUT\n") for s in session.parts.layout())
    text = run(session, "apply_operation", operation="skin_lock", all_edges=True)
    assert "7 edges" in text
    after = {p.id: area(p.cut) for p in session.parts.parts}
    assert sum(after.values()) == pytest.approx(sum(before.values()) + 7 * (189 + 78.9 - 39.04), abs=0.05)
    assert slot_sizes(session) == [(3.2, 12.2)] * 7
    assert sum(len(p.score) for p in session.parts.parts) == 5 + 7
    dxf_after = sum(to_dxf(s).count("\nCUT\n") for s in session.parts.layout())
    assert dxf_after > dxf_before, "the barbs and slits are on the cut layer"
    assert session.recipe["method"]["operations"][0]["operation"] == "skin_lock"
    # 6 mm board: the same operation, sized again.
    run(session, "unfold", tab_width_mm=0, thickness_mm=6, clearance_mm=0.2)
    assert slot_sizes(session) == [(6.2, 24.2)] * 7


def test_a_failing_application_changes_nothing(session):
    run(session, "add_operation", operation=lock_op())
    plain_box(session)
    run(session, "apply_operation", operation="skin_lock", edges=[1])
    before, recipe = cuts(session), copy.deepcopy(session.recipe)
    r = session.run_tool("apply_operation", {"operation": "skin_lock", "edges": [2], "inputs": {"neck": 2}})
    assert r.is_error and "neck" in r.text and "min" in r.text
    r = session.run_tool("apply_operation", {"operation": "skin_lock", "edges": [99]})
    assert r.is_error and "edge 99" in r.text
    r = session.run_tool("apply_operation", {"operation": "nope", "edges": [2]})
    assert r.is_error and "skin_lock" in r.text
    assert cuts(session) == before and session.recipe == recipe


def test_an_edge_that_already_has_a_joint_is_refused_with_a_hint(session):
    run(session, "add_operation", operation=lock_op())
    run(session, "create_shape", shape="box", width_mm=100, depth_mm=100, height_mm=100)
    run(session, "unfold")
    r = session.run_tool("apply_operation", {"operation": "skin_lock", "edges": [3]})
    assert r.is_error and "glue tab" in r.text and "edge_joints" in r.text


def test_a_cable_hole_on_a_part_and_undo(session):
    run(session, "add_operation", operation=HOLE)
    plain_box(session)
    before = cuts(session)
    biggest = max(session.parts.parts, key=lambda p: area(p.cut)).id
    run(session, "apply_operation", operation="cable_hole", part=biggest, inputs={"diameter": 40})
    part = next(p for p in session.parts.parts if p.id == biggest)
    assert len(part.cut) == 2
    text = run(session, "undo_operation")
    assert "cable_hole" in text
    assert cuts(session) == before
    assert "operations" not in session.recipe["method"]


def test_a_new_method_drops_the_operations(session):
    run(session, "add_operation", operation=HOLE)
    plain_box(session)
    run(session, "apply_operation", operation="cable_hole", part="N1")
    text = run(session, "waffle_slice", x_ribs=2, y_ribs=2)
    assert "cable_hole" in text
    assert "operations" not in session.recipe["method"]


def test_rhino_hand_edits_still_win(session):
    run(session, "add_operation", operation=lock_op())
    plain_box(session)
    run(session, "apply_operation", operation="skin_lock", all_edges=True)
    pid = session.parts.parts[0].id
    edited = [[(0, 0), (50, 0), (50, 50), (0, 50)]]
    session.overrides[pid] = Override(part=pid, cut=edited, score=[], etch=[],
                                      base_shape=shape_of(session.generated[pid]), summary="test")
    run(session, "unfold", tab_width_mm=0, thickness_mm=3, clearance_mm=0.2)
    part = next(p for p in session.parts.parts if p.id == pid)
    assert [list(map(tuple, l)) for l in part.cut] == [list(map(tuple, l)) for l in edited]
    assert session.overrides[pid].status == "applied"


def test_save_and_open_rebuild_the_same_parts(session, tmp_path):
    run(session, "add_operation", operation=lock_op())
    plain_box(session)
    run(session, "apply_operation", operation="skin_lock", edges=[1, 2, 3])
    want = cuts(session)
    folder = session.save_project(name="Skin box")["folder"]
    with open(os.path.join(folder, "operations.json"), encoding="utf-8") as fh:
        saved = json.load(fh)
    assert saved["schema"] == ops.OPERATIONS_SCHEMA
    assert [o["key"] for o in saved["operations"]] == ["skin_lock"]
    # Another Mac: a fresh home with no saved operations, and the project folder copied over.
    other = tmp_path / "other mac"
    copied = shutil.copytree(folder, other / "Skin box")
    fresh = DesignSession(home=str(other))
    fresh.open_project(str(copied))
    assert cuts(fresh) == want
    assert any(o["key"] == "skin_lock" for o in fresh.operations.all())


def test_operations_for_all_projects(session, tmp_path):
    run(session, "add_operation", operation=HOLE, scope="all")
    other = DesignSession(home=str(tmp_path))
    assert any(o["key"] == "cable_hole" and o["source"] == "default" for o in other.operations.all())


def test_rejected_operations_go_to_diagnostics(session):
    r = session.run_tool("add_operation", {"operation": lock_op(example={"part": {"width": 200, "height": 100},
                                                                         "expect": {"area_mm2": 1}})})
    assert r.is_error and "area_mm2" in r.text
    assert any("add_operation" in (e.get("source", "") + e.get("message", "") + str(e))
               for e in session.diagnostics.read(level="info"))


def test_the_built_in_lock_tab_on_a_scene_object(session, tmp_path):
    run(session, "create_shape", object="box a", shape="box", width_mm=100, depth_mm=100, height_mm=100)
    run(session, "unfold", object="box a", tab_width_mm=0)
    run(session, "create_shape", object="box b", shape="box", width_mm=100, depth_mm=100, height_mm=100)
    run(session, "unfold", object="box b", tab_width_mm=0)
    plain = cuts(session)
    run(session, "apply_operation", object="box a", operation="lock_tab", all_edges=True)
    run(session, "select_object", name="box b")
    assert cuts(session) == plain, "box b is untouched"
    run(session, "select_object", name="box a")
    with_tabs = cuts(session)
    assert with_tabs != plain
    folder = session.save_project(name="Two boxes")["folder"]
    fresh = DesignSession(home=str(tmp_path))
    fresh.open_project(folder)
    fresh.run_tool("select_object", {"name": "box a"})
    assert cuts(fresh) == with_tabs


def test_a_notch_on_a_waffle_rib_segment(session):
    notch = {
        "key": "cable_notch", "label": "Cable notch", "target": "edge",
        "params": {"width": {"unit": "mm", "default": "20", "min": "thickness"}},
        "steps": [{"combine": "cut", "profile": {"shape": "rectangle", "width": "width", "height": "width / 2"},
                   "place": {"side": "in"}}],
        "example": {"part": {"width": 200, "height": 100}, "expect": {"area_mm2": 20000 - 200, "cut_loops": 1}},
    }
    run(session, "add_operation", operation=notch)
    run(session, "create_shape", shape="box", width_mm=400, depth_mm=400, height_mm=450)
    run(session, "waffle_slice", x_ribs=2, y_ribs=2)
    rib = session.parts.parts[0]
    before = area(rib.cut)
    run(session, "apply_operation", operation="cable_notch", part=rib.id, segment=0)
    after = area(next(p for p in session.parts.parts if p.id == rib.id).cut)
    assert after == pytest.approx(before - 200, abs=1e-3)
    r = session.run_tool("apply_operation", {"operation": "cable_notch", "edges": [1]})
    assert r.is_error and "unfold" in r.text
