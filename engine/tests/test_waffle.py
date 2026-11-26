"""Waffle slicing: interlocking X/Y ribs with half-depth slots."""

import pytest

from cardboard_engine.components.geometry import Mesh
from cardboard_engine.components.region import area, contains, regions_from_loops
from cardboard_engine.components.waffle import WaffleError, WaffleInput, waffle_slice

from . import meshes

BOX = Mesh.from_lists(*meshes.box(100, 60, 40))


def shape(part):
    shapes = regions_from_loops(part.cut)
    assert len(shapes) == 1
    return shapes[0]


def test_box_waffle_part_count_and_names():
    r = waffle_slice(BOX, WaffleInput(nx=3, ny=2, thickness=3.0, clearance=0.0))
    assert [p.id for p in r.parts] == ["X1", "X2", "X3", "Y1", "Y2"]
    assert all(p.label == p.id for p in r.parts)


def test_rib_positions_are_evenly_spaced():
    r = waffle_slice(BOX, WaffleInput(nx=3, ny=2, thickness=3.0))
    assert r.x_positions == pytest.approx([25, 50, 75])
    assert r.y_positions == pytest.approx([20, 40])


def test_box_waffle_slot_areas():
    t = 3.0
    r = waffle_slice(BOX, WaffleInput(nx=3, ny=2, thickness=t, clearance=0.0))
    by_id = {p.id: p for p in r.parts}
    # X rib: 60 x 40 minus two slots of t x 20 (half the 40 mm height)
    assert area(shape(by_id["X2"])) == pytest.approx(60 * 40 - 2 * t * 20, rel=1e-6)
    # Y rib: 100 x 40 minus three slots
    assert area(shape(by_id["Y1"])) == pytest.approx(100 * 40 - 3 * t * 20, rel=1e-6)


def test_clearance_widens_slots():
    r = waffle_slice(BOX, WaffleInput(nx=1, ny=1, thickness=3.0, clearance=0.5))
    x1 = shape(r.parts[0])
    assert area(x1) == pytest.approx(60 * 40 - 3.5 * 20, rel=1e-6)


def test_x_slots_open_from_top_and_y_slots_from_bottom():
    r = waffle_slice(BOX, WaffleInput(nx=1, ny=1, thickness=3.0))
    x1, y1 = (shape(p) for p in r.parts)
    # Parts are in their own frame: (u, v) with v = model z.
    # X rib u is model y (slot at y = 30); Y rib u is model x (slot at x = 50).
    assert not contains(x1, (30.0, 39.0)) and contains(x1, (30.0, 1.0))
    assert contains(y1, (50.0, 39.0)) and not contains(y1, (50.0, 1.0))


def test_kerf_grows_outline():
    plain = waffle_slice(BOX, WaffleInput(nx=1, ny=1, thickness=3.0))
    kerfed = waffle_slice(BOX, WaffleInput(nx=1, ny=1, thickness=3.0, kerf=0.2))
    assert area(shape(kerfed.parts[0])) > area(shape(plain.parts[0]))


def test_ribs_3d_lie_in_their_planes():
    r = waffle_slice(BOX, WaffleInput(nx=2, ny=1, thickness=3.0))
    for loop in r.ribs3d["X1"]:
        assert all(p[0] == pytest.approx(r.x_positions[0]) for p in loop)
    for loop in r.ribs3d["Y1"]:
        assert all(p[1] == pytest.approx(r.y_positions[0]) for p in loop)


def test_sphere_waffle_has_every_rib():
    m = Mesh.from_lists(*meshes.uv_sphere(50, rings=24, segments=48))
    r = waffle_slice(m, WaffleInput(nx=4, ny=4, thickness=3.0))
    assert len(r.parts) == 8
    for p in r.parts:
        assert area(shape(p)) > 100


def test_disjoint_sections_become_separate_parts():
    # Two boxes side by side along y: each X rib cuts both, giving two pieces.
    v1, f1 = meshes.box(100, 20, 40)
    v2, f2 = meshes.box(100, 20, 40, origin=(0, 40, 0))
    m = Mesh.from_lists(v1 + v2, f1 + [tuple(i + 8 for i in f) for f in f2])
    r = waffle_slice(m, WaffleInput(nx=1, ny=0, thickness=3.0))
    assert [p.id for p in r.parts] == ["X1a", "X1b"]


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(nx=0, ny=0, thickness=3.0),
        dict(nx=-1, ny=2, thickness=3.0),
        dict(nx=2, ny=2, thickness=0.0),
        dict(nx=2, ny=2, thickness=3.0, clearance=-1),
        dict(nx=2, ny=2, thickness=3.0, kerf=-0.1),
        dict(nx=2, ny=2, thickness=float("inf")),
        dict(nx=500, ny=2, thickness=3.0),
    ],
)
def test_invalid_inputs_raise(kwargs):
    with pytest.raises(WaffleError):
        waffle_slice(BOX, WaffleInput(**kwargs))


def test_slot_wider_than_spacing_raises():
    with pytest.raises(WaffleError, match="spacing"):
        waffle_slice(BOX, WaffleInput(nx=40, ny=1, thickness=3.0))
