"""Unfolding polyhedral meshes into flat nets with folds, tabs and edge numbers."""

import math
from collections import Counter

import pytest

from cardboard_engine.components.geometry import Mesh
from cardboard_engine.components.region import area, regions_from_loops
from cardboard_engine.components.unfold import UnfoldError, UnfoldInput, unfold

from . import meshes

CUBE_AREA = 2 * (100 * 60 + 100 * 40 + 60 * 40)


def tetra():
    v = [(0, 0, 0), (100, 0, 0), (50, 86.6025, 0), (50, 28.8675, 81.6497)]
    return v, [(0, 2, 1), (0, 1, 3), (1, 2, 3), (2, 0, 3)]


def unwelded(vertices, faces):
    """Every face gets its own copies of its vertices (as Rhino meshes often have at creases)."""
    v, f = [], []
    for face in faces:
        f.append(tuple(range(len(v), len(v) + len(face))))
        v += [vertices[i] for i in face]
    return v, f


def net_area(part):
    return sum(area(s) for s in regions_from_loops(part.cut))


def no_tabs(**kw):
    return UnfoldInput(tab_width=0.0, **kw)


@pytest.mark.parametrize("make", [meshes.box_quads, meshes.box])
def test_box_unfolds_into_one_net_of_the_right_area(make):
    r = unfold(Mesh.from_lists(*make(100, 60, 40)), no_tabs())
    assert len(r.parts) == 1
    assert net_area(r.parts[0]) == pytest.approx(CUBE_AREA, rel=1e-6)
    assert len(regions_from_loops(r.parts[0].cut)) == 1


@pytest.mark.parametrize("make", [meshes.box_quads, meshes.box])
def test_box_has_five_fold_lines(make):
    # A cube net is 6 faces joined by 5 folds; the triangle diagonals are flat and not scored.
    r = unfold(Mesh.from_lists(*make(100, 60, 40)), no_tabs())
    assert len(r.parts[0].score) == 5
    lengths = sorted(round(math.dist(*line)) for line in r.parts[0].score)
    assert all(length in (40, 60, 100) for length in lengths)


def test_unwelded_mesh_is_welded_first():
    r = unfold(Mesh.from_lists(*unwelded(*meshes.box_quads())), no_tabs())
    assert len(r.parts) == 1
    assert net_area(r.parts[0]) == pytest.approx(CUBE_AREA, rel=1e-6)


def test_tetrahedron():
    r = unfold(Mesh.from_lists(*tetra()), no_tabs())
    assert len(r.parts) == 1
    assert len(r.parts[0].score) == 3
    face = math.sqrt(3) / 4 * 100**2
    assert net_area(r.parts[0]) == pytest.approx(4 * face, rel=1e-4)


def test_sphere_unfolds_without_overlap():
    m = Mesh.from_lists(*meshes.uv_sphere(50, rings=8, segments=12))
    total = sum(_tri_area(*t) for t in m.triangles())
    r = unfold(m, no_tabs())
    assert r.face_count == len(m.faces)
    # nets never overlap themselves: the union keeps the full surface area
    assert sum(net_area(p) for p in r.parts) == pytest.approx(total, rel=1e-4)


def _tri_area(a, b, c):
    ab = [b[i] - a[i] for i in range(3)]
    ac = [c[i] - a[i] for i in range(3)]
    cx = (ab[1] * ac[2] - ab[2] * ac[1], ab[2] * ac[0] - ab[0] * ac[2], ab[0] * ac[1] - ab[1] * ac[0])
    return math.sqrt(sum(x * x for x in cx)) / 2


def test_tabs_add_area_and_tab_folds():
    m = Mesh.from_lists(*meshes.box_quads(100, 60, 40))
    r = unfold(m, UnfoldInput(tab_width=8.0, tab_angle=45.0))
    part = r.parts[0]
    assert net_area(part) > CUBE_AREA
    # 12 edges: 5 folds, 7 cut edge pairs; each pair gets one tab with a fold at its base
    assert r.tab_count == 7
    assert len(part.score) == 5 + 7


def test_cut_edges_are_numbered_on_both_sides():
    m = Mesh.from_lists(*meshes.box_quads(100, 60, 40))
    r = unfold(m, UnfoldInput(tab_width=8.0))
    numbers = Counter(t.text for p in r.parts for t in p.texts)
    assert len(numbers) == 7
    assert set(numbers.values()) == {2}


def test_flat_angle_controls_scoring():
    m = Mesh.from_lists(*meshes.uv_sphere(50, rings=8, segments=12))
    every = unfold(m, no_tabs(flat_angle=0.0))
    few = unfold(m, no_tabs(flat_angle=179.0))
    assert sum(len(p.score) for p in few.parts) == 0
    assert sum(len(p.score) for p in every.parts) > 0


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(tab_width=-1.0),
        dict(tab_angle=0.0),
        dict(tab_angle=90.0),
        dict(flat_angle=-1.0),
        dict(flat_angle=180.0),
        dict(tab_width=float("nan")),
    ],
)
def test_invalid_inputs_raise(kwargs):
    with pytest.raises(UnfoldError):
        unfold(Mesh.from_lists(*tetra()), UnfoldInput(**kwargs))


def test_degenerate_faces_are_an_error():
    v = [(0, 0, 0), (1, 0, 0), (2, 0, 0)]
    with pytest.raises(UnfoldError):
        unfold(Mesh.from_lists(v, [(0, 1, 2)]), UnfoldInput())
