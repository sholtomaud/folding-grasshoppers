"""Filling and extruding flat outlines for the 3D preview."""

import math

import pytest

from cardboard_engine.components.triangulate import extrude, planar_axis, triangulate


def tri_area(t):
    (ax, ay), (bx, by), (cx, cy) = t
    return abs((bx - ax) * (cy - ay) - (cx - ax) * (by - ay)) / 2


def poly_area(loop):
    s = 0.0
    for i in range(len(loop)):
        x0, y0 = loop[i]
        x1, y1 = loop[(i + 1) % len(loop)]
        s += x0 * y1 - x1 * y0
    return abs(s) / 2


SQUARE = [(0, 0), (100, 0), (100, 60), (0, 60)]
HOLE = [(20, 20), (40, 20), (40, 40), (20, 40)]
# A waffle rib: a rectangle with two slots cut down from the top.
RIB = [(0, 0), (200, 0), (200, 80), (150, 80), (150, 40), (147, 40), (147, 80),
       (53, 80), (53, 40), (50, 40), (50, 80), (0, 80)]


@pytest.mark.parametrize(
    "loops, expected",
    [
        ([SQUARE], 6000),
        ([SQUARE, HOLE], 6000 - 400),
        ([RIB], poly_area(RIB)),
        ([SQUARE + [SQUARE[0]]], 6000),  # explicitly closed loops too
    ],
)
def test_triangles_cover_the_region_exactly(loops, expected):
    tris = triangulate(loops)
    assert sum(tri_area(t) for t in tris) == pytest.approx(expected, rel=1e-9)


def test_no_triangle_lands_in_a_hole():
    for t in triangulate([SQUARE, HOLE]):
        cx = sum(p[0] for p in t) / 3
        cy = sum(p[1] for p in t) / 3
        assert not (20 < cx < 40 and 20 < cy < 40)


def test_a_circle_with_many_points_is_quick_and_exact():
    n = 2000
    circle = [(50 * math.cos(2 * math.pi * i / n), 50 * math.sin(2 * math.pi * i / n)) for i in range(n)]
    tris = triangulate([circle])
    assert sum(tri_area(t) for t in tris) == pytest.approx(poly_area(circle), rel=1e-9)


def test_degenerate_input_gives_no_triangles():
    assert triangulate([]) == []
    assert triangulate([[(0, 0), (10, 0), (20, 0)]]) == []


def test_planar_axis_finds_the_flat_coordinate():
    assert planar_axis([[(5, 0, 0), (5, 10, 0), (5, 10, 10)]]) == 0
    assert planar_axis([[(0, 7, 0), (10, 7, 0), (10, 7, 10)]]) == 1
    assert planar_axis([[(0, 0, 3), (10, 0, 3), (10, 10, 3)]]) == 2
    assert planar_axis([[(0, 0, 0), (10, 0, 1), (10, 10, 3)]]) is None


def test_extruded_slab_is_thick_and_centred_on_its_plane():
    # Square in the plane x = 5 (u = y, v = z), 3 mm thick.
    loops3d = [[(5, 0, 0), (5, 100, 0), (5, 100, 60), (5, 0, 60)]]
    tris = extrude(loops3d, thickness=3.0)
    xs = [p[0] for t in tris for p in t]
    assert min(xs) == pytest.approx(3.5)
    assert max(xs) == pytest.approx(6.5)
    # two faces of 2 triangles each, plus 4 sides of 2 triangles
    assert len(tris) == 12


def test_extrude_rejects_non_planar_outlines():
    with pytest.raises(ValueError, match="flat"):
        extrude([[(0, 0, 0), (10, 0, 1), (10, 10, 3)]], thickness=3)
