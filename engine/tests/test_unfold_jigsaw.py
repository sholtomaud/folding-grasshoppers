"""Jigsaw edges and joints chosen per edge (#70, part 2).

A jigsaw edge joins two panels without glue: dovetail teeth on one side and
matching sockets cut into the other. Sizes come from the board: the teeth
are drawn kerf / 2 bigger all round and the sockets kerf / 2 smaller, so once
cut the sockets are the clearance wider and deeper than the teeth. A joint
can be chosen for the whole unfold or for single edges by their number (the
numbers printed on both sides of every cut edge).
"""

import math

import pytest

from cardboard_engine.components.geometry import Mesh
from cardboard_engine.components.parts import PartsDoc
from cardboard_engine.components.unfold import JOINTS, UnfoldError, UnfoldInput, unfold
from cardboard_engine.components.nest import NestInput, nest
from cardboard_engine.design import DesignSession
from tests import meshes

T, CLEAR, KERF = 3.0, 0.4, 0.2


def cube():
    return Mesh.from_lists(*meshes.box(100, 100, 100))


def frame(pa, pb, p):
    """(u along pa -> pb, v outward to the right) of point p."""
    l = math.dist(pa, pb)
    ux, uy = (pb[0] - pa[0]) / l, (pb[1] - pa[1]) / l
    dx, dy = p[0] - pa[0], p[1] - pa[1]
    return dx * ux + dy * uy, dx * uy - dy * ux, l


def widths(poly_uv, v):
    """Width of a tooth or socket outline at height v (u extent of the crossing)."""
    xs = []
    for (u1, v1), (u2, v2) in zip(poly_uv, poly_uv[1:] + poly_uv[:1]):
        if (v1 - v) * (v2 - v) <= 0 and v1 != v2:
            xs.append(u1 + (u2 - u1) * (v - v1) / (v2 - v1))
    return max(xs) - min(xs)


def test_jigsaw_is_a_joint_type():
    assert "jigsaw_edge" in JOINTS


def test_jigsaw_teeth_fit_their_sockets_with_the_clearance():
    r = unfold(cube(), UnfoldInput(joint="jigsaw_edge", thickness=T, clearance=CLEAR, kerf=KERF))
    assert r.joint_counts == {"jigsaw_edge": 7}
    s = r.joint_sizes
    for j in r.joints:
        (pa, pb), (oa, ob) = j["edge"], j["socket_edge"]
        assert len(j["teeth"]) == len(j["sockets"]) >= 1
        for tooth, socket in zip(j["teeth"], j["sockets"]):
            t_uv = [frame(pa, pb, p)[:2] for p in tooth]
            l = math.dist(pa, pb)
            # The socket side runs the edge the other way, and its outward is the tooth side's inward.
            s_uv = [(l - u, -v) for u, v in (frame(oa, ob, p)[:2] for p in socket)]
            # Cut by the cutter: teeth lose kerf / 2 all round, sockets gain it.
            t_depth = max(v for _, v in t_uv) - KERF / 2
            s_depth = max(v for _, v in s_uv) + KERF / 2
            assert s_depth - t_depth == pytest.approx(CLEAR / 2, abs=1e-6)
            for v in (0.25 * s["tooth_depth_mm"], 0.75 * s["tooth_depth_mm"]):
                gap = (widths(s_uv, v) + KERF) - (widths(t_uv, v) - KERF)
                assert gap == pytest.approx(CLEAR, abs=1e-6)
            mid_t = sum(u for u, _ in t_uv) / len(t_uv)
            mid_s = sum(u for u, _ in s_uv) / len(s_uv)
            assert mid_t == pytest.approx(mid_s, abs=1e-6), "each tooth sits in the middle of its socket"
        head, neck = s["tooth_head_mm"], s["tooth_neck_mm"]
        assert head > neck, "dovetail: the head is wider than the neck, so the seam cannot pull apart"


def test_jigsaw_parts_nest_and_have_the_sockets_cut_in():
    r = unfold(cube(), UnfoldInput(joint="jigsaw_edge", thickness=T, clearance=CLEAR, kerf=KERF))
    n = nest(PartsDoc(parts=r.parts), NestInput(sheet_width=1200, sheet_height=800))
    assert n.sheet_count == 1
    plain = unfold(cube(), UnfoldInput(tab_width=0))
    assert sum(len(loop) for p in r.parts for loop in p.cut) > sum(len(loop) for p in plain.parts for loop in p.cut)


def test_joints_can_be_chosen_per_edge():
    base = unfold(cube(), UnfoldInput(joint="glue_tab"))
    assert base.edge_numbers == list(range(1, 8))
    r = unfold(cube(), UnfoldInput(joint="glue_tab", thickness=T, clearance=CLEAR, kerf=KERF,
                                   edge_joints={2: "lock_tab", 5: "jigsaw_edge", 6: "none"}))
    by_edge = {j["number"]: j["kind"] for j in r.joints}
    assert by_edge[2] == "lock_tab" and by_edge[5] == "jigsaw_edge"
    assert 6 not in by_edge, "none leaves the edge plain"
    assert r.joint_counts == {"glue_tab": 4, "lock_tab": 1, "jigsaw_edge": 1}


def test_bad_edge_choices_are_refused():
    with pytest.raises(UnfoldError, match="edge 99"):
        unfold(cube(), UnfoldInput(edge_joints={99: "lock_tab"}))
    with pytest.raises(UnfoldError, match="joint"):
        unfold(cube(), UnfoldInput(edge_joints={2: "dovetail"}))


def test_unfold_tool_takes_edge_joints(tmp_path):
    s = DesignSession(home=str(tmp_path))
    assert not s.run_tool("create_shape", {"shape": "box", "width_mm": 100, "depth_mm": 100,
                                           "height_mm": 100}).is_error
    r = s.run_tool("unfold", {"joint": "glue_tab", "edge_joints": {"2": "jigsaw_edge"}, "thickness_mm": 3})
    assert not r.is_error, r.text
    assert "1 jigsaw edge" in r.text
    assert s.recipe["method"]["input"]["edge_joints"] == {"2": "jigsaw_edge"}
    bad = s.run_tool("unfold", {"edge_joints": {"x": "lock_tab"}})
    assert bad.is_error and "edge number" in bad.text
