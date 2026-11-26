"""Multi-sheet nesting: true-shape bottom-left fill with no-fit polygons."""

import itertools

import pytest

from cardboard_engine.components.nest import NestError, NestInput, nest, parse_rotations
from cardboard_engine.components.parts import Part, PartsDoc
from cardboard_engine.components.region import area, intersection, regions_from_loops


def rect(w, h):
    return [(0, 0), (w, 0), (w, h), (0, h)]


def doc_of(*sizes, prefix="P"):
    return PartsDoc(parts=[Part(id=f"{prefix}{i + 1}", cut=[rect(w, h)]) for i, (w, h) in enumerate(sizes)])


def placed_shapes(doc):
    """Outlines of every part on each sheet, after placement."""
    out = {}
    for sheet in doc.layout():
        out[sheet.index] = [(p.id, regions_from_loops(p.cut)) for p in sheet.parts]
    return out


def assert_valid(doc, inp):
    w, h = inp.sheet_width, inp.sheet_height
    for sheet in doc.layout():
        for p in sheet.parts:
            (x0, y0), (x1, y1) = p.bbox()
            assert x0 >= inp.margin - 1e-3 and y0 >= inp.margin - 1e-3, p.id
            assert x1 <= w - inp.margin + 1e-3 and y1 <= h - inp.margin + 1e-3, p.id
    for sheet, parts in placed_shapes(doc).items():
        for (ia, a), (ib, b) in itertools.combinations(parts, 2):
            overlap = sum(area(s) for s in intersection(a, b))
            assert overlap < 1e-2, (sheet, ia, ib)  # mm²


def test_parse_rotations():
    assert parse_rotations("0, 90") == [0.0, 90.0]
    assert parse_rotations("") == [0.0]
    with pytest.raises(NestError):
        parse_rotations("0, ninety")


def test_everything_fits_on_one_sheet():
    inp = NestInput(sheet_width=300, sheet_height=200, gap=5, margin=10)
    doc = doc_of((100, 50), (100, 50), (100, 50), (80, 40))
    r = nest(doc, inp)
    assert r.sheet_count == 1
    assert all(p.placement is not None and p.placement.sheet == 0 for p in r.doc.parts)
    assert r.doc.sheet_size == (300, 200)
    assert_valid(r.doc, inp)


def test_bottom_left_first_part_goes_in_the_corner():
    inp = NestInput(sheet_width=300, sheet_height=200, gap=5, margin=10)
    r = nest(doc_of((100, 50)), inp)
    pl = r.doc.parts[0].placement
    assert (pl.x, pl.y) == (pytest.approx(10), pytest.approx(10))


def test_gap_is_respected():
    inp = NestInput(sheet_width=300, sheet_height=100, gap=7, margin=0, rotations=(0,))
    r = nest(doc_of((100, 50), (100, 50)), inp)
    a, b = (p.placement for p in r.doc.parts)
    assert abs(b.x - a.x) >= 107 - 1e-3 or abs(b.y - a.y) >= 57 - 1e-3


def test_overflow_opens_more_sheets():
    inp = NestInput(sheet_width=220, sheet_height=120, gap=5, margin=5)
    doc = doc_of(*[(100, 50)] * 9)  # four per sheet
    r = nest(doc, inp)
    assert r.sheet_count == 3
    assert sorted(len(s.parts) for s in r.doc.layout()) == [1, 4, 4]
    assert_valid(r.doc, inp)


def test_rotation_lets_a_tall_part_fit():
    inp = NestInput(sheet_width=300, sheet_height=100, gap=0, margin=0, rotations=(0, 90))
    r = nest(doc_of((60, 250)), inp)
    assert r.doc.parts[0].placement.rotation == 90
    assert_valid(r.doc, inp)


def test_part_too_big_for_the_sheet_is_an_error():
    inp = NestInput(sheet_width=300, sheet_height=100, rotations=(0,))
    with pytest.raises(NestError, match="P1"):
        nest(doc_of((60, 250)), inp)


def test_l_shapes_interlock():
    # Two L shapes that only fit side by side on this sheet if their notches interlock.
    L = [(0, 0), (100, 0), (100, 20), (20, 20), (20, 100), (0, 100)]
    L2 = [(100 - x, 100 - y) for x, y in L]  # rotated 180 about (50, 50)
    doc = PartsDoc(parts=[Part(id="A", cut=[L]), Part(id="B", cut=[L2])])
    inp = NestInput(sheet_width=125, sheet_height=125, gap=2, margin=0, rotations=(0,))
    r = nest(doc, inp)
    assert r.sheet_count == 1
    assert_valid(r.doc, inp)


def test_utilisation():
    inp = NestInput(sheet_width=200, sheet_height=100, gap=0, margin=0)
    r = nest(doc_of((100, 100), (100, 100)), inp)
    assert r.utilisation == [pytest.approx(1.0)]


def test_holes_are_kept_and_count_against_area():
    outer = rect(100, 100)
    hole = [(25, 25), (75, 25), (75, 75), (25, 75)]
    doc = PartsDoc(parts=[Part(id="F", cut=[outer, hole])])
    r = nest(doc, NestInput(sheet_width=200, sheet_height=200, gap=0, margin=0))
    assert len(r.doc.parts[0].cut) == 2
    assert r.utilisation[0] == pytest.approx((10000 - 2500) / 40000)


def test_existing_placements_are_replaced():
    doc = doc_of((50, 50))
    first = nest(doc, NestInput(sheet_width=100, sheet_height=100, margin=0)).doc
    again = nest(first, NestInput(sheet_width=100, sheet_height=100, margin=20)).doc
    assert again.parts[0].placement.x == pytest.approx(20)


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(sheet_width=0, sheet_height=100),
        dict(sheet_width=100, sheet_height=-1),
        dict(sheet_width=100, sheet_height=100, gap=-1),
        dict(sheet_width=100, sheet_height=100, margin=60),
        dict(sheet_width=float("inf"), sheet_height=100),
        dict(sheet_width=100, sheet_height=100, rotations=()),
    ],
)
def test_invalid_inputs_raise(kwargs):
    with pytest.raises(NestError):
        nest(doc_of((10, 10)), NestInput(**kwargs))


def test_empty_document_is_an_error():
    with pytest.raises(NestError):
        nest(PartsDoc(parts=[]), NestInput())


def circle(r, n=64, cx=0.0, cy=0.0):
    import math

    return [(cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n)) for k in range(n)]


def test_equal_parts_never_stack_on_each_other():
    # Regression: when the moving part is as big as a placed one, the hole in
    # their Minkowski sum is "one inside the other" and must stay forbidden.
    doc = PartsDoc(parts=[Part(id=f"C{i}", cut=[circle(100, 120)]) for i in range(3)])
    inp = NestInput(sheet_width=700, sheet_height=250, gap=5, margin=10)
    r = nest(doc, inp)
    assert r.sheet_count == 1
    assert_valid(r.doc, inp)


def test_waffle_ribs_nest_without_overlap():
    from cardboard_engine.components.geometry import Mesh
    from cardboard_engine.components.waffle import WaffleInput, waffle_slice

    from . import meshes

    ribs = waffle_slice(Mesh.from_lists(*meshes.uv_sphere(80, 16, 32)), WaffleInput(nx=4, ny=4, thickness=3)).parts
    inp = NestInput(sheet_width=400, sheet_height=300, gap=4, margin=8, rotations=(0, 90, 180, 270))
    r = nest(PartsDoc(parts=ribs), inp)
    assert_valid(r.doc, inp)
    assert all(0 < u <= 1 for u in r.utilisation)
