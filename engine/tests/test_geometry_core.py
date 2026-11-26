"""Mesh sections and 2D regions: the shared core of every geometry endpoint."""

import math

import pytest

from cardboard_engine.components.geometry import GeometryError, Mesh, section_2d
from cardboard_engine.components.region import (
    area,
    contains,
    difference,
    line_intervals,
    offset,
    regions_from_loops,
)

from . import meshes


def test_mesh_rejects_bad_input():
    with pytest.raises(GeometryError):
        Mesh.from_lists([], [])
    with pytest.raises(GeometryError):
        Mesh.from_lists([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 7)])
    with pytest.raises(GeometryError):
        Mesh.from_lists([(0, 0, float("nan")), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)])


def test_quads_are_split_into_triangles():
    m = Mesh.from_lists(*meshes.box_quads())
    assert len(m.triangles()) == 12


def test_bbox():
    m = Mesh.from_lists(*meshes.box(10, 20, 30, origin=(1, 2, 3)))
    assert m.bbox() == ((1, 2, 3), (11, 22, 33))


@pytest.mark.parametrize("make", [meshes.box, meshes.box_quads])
def test_box_section_is_one_rectangle(make):
    m = Mesh.from_lists(*make(100, 60, 40))
    loops = section_2d(m, axis=0, value=50)  # plane x = 50 -> (y, z)
    assert len(loops) == 1
    shapes = regions_from_loops(loops)
    assert len(shapes) == 1
    assert area(shapes[0]) == pytest.approx(60 * 40, rel=1e-6)


def test_section_through_a_vertex_plane_still_closes():
    # x = 0 is the box's own face; a section exactly on a vertex plane must not crash
    # and the plane through the box's vertices at x = 100 is likewise handled.
    m = Mesh.from_lists(*meshes.box(100, 60, 40))
    for x in (0.0, 100.0):
        loops = section_2d(m, axis=0, value=x)
        for loop in loops:
            assert len(loop) >= 3


def test_section_outside_is_empty():
    m = Mesh.from_lists(*meshes.box())
    assert section_2d(m, axis=2, value=500) == []


def test_sphere_section_area():
    r = 50.0
    m = Mesh.from_lists(*meshes.uv_sphere(r, rings=48, segments=96))
    shapes = regions_from_loops(section_2d(m, axis=2, value=0.0))
    assert len(shapes) == 1
    assert area(shapes[0]) == pytest.approx(math.pi * r * r, rel=0.01)


def test_torus_section_has_a_hole():
    m = Mesh.from_lists(*meshes.torus(60, 20))
    shapes = regions_from_loops(section_2d(m, axis=2, value=0.0))
    assert len(shapes) == 1
    assert len(shapes[0].holes) == 1
    assert not contains(shapes[0], (0.0, 0.0))
    assert contains(shapes[0], (60.0, 0.0))


def test_region_difference_and_offset():
    sq = regions_from_loops([[(0, 0), (10, 0), (10, 10), (0, 10)]])
    cut = difference(sq, [[(4, 5), (6, 5), (6, 20), (4, 20)]])
    assert len(cut) == 1
    assert area(cut[0]) == pytest.approx(100 - 2 * 5)
    grown = offset(sq, 1.0)
    # square grown by 1 with round corners: 12*12 - 4 + pi
    assert area(grown[0]) == pytest.approx(144 - 4 + math.pi, rel=1e-3)


def test_line_intervals():
    shapes = regions_from_loops([[(0, 0), (10, 0), (10, 10), (0, 10)], [(3, 3), (7, 3), (7, 7), (3, 7)]])
    assert line_intervals(shapes[0], 5.0) == [pytest.approx((0, 3)), pytest.approx((7, 10))]
    assert line_intervals(shapes[0], 1.0) == [pytest.approx((0, 10))]
    assert line_intervals(shapes[0], 20.0) == []
