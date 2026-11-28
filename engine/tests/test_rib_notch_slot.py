"""Rib notches and slot joints (#70, part 4), built in as part operations (#71).

A rib notch is a flap with a notch that drops over a rib end, so the waffle
holds the skin. A slot joint is a slot of board thickness plus clearance cut
in from an edge, for two panels that cross. Both are sized from the board, and
an applied edge operation counts as a joint for the quote
(joints.<operation>.count).
"""

import pytest

from cardboard_engine import operations as ops
from cardboard_engine.design import DesignSession

T, CLEAR, KERF = 3.0, 0.2, 0.1


@pytest.fixture
def session(tmp_path):
    return DesignSession(home=str(tmp_path))


def run(session, tool_name, **args):
    r = session.run_tool(tool_name, args)
    assert not r.is_error, r.text
    return r.text


def area(loops):
    def a2(p):
        return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(p, p[1:] + p[:1])) / 2
    loops = sorted(loops, key=lambda p: -abs(a2(p)))
    return abs(a2(loops[0])) - sum(abs(a2(p)) for p in loops[1:])


def plain_box(session, thickness=T):
    run(session, "create_shape", shape="box", width_mm=100, depth_mm=100, height_mm=100)
    run(session, "unfold", tab_width_mm=0, thickness_mm=thickness, clearance_mm=CLEAR, kerf_mm=KERF)


def builtin(key):
    return next(o for o in ops.BUILTIN_OPERATIONS if o["key"] == key)


def test_rib_notch_and_slot_joint_are_built_in():
    for key in ("rib_notch", "slot_joint"):
        op = builtin(key)
        assert op["target"] == "edge"
        assert ops.validate_operation(op)["version"] == op["version"]


def test_a_rib_notch_flap_is_sized_from_the_board(session):
    plain_box(session)
    total = sum(area(p.cut) for p in session.parts.parts)
    run(session, "apply_operation", operation="rib_notch", edges=[1])
    op = builtin("rib_notch")
    # Flap 4t deep and 10t wide, less a notch t + c - k wide and 2t deep (as drawn).
    flap, width, depth = 4 * T, 10 * T, 2 * T
    added = flap * width - (T + CLEAR - KERF) * depth
    assert sum(area(p.cut) for p in session.parts.parts) == pytest.approx(total + added, abs=1e-3)
    assert sum(len(p.score) for p in session.parts.parts) == 5 + 1, "the flap folds on a score"
    assert "rib notch" in op["label"].lower()


def test_a_slot_joint_is_board_wide_and_resizes(session):
    plain_box(session)
    total = sum(area(p.cut) for p in session.parts.parts)
    run(session, "apply_operation", operation="slot_joint", edges=[2], inputs={"depth": 30})
    assert sum(area(p.cut) for p in session.parts.parts) == pytest.approx(total - (T + CLEAR - KERF) * 30, abs=1e-3)
    run(session, "unfold", tab_width_mm=0, thickness_mm=6, clearance_mm=CLEAR, kerf_mm=KERF)
    assert sum(area(p.cut) for p in session.parts.parts) == pytest.approx(total - (6 + CLEAR - KERF) * 30, abs=1e-3)


def test_a_slot_joint_deeper_than_the_panel_is_refused(session):
    plain_box(session)
    r = session.run_tool("apply_operation", {"operation": "slot_joint", "edges": [2], "inputs": {"depth": 500}})
    assert r.is_error and "depth" in r.text and "max" in r.text


def test_applied_edge_operations_count_as_joints(session):
    plain_box(session)
    run(session, "apply_operation", operation="rib_notch", edges=[1, 2])
    run(session, "apply_operation", operation="lock_tab", edges=[3])
    run(session, "apply_operation", operation="hand_hole", part="N1")
    units = session.field_units()
    assert units["joints.rib_notch.count"] == "joint" and units["joints.slot_joint.count"] == "joint"
    m = session._measure(session._need_parts())
    assert m["joints.rib_notch.count"] == 2
    assert m["joints.lock_tab.count"] == 1
    assert m["joints.count"] == 3, "a hand hole is not a joint"
    assert "hand_hole" not in session.joint_counts
