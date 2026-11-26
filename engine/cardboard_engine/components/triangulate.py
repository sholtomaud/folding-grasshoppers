"""Fill flat outlines with triangles and extrude them, for the 3D preview.

``triangulate`` cuts the region (even-odd rule, so holes work) into
horizontal trapezoids: between two consecutive vertex heights no edge
starts or ends, so the edges crossing that band, paired left to right, bound
straight-sided trapezoids. It is exact, needs no ear clipping, and copes
with the slots and holes cardboard parts are full of.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from .geometry import plane_axes, to_model

P2 = Tuple[float, float]
P3 = Tuple[float, float, float]
Tri2 = Tuple[P2, P2, P2]
Tri3 = Tuple[P3, P3, P3]


def _open(loop: Sequence[Sequence[float]]) -> List[P2]:
    pts = [(float(p[0]), float(p[1])) for p in loop]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts.pop()
    return pts


def triangulate(loops: Sequence[Sequence[Sequence[float]]]) -> List[Tri2]:
    edges = []  # (y_low, y_high, x_at_low, x_at_high)
    for loop in loops:
        pts = _open(loop)
        for i in range(len(pts)):
            (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % len(pts)]
            if y0 == y1:
                continue
            if y0 > y1:
                x0, y0, x1, y1 = x1, y1, x0, y0
            edges.append((y0, y1, x0, x1))
    if not edges:
        return []
    edges.sort()
    ys = sorted({e[0] for e in edges} | {e[1] for e in edges})
    out: List[Tri2] = []
    active: List[tuple] = []
    k = 0
    for y0, y1 in zip(ys, ys[1:]):
        while k < len(edges) and edges[k][0] <= y0:
            active.append(edges[k])
            k += 1
        active = [e for e in active if e[1] > y0]
        if y1 - y0 <= 0:
            continue
        cross = []
        for ey0, ey1, ex0, ex1 in active:
            if ey0 <= y0 and ey1 >= y1:
                t0 = (y0 - ey0) / (ey1 - ey0)
                t1 = (y1 - ey0) / (ey1 - ey0)
                cross.append((ex0 + (ex1 - ex0) * t0, ex0 + (ex1 - ex0) * t1))
        cross.sort(key=lambda c: c[0] + c[1])
        for (a0, a1), (b0, b1) in zip(cross[0::2], cross[1::2]):
            if b0 - a0 > 0:
                out.append(((a0, y0), (b0, y0), (b1, y1)))
            if b1 - a1 > 0:
                out.append(((a0, y0), (b1, y1), (a1, y1)))
    return out


def planar_axis(loops3d: Sequence[Sequence[Sequence[float]]], tol: float = 1e-6) -> Optional[int]:
    """The axis (0, 1, 2) along which every point has the same coordinate, if any."""
    pts = [p for loop in loops3d for p in loop]
    if not pts:
        return None
    for axis in (2, 0, 1):
        vals = [p[axis] for p in pts]
        if max(vals) - min(vals) <= tol * max(1.0, abs(vals[0])):
            return axis
    return None


def extrude(loops3d: Sequence[Sequence[Sequence[float]]], thickness: float) -> List[Tri3]:
    """Axis-aligned flat outlines (in place in the model) as a board ``thickness`` thick."""
    axis = planar_axis(loops3d)
    if axis is None:
        raise ValueError("Outlines must be flat and lie in an X, Y or Z plane")
    level = float(loops3d[0][0][axis])
    a, b = plane_axes(axis)
    loops = [_open([(p[a], p[b]) for p in loop]) for loop in loops3d]
    lo, hi = level - thickness / 2, level + thickness / 2

    def p3(uv: P2, at: float) -> P3:
        return to_model(axis, at, uv)

    out: List[Tri3] = []
    for t in triangulate(loops):
        out.append((p3(t[0], lo), p3(t[2], lo), p3(t[1], lo)))
        out.append((p3(t[0], hi), p3(t[1], hi), p3(t[2], hi)))
    for loop in loops:
        for i in range(len(loop)):
            u, v = loop[i], loop[(i + 1) % len(loop)]
            out.append((p3(u, lo), p3(v, lo), p3(v, hi)))
            out.append((p3(u, lo), p3(v, hi), p3(u, hi)))
    return out
