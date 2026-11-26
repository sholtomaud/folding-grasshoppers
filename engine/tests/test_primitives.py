"""Shapes the Design Assistant can create from a description (no CAD file needed)."""

import math
from collections import Counter

import pytest

from cardboard_engine.components.primitives import SHAPES, ShapeError, make_shape


def edge_counts(mesh):
    c = Counter()
    for f in mesh.faces:
        for i in range(len(f)):
            a, b = f[i], f[(i + 1) % len(f)]
            c[(min(a, b), max(a, b))] += 1
    return c


def signed_volume(mesh):
    vol = 0.0
    for a, b, c in mesh.triangles():
        vol += (
            a[0] * (b[1] * c[2] - b[2] * c[1])
            - a[1] * (b[0] * c[2] - b[2] * c[0])
            + a[2] * (b[0] * c[1] - b[1] * c[0])
        ) / 6.0
    return vol


@pytest.mark.parametrize("shape", SHAPES)
def test_every_shape_is_closed_and_outward(shape):
    m = make_shape(shape, width=200, depth=120, height=90, segments=24)
    assert set(edge_counts(m).values()) == {2}, "every edge shared by exactly two faces"
    assert signed_volume(m) > 0, "faces wind outward"


@pytest.mark.parametrize("shape", SHAPES)
def test_shapes_sit_on_the_ground_centred(shape):
    m = make_shape(shape, width=200, depth=120, height=90, segments=32)
    (lx, ly, lz), (hx, hy, hz) = m.bbox()
    assert lz == pytest.approx(0)
    assert hz == pytest.approx(90)
    assert (lx + hx) / 2 == pytest.approx(0, abs=1e-6)
    assert (ly + hy) / 2 == pytest.approx(0, abs=1e-6)
    assert hx - lx == pytest.approx(200, rel=0.02)
    assert hy - ly == pytest.approx(120, rel=0.02)


def test_box_volume_is_exact():
    assert signed_volume(make_shape("box", width=100, depth=60, height=40)) == pytest.approx(240000)


def test_cylinder_and_cone_volumes_approach_the_formula():
    cyl = make_shape("cylinder", width=100, depth=100, height=50, segments=128)
    assert signed_volume(cyl) == pytest.approx(math.pi * 50**2 * 50, rel=0.01)
    cone = make_shape("cone", width=100, depth=100, height=60, segments=128)
    assert signed_volume(cone) == pytest.approx(math.pi * 50**2 * 60 / 3, rel=0.01)


def test_sphere_volume_approaches_the_formula():
    s = make_shape("sphere", width=100, depth=100, height=100, segments=64)
    assert signed_volume(s) == pytest.approx(4 / 3 * math.pi * 50**3, rel=0.02)


def test_shape_name_is_case_insensitive():
    assert make_shape(" Box ", width=10, depth=10, height=10).faces


@pytest.mark.parametrize(
    "kwargs, message",
    [
        (dict(shape="torus"), "shape"),
        (dict(shape="box", width=0), "width"),
        (dict(shape="box", height=-5), "height"),
        (dict(shape="box", depth=float("nan")), "depth"),
        (dict(shape="cylinder", segments=2), "segments"),
        (dict(shape="cylinder", segments=1000), "segments"),
        (dict(shape="box", width="wide"), "width"),
    ],
)
def test_bad_shape_input_raises(kwargs, message):
    args = dict(width=100, depth=100, height=100)
    args.update(kwargs)
    with pytest.raises(ShapeError, match=message):
        make_shape(**args)
