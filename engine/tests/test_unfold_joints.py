"""Joint types for unfold (#70, part 1): glue tab, tuck flap, lock tab with slot.

Every joint is sized from the board (thickness, slot clearance, kerf). A lock
tab is a flap with a neck and a barbed head; a slit in the next panel takes
the head, at the neck's distance from the edge. The tab's neck is drawn kerf
wider (the cutter takes kerf / 2 off each side), the slit kerf narrower, and
the barbs stand proud of the slit by a compression allowance. Glue tabs stay
the default, so saved projects open with the same parts.
"""

import math

import pytest

from cardboard_engine.components.export import to_dxf
from cardboard_engine.components.geometry import Mesh
from cardboard_engine.components.parts import PartsDoc
from cardboard_engine.components.unfold import JOINTS, UnfoldError, UnfoldInput, unfold
from cardboard_engine.design import DesignSession
from tests import meshes

T, CLEAR, KERF = 3.0, 0.2, 0.1


def box(dx, dy, dz):
    return Mesh.from_lists(*meshes.box(dx, dy, dz))


def cube():
    return box(100, 100, 100)


def inside(pt, poly):
    x, y = pt
    hit = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            hit = not hit
    return hit


def dist_to_line(p, a, b):
    (x, y), (x1, y1), (x2, y2) = p, a, b
    return abs((x2 - x1) * (y1 - y) - (x1 - x) * (y2 - y1)) / math.hypot(x2 - x1, y2 - y1)


# ---- glue tab stays the default -------------------------------------------------


def test_joint_types_are_listed():
    assert JOINTS[:3] == ("glue_tab", "tuck_flap", "lock_tab")


def test_glue_tab_is_the_default_and_unchanged():
    before = unfold(cube(), UnfoldInput())
    again = unfold(cube(), UnfoldInput(joint="glue_tab", thickness=T, clearance=CLEAR, kerf=KERF))
    assert [p.cut for p in before.parts] == [p.cut for p in again.parts]
    assert before.tab_count == 7
    assert before.joint_counts == {"glue_tab": 7}


def test_unknown_joint_is_refused():
    with pytest.raises(UnfoldError, match="joint"):
        unfold(cube(), UnfoldInput(joint="dovetail"))


# ---- lock tabs --------------------------------------------------------------------


def test_lock_tab_sizes_come_from_the_board():
    r = unfold(cube(), UnfoldInput(joint="lock_tab", thickness=T, clearance=CLEAR, kerf=KERF))
    s = r.joint_sizes
    neck = s["neck_mm"]  # nominal neck width, as built
    assert s["neck_drawn_mm"] == pytest.approx(neck + KERF)
    assert s["slot_length_drawn_mm"] == pytest.approx(neck + CLEAR - KERF)
    assert s["slot_width_drawn_mm"] == pytest.approx(T + CLEAR - KERF)
    assert s["neck_length_mm"] == pytest.approx(T + CLEAR)
    assert s["barb_mm"] == pytest.approx(T / 2), "compression allowance each side"
    assert s["head_width_drawn_mm"] == pytest.approx(neck + CLEAR + 2 * T / 2 + KERF)
    assert r.joint_counts == {"lock_tab": 7}


def test_every_lock_tab_has_its_slot_inside_the_next_panel():
    r = unfold(cube(), UnfoldInput(joint="lock_tab", thickness=T, clearance=CLEAR, kerf=KERF))
    assert len(r.joints) == 7
    s = r.joint_sizes
    for j in r.joints:
        assert j["kind"] == "lock_tab"
        slot, face, (ea, eb) = j["slot"], j["slot_face"], j["slot_edge"]
        assert len(slot) == 4
        for corner in slot:
            assert inside(corner, face), "the slot lies inside the neighbouring panel"
        near = min(dist_to_line(c, ea, eb) for c in slot)
        far = max(dist_to_line(c, ea, eb) for c in slot)
        assert near == pytest.approx(s["slot_inset_mm"], abs=1e-6)
        assert far - near == pytest.approx(s["slot_width_drawn_mm"], abs=1e-6)
        part = next(p for p in r.parts if p.id == j["slot_part"])
        assert any(sorted(map(tuple, loop)) == sorted(map(tuple, slot)) for loop in part.cut), \
            "the slot is a cut loop of its part"


def test_lock_tab_head_is_wider_than_its_slot_and_tab_is_scored():
    r = unfold(cube(), UnfoldInput(joint="lock_tab", thickness=T, clearance=CLEAR, kerf=KERF))
    s = r.joint_sizes
    assert s["head_width_drawn_mm"] - KERF > s["slot_length_drawn_mm"] + KERF
    assert sum(len(p.score) for p in r.parts) >= 5 + 7, "folds plus a score at the base of every tab"


def test_lock_tabs_reach_the_dxf_cut_layer():
    r = unfold(cube(), UnfoldInput(joint="lock_tab", thickness=T, clearance=CLEAR, kerf=KERF))
    sheet = PartsDoc(parts=r.parts).layout()[0]
    dxf = to_dxf(sheet)
    cut_entities = dxf.count("\nCUT\n")
    assert cut_entities >= len(r.parts) + 7, "an outline per net and a slit per lock tab"


def test_short_edges_fall_back_to_glue_tabs():
    r = unfold(box(100, 100, 12), UnfoldInput(joint="lock_tab", thickness=T, clearance=CLEAR, kerf=KERF))
    assert r.joint_counts.get("lock_tab", 0) >= 1
    assert sum(r.joint_counts.values()) == 7


# ---- tuck flaps ---------------------------------------------------------------------


def test_tuck_flaps_clear_the_board_at_each_end():
    r = unfold(cube(), UnfoldInput(joint="tuck_flap", thickness=T, clearance=CLEAR, kerf=KERF))
    assert r.joint_counts == {"tuck_flap": 7}
    for j in r.joints:
        pa, pb = j["edge"]
        flap = j["flap"]
        assert math.dist(flap[0], pa) == pytest.approx(T, abs=1e-6)
        assert math.dist(flap[-1], pb) == pytest.approx(T, abs=1e-6)


# ---- in the design session -----------------------------------------------------------


@pytest.fixture
def session(tmp_path):
    return DesignSession(home=str(tmp_path))


def test_unfold_tool_takes_a_joint_and_keeps_it_in_the_recipe(session):
    assert not session.run_tool("create_shape", {"shape": "box", "width_mm": 100, "depth_mm": 100,
                                                 "height_mm": 100}).is_error
    r = session.run_tool("unfold", {"joint": "lock_tab", "thickness_mm": 3, "clearance_mm": 0.2, "kerf_mm": 0.1})
    assert not r.is_error, r.text
    assert "7 lock tabs" in r.text and "slot" in r.text
    assert "lock tab" in session.method_summary
    assert session.recipe["method"]["input"]["joint"] == "lock_tab"
    assert "lock tab" not in session.run_tool("unfold", {}).text, "a plain unfold goes back to glue tabs"
