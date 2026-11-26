"""Unfold a polyhedral mesh into flat nets with fold lines and glue tabs.

1. Weld vertices that share a position (Rhino splits them at creases).
2. Faces are triangles or planar quads; a quad that is not planar is split.
3. Nets grow from the largest unplaced face across its edges, flattest folds
   first, then longest edges. Each new face is rotated flat about the shared
   edge. A face that would overlap its net is left for another edge or a
   new net, so nets never overlap themselves.
4. Edges joined inside a net are folds, scored when the fold is sharper than
   ``flat_angle``. Every other edge is cut, and the two sides of a cut edge
   get the same number. One side gets a glue tab (if it fits) with a fold
   at its base.

Net ids are N1, N2... largest first.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import pyclipper

from .geometry import Mesh
from .parts import Part, Text
from .region import SCALE, interior_point, regions_from_loops

Vec = Tuple[float, float, float]
P2 = Tuple[float, float]

MAX_FACES = 20_000


class UnfoldError(ValueError):
    """Invalid unfold input. Hops shows the message as a component error."""


@dataclass(frozen=True)
class UnfoldInput:
    tab_width: float = 8.0  # mm, 0 = no tabs
    tab_angle: float = 45.0  # degrees, slope of the tab's sides
    flat_angle: float = 1.0  # degrees; folds flatter than this are not scored


@dataclass
class UnfoldResult:
    parts: List[Part]
    face_count: int
    tab_count: int


def validate(inp: UnfoldInput) -> None:
    errors = []
    for name, label in (("tab_width", "Tab width"), ("tab_angle", "Tab angle"), ("flat_angle", "Flat angle")):
        v = getattr(inp, name)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            errors.append(f"{label} must be a finite number")
    if not errors:
        if inp.tab_width < 0:
            errors.append(f"Tab width must not be negative (got {inp.tab_width:g})")
        if not 0 < inp.tab_angle < 90:
            errors.append(f"Tab angle must be between 0 and 90 degrees (got {inp.tab_angle:g})")
        if not 0 <= inp.flat_angle < 180:
            errors.append(f"Flat angle must be from 0 up to 180 degrees (got {inp.flat_angle:g})")
    if errors:
        raise UnfoldError("; ".join(errors))


# ---- small vector helpers --------------------------------------------------


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a):
    return math.sqrt(_dot(a, a))


def _unit(a):
    n = _norm(a)
    return (a[0] / n, a[1] / n, a[2] / n)


def _newell(pts) -> Vec:
    """Face normal (unnormalised, length = 2 x area)."""
    nx = ny = nz = 0.0
    for i in range(len(pts)):
        x0, y0, z0 = pts[i]
        x1, y1, z1 = pts[(i + 1) % len(pts)]
        nx += (y0 - y1) * (z0 + z1)
        ny += (z0 - z1) * (x0 + x1)
        nz += (x0 - x1) * (y0 + y1)
    return (nx, ny, nz)


def _area2(poly: Sequence[P2]) -> float:
    return sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly))) / 2


# ---- mesh preparation ------------------------------------------------------


def _prepare(mesh: Mesh):
    lo, hi = mesh.bbox()
    span = max(hi[i] - lo[i] for i in range(3)) or 1.0
    tol = span * 1e-7
    weld: Dict[Tuple[int, int, int], int] = {}
    verts: List[Vec] = []
    remap = []
    for p in mesh.vertices:
        key = (round(p[0] / tol), round(p[1] / tol), round(p[2] / tol))
        if key not in weld:
            weld[key] = len(verts)
            verts.append(p)
        remap.append(weld[key])
    faces: List[Tuple[int, ...]] = []
    for f in mesh.faces:
        idx = tuple(remap[i] for i in f)
        if len(idx) == 4:
            pts = [verts[i] for i in idx]
            n = _newell(pts)
            if _norm(n) == 0 or abs(_dot(_sub(pts[3], pts[0]), _unit(n))) > span * 1e-5:
                faces += [(idx[0], idx[1], idx[2]), (idx[0], idx[2], idx[3])]
                continue
        faces.append(idx)
    good = []
    for f in faces:
        if len(set(f)) < 3 or _norm(_newell([verts[i] for i in f])) <= (span * 1e-6) ** 2:
            continue
        good.append(f)
    if not good:
        raise UnfoldError("The mesh has no faces with area")
    if len(good) > MAX_FACES:
        raise UnfoldError(f"{len(good)} faces is too many to unfold (limit {MAX_FACES}); reduce the mesh")
    return verts, good


def _overlaps(poly: List[P2], others: List[List[P2]]) -> bool:
    """Does ``poly`` (shrunk a hair, so touching is fine) overlap any of ``others``?"""
    if not others:
        return False
    subj = [(int(round(x * SCALE)), int(round(y * SCALE))) for x, y in poly]
    if not pyclipper.Orientation(subj):
        subj.reverse()
    pco = pyclipper.PyclipperOffset()
    pco.AddPath(subj, pyclipper.JT_MITER, pyclipper.ET_CLOSEDPOLYGON)
    shrunk = pco.Execute(-10)  # 10 um
    if not shrunk:
        return False
    sx = [p[0] for s in shrunk for p in s]
    sy = [p[1] for s in shrunk for p in s]
    box = (min(sx), min(sy), max(sx), max(sy))
    clip = []
    for o in others:
        q = [(int(round(x * SCALE)), int(round(y * SCALE))) for x, y in o]
        if max(p[0] for p in q) < box[0] or min(p[0] for p in q) > box[2]:
            continue
        if max(p[1] for p in q) < box[1] or min(p[1] for p in q) > box[3]:
            continue
        clip.append(q)
    if not clip:
        return False
    pc = pyclipper.Pyclipper()
    pc.AddPaths(shrunk, pyclipper.PT_SUBJECT, True)
    pc.AddPaths(clip, pyclipper.PT_CLIP, True)
    out = pc.Execute(pyclipper.CT_INTERSECTION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
    return any(abs(pyclipper.Area(p)) > 1.0 for p in out)


def unfold(mesh: Mesh, inp: UnfoldInput) -> UnfoldResult:
    validate(inp)
    verts, faces = _prepare(mesh)
    normals = [_unit(_newell([verts[i] for i in f])) for f in faces]

    edge_faces: Dict[Tuple[int, int], List[int]] = {}
    for fi, f in enumerate(faces):
        for k in range(len(f)):
            a, b = f[k], f[(k + 1) % len(f)]
            edge_faces.setdefault((min(a, b), max(a, b)), []).append(fi)

    def fold_angle(f1, f2) -> float:
        c = max(-1.0, min(1.0, _dot(normals[f1], normals[f2])))
        return math.degrees(math.acos(c))

    def flatten_root(fi) -> Dict[int, P2]:
        f = faces[fi]
        o = verts[f[0]]
        u = _unit(_sub(verts[f[1]], o))
        w = _cross(normals[fi], u)
        return {i: (_dot(_sub(verts[i], o), u), _dot(_sub(verts[i], o), w)) for i in f}

    def flatten_child(fi, a, b, pa: P2, pb: P2) -> Dict[int, P2]:
        """Place face ``fi`` so its edge (a, b) lands on (pa, pb), counter-clockwise like the net."""
        A = verts[a]
        u = _unit(_sub(verts[b], A))
        w = _cross(normals[fi], u)
        ex, ey = pb[0] - pa[0], pb[1] - pa[1]
        el = math.hypot(ex, ey)
        ux, uy = ex / el, ey / el
        out = {}
        for i in faces[fi]:
            d = _sub(verts[i], A)
            x, y = _dot(d, u), _dot(d, w)
            out[i] = (pa[0] + x * ux - y * uy, pa[1] + x * uy + y * ux)
        return out

    face_area = [_norm(_newell([verts[i] for i in f])) / 2 for f in faces]
    net_of: List[Optional[int]] = [None] * len(faces)
    pos: List[Optional[Dict[int, P2]]] = [None] * len(faces)
    nets: List[List[int]] = []
    for start in sorted(range(len(faces)), key=lambda i: -face_area[i]):
        if net_of[start] is not None:
            continue
        net_id = len(nets)
        members = [start]
        net_of[start] = net_id
        pos[start] = flatten_root(start)
        polys = [[pos[start][i] for i in faces[start]]]
        heap: List = []
        counter = 0

        def push(fi):
            nonlocal counter
            f = faces[fi]
            for k in range(len(f)):
                a, b = f[k], f[(k + 1) % len(f)]
                for other in edge_faces[(min(a, b), max(a, b))]:
                    if other != fi and net_of[other] is None and len(edge_faces[(min(a, b), max(a, b))]) == 2:
                        length = _norm(_sub(verts[a], verts[b]))
                        heapq.heappush(heap, (round(fold_angle(fi, other), 6), -length, counter, fi, other, a, b))
                        counter += 1

        push(start)
        while heap:
            _, _, _, parent, child, a, b = heapq.heappop(heap)
            if net_of[child] is not None:
                continue
            # In the parent's winding the edge runs a -> b; the child runs b -> a.
            placed = flatten_child(child, b, a, pos[parent][b], pos[parent][a])
            poly = [placed[i] for i in faces[child]]
            if _area2(poly) <= 0 or _overlaps(poly, polys):
                continue
            net_of[child] = net_id
            pos[child] = placed
            members.append(child)
            polys.append(poly)
            push(child)
        nets.append(members)

    parts: List[Part] = []
    tab_count = 0
    edge_number = 0
    cut_marks: Dict[int, List[Text]] = {n: [] for n in range(len(nets))}
    tab_polys: Dict[int, List[List[P2]]] = {n: [] for n in range(len(nets))}
    scores: Dict[int, List[List[P2]]] = {n: [] for n in range(len(nets))}
    net_polys = {n: [[pos[fi][i] for i in faces[fi]] for fi in members] for n, members in enumerate(nets)}

    def same(p: P2, q: P2) -> bool:
        return abs(p[0] - q[0]) < 1e-6 and abs(p[1] - q[1]) < 1e-6

    text_size = max(1.0, min(5.0, inp.tab_width * 0.6 if inp.tab_width else 3.0))
    for (a, b), fs in sorted(edge_faces.items()):
        sides = []  # (face, net, pa, pb) with pa -> pb in that face's winding
        for fi in fs:
            f = faces[fi]
            k = f.index(a)
            if f[(k + 1) % len(f)] == b:
                sides.append((fi, net_of[fi], pos[fi][a], pos[fi][b]))
            else:
                sides.append((fi, net_of[fi], pos[fi][b], pos[fi][a]))
        if len(sides) == 2:
            (f1, n1, p1a, p1b), (f2, n2, p2a, p2b) = sides
            if n1 == n2 and same(p1a, p2b) and same(p1b, p2a):
                if fold_angle(f1, f2) >= inp.flat_angle and fold_angle(f1, f2) > 1e-9:
                    scores[n1].append([p1a, p1b])
                continue
        # A cut edge: number both sides, and put a tab on the first side that has room.
        if len(sides) < 2:
            continue  # open mesh boundary: just cut, nothing to glue
        edge_number += 1
        for fi, n, pa, pb in sides:
            cut_marks[n].append(Text(str(edge_number), _inset(pa, pb, text_size * 0.9), text_size))
        if inp.tab_width > 0:
            for fi, n, pa, pb in sides:
                tab = _tab(pa, pb, inp.tab_width, inp.tab_angle)
                if not _overlaps(tab, net_polys[n] + tab_polys[n]):
                    tab_polys[n].append(tab)
                    scores[n].append([pa, pb])
                    tab_count += 1
                    break

    order = sorted(range(len(nets)), key=lambda n: -sum(face_area[f] for f in nets[n]))
    for rank, n in enumerate(order, start=1):
        shapes = regions_from_loops(net_polys[n])  # faces are disjoint; even-odd is fine
        loops = _union(net_polys[n] + tab_polys[n])
        pid = f"N{rank}"
        parts.append(
            Part(
                id=pid,
                cut=loops,
                score=scores[n],
                label=pid,
                label_at=interior_point(max(shapes, key=lambda s: abs(_area2(s.outer)))) if shapes else None,
                texts=cut_marks[n],
            )
        )
    return UnfoldResult(parts=parts, face_count=len(faces), tab_count=tab_count)


def _union(polys) -> List[List[P2]]:
    pc = pyclipper.Pyclipper()
    for p in polys:
        q = [(int(round(x * SCALE)), int(round(y * SCALE))) for x, y in p]
        if not pyclipper.Orientation(q):
            q.reverse()
        pc.AddPath(q, pyclipper.PT_SUBJECT, True)
    out = pc.Execute(pyclipper.CT_UNION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
    return [[(x / SCALE, y / SCALE) for x, y in path] for path in out]


def _outward(pa: P2, pb: P2) -> P2:
    """Unit normal pointing out of a counter-clockwise face, to the right of pa -> pb."""
    dx, dy = pb[0] - pa[0], pb[1] - pa[1]
    l = math.hypot(dx, dy)
    return (dy / l, -dx / l)


def _inset(pa: P2, pb: P2, d: float) -> P2:
    nx, ny = _outward(pa, pb)
    l = math.dist(pa, pb)
    d = min(d, l / 3)
    return ((pa[0] + pb[0]) / 2 - nx * d, (pa[1] + pb[1]) / 2 - ny * d)


def _tab(pa: P2, pb: P2, width: float, angle: float) -> List[P2]:
    nx, ny = _outward(pa, pb)
    l = math.dist(pa, pb)
    ux, uy = (pb[0] - pa[0]) / l, (pb[1] - pa[1]) / l
    run = width / math.tan(math.radians(angle))
    if 2 * run >= l:  # short edge: a triangle tab, no taller than the edge allows
        h = min(width, l / 2 * math.tan(math.radians(angle)))
        mid = ((pa[0] + pb[0]) / 2 + nx * h, (pa[1] + pb[1]) / 2 + ny * h)
        return [pa, mid, pb]
    return [
        pa,
        (pa[0] + ux * run + nx * width, pa[1] + uy * run + ny * width),
        (pb[0] - ux * run + nx * width, pb[1] - uy * run + ny * width),
        pb,
    ]
