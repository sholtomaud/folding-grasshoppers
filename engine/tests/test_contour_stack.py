"""Contour stacks: horizontal layers of board, etched with their neighbours' outlines."""

import math

import pytest

from cardboard_engine.components.contour import ContourError, ContourInput, contour_stack
from cardboard_engine.components.geometry import Mesh
from cardboard_engine.components.region import area, regions_from_loops

from . import meshes

BOX = Mesh.from_lists(*meshes.box(100, 60, 40))
SPHERE = Mesh.from_lists(*meshes.uv_sphere(50, rings=32, segments=64))


def shapes(part):
    return regions_from_loops(part.cut)


def test_layer_count_and_names():
    r = contour_stack(BOX, ContourInput(thickness=10.0))
    assert [p.id for p in r.parts] == ["L01", "L02", "L03", "L04"]
    assert r.levels == pytest.approx([5, 15, 25, 35])


def test_partial_last_band_still_gets_a_layer():
    r = contour_stack(BOX, ContourInput(thickness=15.0))
    # bands 0-15, 15-30, 30-40: each layer is the section through its band's middle
    assert r.levels == pytest.approx([7.5, 22.5, 35.0])


def test_box_layers_are_full_rectangles():
    r = contour_stack(BOX, ContourInput(thickness=10.0, etch=False))
    for p in r.parts:
        assert area(shapes(p)[0]) == pytest.approx(6000, rel=1e-6)
        assert p.etch == []


def test_sphere_layers_shrink_towards_the_poles():
    r = contour_stack(SPHERE, ContourInput(thickness=10.0))
    areas = [sum(area(s) for s in shapes(p)) for p in r.parts]
    assert len(areas) == 10
    assert max(areas) == pytest.approx(areas[4], rel=0.05) or max(areas) == pytest.approx(areas[5], rel=0.05)
    assert areas[0] < areas[4] and areas[-1] < areas[5]
    # middle layer ~ circle of radius sqrt(50^2 - 5^2)
    assert areas[4] == pytest.approx(math.pi * (50**2 - 5**2), rel=0.02)


def test_layers_are_etched_with_the_smaller_neighbour_outline():
    r = contour_stack(SPHERE, ContourInput(thickness=10.0, etch=True))
    # Each layer carries the parts of its neighbours' outlines that fall on it:
    # on a sphere that is the smaller neighbour, towards the pole.
    for p in r.parts[1:4] + r.parts[6:9]:
        assert len(p.etch) >= 1, p.id
        assert all(len(line) >= 2 for line in p.etch)
    # The pole layers' only neighbour is larger, so nothing lands on them.
    assert r.parts[0].etch == [] and r.parts[-1].etch == []


def test_etch_lines_lie_inside_the_layer():
    from cardboard_engine.components.region import contains, offset

    r = contour_stack(SPHERE, ContourInput(thickness=10.0))
    for p in r.parts:
        grown = offset(shapes(p), 0.01)  # on the boundary counts as inside
        for line in p.etch:
            for i in range(len(line) - 1):
                mid = ((line[i][0] + line[i + 1][0]) / 2, (line[i][1] + line[i + 1][1]) / 2)
                assert any(contains(s, mid) for s in grown), (p.id, mid)


def test_torus_layers_keep_their_hole():
    m = Mesh.from_lists(*meshes.torus(60, 20))
    r = contour_stack(m, ContourInput(thickness=8.0))
    middle = r.parts[len(r.parts) // 2]
    assert len(shapes(middle)) == 1 and len(shapes(middle)[0].holes) == 1


def test_axis_x():
    r = contour_stack(BOX, ContourInput(thickness=25.0, axis="x"))
    assert r.levels == pytest.approx([12.5, 37.5, 62.5, 87.5])
    assert area(shapes(r.parts[0])[0]) == pytest.approx(60 * 40, rel=1e-6)


def test_layers_3d_sit_at_their_level():
    r = contour_stack(BOX, ContourInput(thickness=10.0))
    for p, z in zip(r.parts, r.levels):
        for loop in r.layers3d[p.id]:
            assert all(pt[2] == pytest.approx(z) for pt in loop)


def test_kerf_grows_layers():
    a = contour_stack(BOX, ContourInput(thickness=10.0)).parts[0]
    b = contour_stack(BOX, ContourInput(thickness=10.0, kerf=0.4)).parts[0]
    assert area(shapes(b)[0]) > area(shapes(a)[0])


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(thickness=0.0),
        dict(thickness=-3.0),
        dict(thickness=float("nan")),
        dict(thickness=3.0, kerf=-1),
        dict(thickness=3.0, axis="w"),
        dict(thickness=0.01),  # 4000 layers
    ],
)
def test_invalid_inputs_raise(kwargs):
    with pytest.raises(ContourError):
        contour_stack(BOX, ContourInput(**kwargs))
