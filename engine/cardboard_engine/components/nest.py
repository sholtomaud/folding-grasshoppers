"""Multi-sheet nesting: true-shape bottom-left fill.

Parts go largest first. For each part, each allowed rotation and each open
sheet (first fit), the feasible positions are the inner-fit rectangle (the
part fully inside the sheet margin) minus the no-fit polygons of the parts
already on that sheet. A no-fit polygon is the Minkowski sum of a placed
outline and the mirrored moving outline, both grown by gap / 2; any
reference point outside it keeps the two parts at least ``gap`` apart.
The lowest, then leftmost, vertex of the feasible region wins. When no open
sheet has room, a new sheet starts.

Only outer outlines are nested: a small part is never placed inside another
part's hole (v1). Holes are kept and cut as usual.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import List, Sequence, Tuple

import pyclipper

from .parts import Part, PartsDoc, PartsError, Placement
from .region import SCALE, area, regions_from_loops

NEST_ARC_TOLERANCE = 0.1  # mm; coarser than cutting, fewer vertices to sum
MAX_NEST_VERTICES = 48  # per outline; no-fit polygons cost (n x m)
EPS = 1  # integer units (1 um): lets exact fits through clipper's area test
MAX_PARTS = 2000


class NestError(ValueError):
    """Invalid nesting input. Hops shows the message as a component error."""


@dataclass(frozen=True)
class NestInput:
    sheet_width: float = 1200.0  # mm
    sheet_height: float = 800.0  # mm
    gap: float = 5.0  # mm between parts
    margin: float = 10.0  # mm from the sheet edge
    rotations: Tuple[float, ...] = (0.0, 90.0)


@dataclass
class NestResult:
    doc: PartsDoc
    sheet_count: int
    utilisation: List[float]  # 0..1 per sheet


def parse_rotations(text: str) -> List[float]:
    """'0, 90, 180' -> [0.0, 90.0, 180.0]. Empty means no rotation."""
    items = [t.strip() for t in str(text or "").replace(";", ",").split(",") if t.strip()]
    if not items:
        return [0.0]
    out = []
    for t in items:
        try:
            v = float(t)
        except ValueError:
            raise NestError(f"Rotations must be numbers in degrees, like 0,90 (got {t!r})") from None
        if not math.isfinite(v):
            raise NestError("Rotations must be finite numbers")
        v = v % 360.0
        if v not in out:
            out.append(v)
    return out


def validate(inp: NestInput) -> None:
    errors = []
    for name, label, positive in (
        ("sheet_width", "Sheet width", True),
        ("sheet_height", "Sheet height", True),
        ("gap", "Gap", False),
        ("margin", "Margin", False),
    ):
        v = getattr(inp, name)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            errors.append(f"{label} must be a finite number")
        elif positive and v <= 0:
            errors.append(f"{label} must be greater than 0 (got {v:g})")
        elif v < 0:
            errors.append(f"{label} must not be negative (got {v:g})")
    if not errors and 2 * inp.margin >= min(inp.sheet_width, inp.sheet_height):
        errors.append("Margin leaves no room on the sheet")
    if not inp.rotations:
        errors.append("Allow at least one rotation (0 for none)")
    if errors:
        raise NestError("; ".join(errors))


def _ints(loop):
    return [(int(round(x * SCALE)), int(round(y * SCALE))) for x, y in loop]


@dataclass
class _Variant:
    rotation: float
    width: float  # mm, unoffset bbox
    height: float
    outline: List[List[Tuple[int, int]]]  # grown outlines, reference at bbox min = (0, 0)


def _rdp(pts, tol):
    """Ramer-Douglas-Peucker on an open polyline (iterative)."""
    if len(pts) < 3:
        return list(pts)
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        (ax, ay), (bx, by) = pts[i], pts[j]
        dx, dy = bx - ax, by - ay
        ll = math.hypot(dx, dy)
        best, idx = -1.0, -1
        for k in range(i + 1, j):
            px, py = pts[k]
            d = abs(dy * (px - ax) - dx * (py - ay)) / ll if ll else math.hypot(px - ax, py - ay)
            if d > best:
                best, idx = d, k
        if best > tol:
            keep[idx] = True
            stack += [(i, idx), (idx, j)]
    return [p for p, k in zip(pts, keep) if k]


def simplify(loop, max_vertices: int = MAX_NEST_VERTICES):
    """Fewer vertices, and the tolerance used: every original point lies within it."""
    if len(loop) <= max_vertices:
        return list(loop), 0.0
    xs, ys = zip(*loop)
    tol = max(max(xs) - min(xs), max(ys) - min(ys)) / 2000
    # Split the closed loop at its farthest pair so RDP sees two open halves.
    far = max(range(len(loop)), key=lambda k: (loop[k][0] - loop[0][0]) ** 2 + (loop[k][1] - loop[0][1]) ** 2)
    while True:
        out = _rdp(loop[: far + 1], tol)[:-1] + _rdp(loop[far:] + [loop[0]], tol)[:-1]
        if len(out) <= max_vertices or len(out) <= 4:
            return out, tol
        tol *= 1.6


def _variants(part: Part, rotations: Sequence[float], gap: float) -> List[_Variant]:
    out = []
    for r in rotations:
        p = part.rotated(r) if r else part.moved(0, 0)
        (x0, y0), (x1, y1) = p.bbox()
        p = p.moved(-x0, -y0)
        outlines = []
        for shape in regions_from_loops(p.cut):
            loop, tol = simplify(shape.outer)
            o = _ints(loop)
            if not pyclipper.Orientation(o):
                o.reverse()
            # Growing by the simplification tolerance keeps the outline conservative.
            grow = (gap / 2 + tol) * SCALE
            if grow > 0:
                pco = pyclipper.PyclipperOffset(2.0, NEST_ARC_TOLERANCE * SCALE)
                pco.AddPath(o, pyclipper.JT_MITER, pyclipper.ET_CLOSEDPOLYGON)
                outlines.extend(list(q) for q in pco.Execute(grow))
            else:
                outlines.append(o)
        out.append(_Variant(r, x1 - x0, y1 - y0, outlines))
    return out


def _solid(paths):
    """Outer boundaries of the union of ``paths``, holes filled, all counter-clockwise.

    The hole in a Minkowski sum of two outlines is where one part would sit
    inside the other, so it belongs to the no-fit polygon. (A true pocket a
    part could slide into is filled too: conservative, never an overlap.)
    """
    pc = pyclipper.Pyclipper()
    pc.AddPaths(paths, pyclipper.PT_SUBJECT, True)
    tree = pc.Execute2(pyclipper.CT_UNION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
    out = []
    stack = list(tree.Childs)
    while stack:
        node = stack.pop()
        if not node.IsHole:
            c = list(node.Contour)
            out.append(c if pyclipper.Orientation(c) else c[::-1])
        for hole in node.Childs:
            stack.extend(hole.Childs)
    return out


def _best_spot(placed, v: _Variant, inp: NestInput):
    """Lowest-then-leftmost feasible reference point (integer units), or None."""
    m = inp.margin * SCALE
    xmax = (inp.sheet_width - inp.margin - v.width) * SCALE
    ymax = (inp.sheet_height - inp.margin - v.height) * SCALE
    if xmax < m - EPS or ymax < m - EPS:
        return None
    lo_x, lo_y = int(math.floor(m)) - EPS, int(math.floor(m)) - EPS
    hi_x, hi_y = int(math.ceil(xmax)) + EPS, int(math.ceil(ymax)) + EPS
    ifp = [(lo_x, lo_y), (hi_x, lo_y), (hi_x, hi_y), (lo_x, hi_y)]
    if not placed:
        return (m, m)
    mirrored = [[(-x, -y) for x, y in o] for o in v.outline]
    nfps = []
    for fixed in placed:
        for pat in mirrored:
            nfps.extend(_solid(pyclipper.MinkowskiSum(pat, fixed, True)))
    pc = pyclipper.Pyclipper()
    pc.AddPath(ifp, pyclipper.PT_SUBJECT, True)
    pc.AddPaths(nfps, pyclipper.PT_CLIP, True)
    free = pc.Execute(pyclipper.CT_DIFFERENCE, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
    best = None
    for poly in free:
        for x, y in poly:
            if best is None or (y, x) < (best[1], best[0]):
                best = (x, y)
    if best is None:
        return None
    # Pull points from the EPS slack back onto the sheet.
    return (min(max(best[0], m), max(xmax, m)), min(max(best[1], m), max(ymax, m)))


def nest(doc: PartsDoc, inp: NestInput) -> NestResult:
    validate(inp)
    if not doc.parts:
        raise NestError("Nothing to nest: the parts list is empty")
    if len(doc.parts) > MAX_PARTS:
        raise NestError(f"{len(doc.parts)} parts is too many to nest at once (limit {MAX_PARTS})")
    rotations = [r % 360.0 for r in inp.rotations]

    variants = []
    too_big = []
    for p in doc.parts:
        vs = _variants(p, rotations, inp.gap)
        room_w = inp.sheet_width - 2 * inp.margin + 1e-6
        room_h = inp.sheet_height - 2 * inp.margin + 1e-6
        if not any(v.width <= room_w and v.height <= room_h for v in vs):
            too_big.append(p.id)
        variants.append(vs)
    if too_big:
        raise NestError(
            f"Too big for a {inp.sheet_width:g} x {inp.sheet_height:g} mm sheet with {inp.margin:g} mm margin "
            f"(try more rotations or a bigger sheet): {', '.join(too_big)}"
        )

    areas = []
    for p in doc.parts:
        try:
            areas.append(sum(area(s) for s in regions_from_loops(p.cut)))
        except PartsError:  # pragma: no cover - from_json already validated
            areas.append(0.0)
    order = sorted(range(len(doc.parts)), key=lambda i: -max(v.width * v.height for v in variants[i]))

    sheets: List[List[List[Tuple[int, int]]]] = []  # grown outlines placed on each sheet
    sheet_area: List[float] = []
    placements = [None] * len(doc.parts)
    for i in order:
        done = False
        for s in range(len(sheets) + 1):
            if s == len(sheets):
                sheets.append([])
                sheet_area.append(0.0)
            best = None
            for v in variants[i]:
                spot = _best_spot(sheets[s], v, inp)
                if spot is not None and (best is None or (spot[1], spot[0]) < (best[0][1], best[0][0])):
                    best = (spot, v)
            if best is None:
                continue
            (x, y), v = best
            ix, iy = int(round(x)), int(round(y))
            for o in v.outline:
                sheets[s].append([(px + ix, py + iy) for px, py in o])
            sheet_area[s] += areas[i]
            placements[i] = Placement(sheet=s, x=x / SCALE, y=y / SCALE, rotation=v.rotation)
            done = True
            break
        if not done:  # pragma: no cover - an empty sheet always fits (checked above)
            raise NestError(f"Could not place {doc.parts[i].id}")

    parts = [replace(p, placement=pl) for p, pl in zip(doc.parts, placements)]
    total = inp.sheet_width * inp.sheet_height
    return NestResult(
        doc=PartsDoc(parts=parts, sheet_size=(float(inp.sheet_width), float(inp.sheet_height))),
        sheet_count=len(sheets),
        utilisation=[a / total for a in sheet_area],
    )
