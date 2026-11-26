"""2D regions (outlines with holes) on top of pyclipper.

pyclipper works on integers, so coordinates are scaled to micrometres
(``SCALE`` units per mm). That is far below any cutter's precision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Sequence, Tuple

import pyclipper

Point2 = Tuple[float, float]
Loop2 = List[Point2]

SCALE = 1000.0  # 1 unit = 1 micrometre when the model is in mm
ARC_TOLERANCE = 0.01  # mm, for rounded offsets


@dataclass
class Shape:
    """One connected region: an outer boundary and zero or more holes."""

    outer: Loop2
    holes: List[Loop2] = field(default_factory=list)

    def loops(self) -> List[Loop2]:
        return [self.outer, *self.holes]


def _to_int(loop: Sequence[Point2]):
    return [(int(round(x * SCALE)), int(round(y * SCALE))) for x, y in loop]


def _to_float(path) -> Loop2:
    return [(x / SCALE, y / SCALE) for x, y in path]


def _from_tree(tree) -> List[Shape]:
    shapes: List[Shape] = []

    def visit(node):
        for child in node.Childs:
            # Children of the root are outers; their children are holes;
            # islands inside holes are outers again.
            if not child.IsHole:
                shape = Shape(_to_float(child.Contour))
                for hole in child.Childs:
                    shape.holes.append(_to_float(hole.Contour))
                    visit(hole)
                shapes.append(shape)

    visit(tree)
    return shapes


def _paths(shapes: Iterable[Shape]):
    out = []
    for s in shapes:
        out.extend(_to_int(loop) for loop in s.loops())
    return out


def regions_from_loops(loops: Iterable[Sequence[Point2]]) -> List[Shape]:
    """Even-odd union of closed loops: nested loops become holes."""
    paths = [_to_int(loop) for loop in loops if len(loop) >= 3]
    if not paths:
        return []
    pc = pyclipper.Pyclipper()
    pc.AddPaths(paths, pyclipper.PT_SUBJECT, True)
    tree = pc.Execute2(pyclipper.CT_UNION, pyclipper.PFT_EVENODD, pyclipper.PFT_EVENODD)
    return _from_tree(tree)


def difference(shapes: Sequence[Shape], cutters: Iterable[Sequence[Point2]]) -> List[Shape]:
    subj = _paths(shapes)
    clip = [_to_int(c) for c in cutters if len(c) >= 3]
    if not subj:
        return []
    if not clip:
        return list(shapes)
    pc = pyclipper.Pyclipper()
    pc.AddPaths(subj, pyclipper.PT_SUBJECT, True)
    pc.AddPaths(clip, pyclipper.PT_CLIP, True)
    tree = pc.Execute2(pyclipper.CT_DIFFERENCE, pyclipper.PFT_EVENODD, pyclipper.PFT_NONZERO)
    return _from_tree(tree)


def intersection(a: Sequence[Shape], b: Sequence[Shape]) -> List[Shape]:
    pa, pb = _paths(a), _paths(b)
    if not pa or not pb:
        return []
    pc = pyclipper.Pyclipper()
    pc.AddPaths(pa, pyclipper.PT_SUBJECT, True)
    pc.AddPaths(pb, pyclipper.PT_CLIP, True)
    tree = pc.Execute2(pyclipper.CT_INTERSECTION, pyclipper.PFT_EVENODD, pyclipper.PFT_EVENODD)
    return _from_tree(tree)


def offset(shapes: Sequence[Shape], delta: float, round_corners: bool = True) -> List[Shape]:
    """Grow (delta > 0) or shrink (delta < 0) regions; holes move the other way."""
    if delta == 0:
        return list(shapes)
    pco = pyclipper.PyclipperOffset(2.0, ARC_TOLERANCE * SCALE)
    join = pyclipper.JT_ROUND if round_corners else pyclipper.JT_MITER
    for s in shapes:
        outer = _to_int(s.outer)
        if not pyclipper.Orientation(outer):
            outer.reverse()
        pco.AddPath(outer, join, pyclipper.ET_CLOSEDPOLYGON)
        for h in s.holes:
            hole = _to_int(h)
            if pyclipper.Orientation(hole):
                hole.reverse()
            pco.AddPath(hole, join, pyclipper.ET_CLOSEDPOLYGON)
    return _from_tree(pco.Execute2(delta * SCALE))


def area(shape: Shape) -> float:
    a = abs(pyclipper.Area(_to_int(shape.outer)))
    a -= sum(abs(pyclipper.Area(_to_int(h))) for h in shape.holes)
    return a / (SCALE * SCALE)


def contains(shape: Shape, pt: Point2) -> bool:
    p = (int(round(pt[0] * SCALE)), int(round(pt[1] * SCALE)))
    if pyclipper.PointInPolygon(p, _to_int(shape.outer)) != 1:
        return False
    return all(pyclipper.PointInPolygon(p, _to_int(h)) == 0 for h in shape.holes)


def line_intervals(shape: Shape, u: float) -> List[Tuple[float, float]]:
    """Where the vertical line ``x == u`` is inside the shape, as sorted (v0, v1)."""
    vs = []
    for loop in shape.loops():
        n = len(loop)
        for i in range(n):
            (x0, y0), (x1, y1) = loop[i], loop[(i + 1) % n]
            if (x0 <= u < x1) or (x1 <= u < x0):
                t = (u - x0) / (x1 - x0)
                vs.append(y0 + t * (y1 - y0))
    vs.sort()
    return [(vs[i], vs[i + 1]) for i in range(0, len(vs) - 1, 2)]


def bounds(loops: Iterable[Sequence[Point2]]) -> Tuple[Point2, Point2]:
    xs, ys = [], []
    for loop in loops:
        for x, y in loop:
            xs.append(x)
            ys.append(y)
    if not xs:
        return (0.0, 0.0), (0.0, 0.0)
    return (min(xs), min(ys)), (max(xs), max(ys))


def interior_point(shape: Shape, samples: int = 15) -> Point2:
    """A point inside the shape near its middle (for labels).

    Tries the bounding-box centre, then a grid, preferring points far from the
    edge so an engraved label sits on solid material.
    """
    (x0, y0), (x1, y1) = bounds([shape.outer])
    centre = ((x0 + x1) / 2, (y0 + y1) / 2)
    best, best_score = None, -1.0
    for i in range(samples):
        for j in range(samples):
            p = (x0 + (x1 - x0) * (i + 0.5) / samples, y0 + (y1 - y0) * (j + 0.5) / samples)
            if not contains(shape, p):
                continue
            score = _edge_distance(shape, p) - 0.01 * ((p[0] - centre[0]) ** 2 + (p[1] - centre[1]) ** 2) ** 0.5
            if score > best_score:
                best, best_score = p, score
    return best if best is not None else centre


def _edge_distance(shape: Shape, p: Point2) -> float:
    best = float("inf")
    px, py = p
    for loop in shape.loops():
        n = len(loop)
        for i in range(n):
            (ax, ay), (bx, by) = loop[i], loop[(i + 1) % n]
            dx, dy = bx - ax, by - ay
            ll = dx * dx + dy * dy
            t = 0.0 if ll == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / ll))
            qx, qy = ax + t * dx - px, ay + t * dy - py
            best = min(best, qx * qx + qy * qy)
    return best ** 0.5
