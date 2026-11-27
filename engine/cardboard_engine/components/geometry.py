"""Plain-Python meshes and planar sections.

A ``Mesh`` here is just vertices and faces (triangles or quads), so the
geometry code needs neither rhino3dm nor numpy. ``hops_geometry`` converts
rhino3dm meshes into this form.

Units are whatever the model uses; Fold Theory assumes millimetres.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

Point3 = Tuple[float, float, float]
Point2 = Tuple[float, float]
Loop2 = List[Point2]
Loop3 = List[Point3]

MAX_FACES = 2_000_000


class GeometryError(ValueError):
    """Invalid geometry. Hops shows the message as a component error."""


@dataclass(frozen=True)
class Mesh:
    vertices: Tuple[Point3, ...]
    faces: Tuple[Tuple[int, ...], ...]

    @classmethod
    def from_lists(cls, vertices: Iterable[Sequence[float]], faces: Iterable[Sequence[int]]) -> "Mesh":
        verts = []
        for v in vertices:
            if len(v) != 3:
                raise GeometryError("Mesh vertices must have 3 coordinates")
            p = tuple(float(c) for c in v)
            if not all(math.isfinite(c) for c in p):
                raise GeometryError("Mesh vertices must be finite numbers")
            verts.append(p)
        fcs = []
        n = len(verts)
        for f in faces:
            idx = tuple(int(i) for i in f)
            if len(idx) == 4 and idx[2] == idx[3]:
                idx = idx[:3]  # rhino3dm reports triangles as (a, b, c, c)
            if len(idx) not in (3, 4):
                raise GeometryError("Mesh faces must be triangles or quads")
            if any(i < 0 or i >= n for i in idx):
                raise GeometryError("Mesh face refers to a vertex that does not exist")
            fcs.append(idx)
        if not verts or not fcs:
            raise GeometryError("Mesh is empty")
        if len(fcs) > MAX_FACES:
            raise GeometryError(f"Mesh has {len(fcs)} faces; the limit is {MAX_FACES}")
        return cls(tuple(verts), tuple(fcs))

    def triangles(self) -> List[Tuple[Point3, Point3, Point3]]:
        v = self.vertices
        out = []
        for f in self.faces:
            out.append((v[f[0]], v[f[1]], v[f[2]]))
            if len(f) == 4:
                out.append((v[f[0]], v[f[2]], v[f[3]]))
        return out

    def bbox(self) -> Tuple[Point3, Point3]:
        xs, ys, zs = zip(*self.vertices)
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


# Which model axes become (u, v) for a section normal to each axis.
_PLANE_AXES = {0: (1, 2), 1: (0, 2), 2: (0, 1)}


def plane_axes(axis: int) -> Tuple[int, int]:
    if axis not in _PLANE_AXES:
        raise GeometryError("axis must be 0 (x), 1 (y) or 2 (z)")
    return _PLANE_AXES[axis]


def to_model(axis: int, value: float, uv: Point2) -> Point3:
    """Map a section point (u, v) back to model coordinates."""
    a, b = plane_axes(axis)
    p = [0.0, 0.0, 0.0]
    p[axis] = value
    p[a], p[b] = uv
    return (p[0], p[1], p[2])


def section_2d(mesh: Mesh, axis: int, value: float) -> List[Loop2]:
    """Closed loops where the plane ``coord[axis] == value`` cuts the mesh.

    Points are (u, v) in the plane (see ``plane_axes``). Open chains (from a
    mesh that is not closed) are closed with a straight segment, so a slightly
    leaky mesh still gives usable outlines.
    """
    a, b = plane_axes(axis)
    lo, hi = mesh.bbox()
    span = max(hi[i] - lo[i] for i in range(3)) or 1.0
    # A plane through a vertex makes degenerate segments. Nudge it off.
    eps = span * 1e-9
    v = value
    coords = {p[axis] for p in mesh.vertices}
    for _ in range(8):
        if not any(abs(c - v) <= eps for c in coords):
            break
        v += span * 1e-7
    if v < lo[axis] or v > hi[axis]:
        return []

    segments = []
    for tri in mesh.triangles():
        d = [p[axis] - v for p in tri]
        pts = []
        for i in range(3):
            p, q = tri[i], tri[(i + 1) % 3]
            dp, dq = d[i], d[(i + 1) % 3]
            if (dp < 0) != (dq < 0):
                t = dp / (dp - dq)
                pts.append((p[a] + t * (q[a] - p[a]), p[b] + t * (q[b] - p[b])))
        if len(pts) == 2 and pts[0] != pts[1]:
            segments.append((pts[0], pts[1]))
    return _chain(segments, tol=span * 1e-6)


def _chain(segments, tol: float) -> List[Loop2]:
    """Join unordered segments into loops by matching endpoints."""

    def key(p):
        return (round(p[0] / tol), round(p[1] / tol))

    ends = {}
    for i, (p, q) in enumerate(segments):
        ends.setdefault(key(p), []).append((i, 0))
        ends.setdefault(key(q), []).append((i, 1))
    used = [False] * len(segments)
    loops = []
    for start in range(len(segments)):
        if used[start]:
            continue
        used[start] = True
        p0, p1 = segments[start]
        loop = [p0, p1]
        first = key(p0)
        cur = key(p1)
        while cur != first:
            nxt = None
            for j, end in ends.get(cur, ()):
                if not used[j]:
                    nxt = (j, end)
                    break
            if nxt is None:
                break  # open chain: close it with a straight edge
            j, end = nxt
            used[j] = True
            other = segments[j][1 - end]
            loop.append(other)
            cur = key(other)
        if cur == first:
            loop.pop()  # last point repeats the first
        if len(loop) >= 3:
            loops.append(loop)
    return loops
