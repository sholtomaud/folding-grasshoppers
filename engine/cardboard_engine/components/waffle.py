"""Waffle slicing: a grid of interlocking cardboard ribs.

The mesh is cut by ``nx`` evenly spaced planes normal to X and ``ny`` planes
normal to Y. Where an X rib crosses a Y rib, both get a slot of width
``thickness + clearance`` reaching half way: X ribs are slotted from the top,
Y ribs from the bottom, so they slide together.

Part frames (mm, before layout):

- X ribs: u = model y, v = model z
- Y ribs: u = model x, v = model z

``kerf`` grows every outline by kerf / 2 (and so narrows slots by the same),
compensating for the material the cutter removes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from .geometry import GeometryError, Loop3, Mesh, section_2d, to_model
from .parts import Part
from .region import Shape, difference, interior_point, line_intervals, offset, regions_from_loops

MAX_RIBS = 200  # per direction


class WaffleError(ValueError):
    """Invalid waffle input. Hops shows the message as a component error."""


@dataclass(frozen=True)
class WaffleInput:
    nx: int = 5
    ny: int = 5
    thickness: float = 3.0  # mm, material
    clearance: float = 0.2  # mm, extra slot width
    kerf: float = 0.0  # mm, cutter width


@dataclass
class WaffleResult:
    parts: List[Part]
    ribs3d: Dict[str, List[Loop3]]  # slotted outlines (no kerf) in model space
    x_positions: List[float] = field(default_factory=list)
    y_positions: List[float] = field(default_factory=list)


def validate(inp: WaffleInput) -> None:
    errors = []
    for name in ("nx", "ny"):
        v = getattr(inp, name)
        if isinstance(v, bool) or not isinstance(v, int):
            errors.append(f"{name} must be a whole number")
        elif v < 0:
            errors.append(f"{name} must not be negative (got {v})")
        elif v > MAX_RIBS:
            errors.append(f"{name} must be at most {MAX_RIBS} (got {v})")
    for name, label in (("thickness", "Thickness"), ("clearance", "Slot clearance"), ("kerf", "Kerf")):
        v = getattr(inp, name)
        if not isinstance(v, (int, float)) or not math.isfinite(v):
            errors.append(f"{label} must be a finite number")
        elif name == "thickness" and v <= 0:
            errors.append(f"{label} must be greater than 0 (got {v:g})")
        elif v < 0:
            errors.append(f"{label} must not be negative (got {v:g})")
    if not errors and inp.nx + inp.ny == 0:
        errors.append("Ask for at least one rib (nx or ny above 0)")
    if errors:
        raise WaffleError("; ".join(errors))


def _positions(lo: float, hi: float, n: int) -> List[float]:
    return [lo + (hi - lo) * (i + 1) / (n + 1) for i in range(n)]


def _intervals(shapes: List[Shape], u: float) -> List[Tuple[float, float]]:
    out = []
    for s in shapes:
        out.extend(line_intervals(s, u))
    return sorted(out)


def _overlaps(a, b) -> List[Tuple[float, float]]:
    out = []
    for a0, a1 in a:
        for b0, b1 in b:
            lo, hi = max(a0, b0), min(a1, b1)
            if hi - lo > 1e-6:
                out.append((lo, hi))
    return sorted(out)


def _reach(intervals, lo: float, hi: float, up: bool, limit: float) -> float:
    """How far a slot may run past an interval end before it meets other material."""
    if up:
        above = [a for a, _ in intervals if a > hi + 1e-9]
        return (hi + min(above)) / 2 if above else limit
    below = [b for _, b in intervals if b < lo - 1e-9]
    return (lo + max(below)) / 2 if below else limit


def _rect(u0, u1, v0, v1):
    return [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]


def waffle_slice(mesh: Mesh, inp: WaffleInput) -> WaffleResult:
    validate(inp)
    (lx, ly, lz), (hx, hy, hz) = mesh.bbox()
    w = inp.thickness + inp.clearance
    for n, lo, hi, axis in ((inp.nx, lx, hx, "X"), (inp.ny, ly, hy, "Y")):
        if n and (hi - lo) / (n + 1) <= w:
            raise WaffleError(
                f"{axis} rib spacing ({(hi - lo) / (n + 1):.2f} mm) is not wider than the slot "
                f"({w:g} mm); use fewer {axis} ribs"
            )
    xs = _positions(lx, hx, inp.nx)
    ys = _positions(ly, hy, inp.ny)
    try:
        x_sections = [regions_from_loops(section_2d(mesh, 0, x)) for x in xs]
        y_sections = [regions_from_loops(section_2d(mesh, 1, y)) for y in ys]
    except GeometryError as exc:
        raise WaffleError(str(exc)) from None

    margin = (hz - lz) + 1.0
    x_cuts: List[List] = [[] for _ in xs]
    y_cuts: List[List] = [[] for _ in ys]
    for i, x in enumerate(xs):
        for j, y in enumerate(ys):
            xi = _intervals(x_sections[i], y)  # X rib along the line x = xs[i], y = ys[j]
            yi = _intervals(y_sections[j], x)
            for a, b in _overlaps(xi, yi):
                m = (a + b) / 2
                top = _reach(xi, a, b, up=True, limit=hz + margin)
                bottom = _reach(yi, a, b, up=False, limit=lz - margin)
                x_cuts[i].append(_rect(y - w / 2, y + w / 2, m, top))
                y_cuts[j].append(_rect(x - w / 2, x + w / 2, bottom, m))

    parts: List[Part] = []
    ribs3d: Dict[str, List[Loop3]] = {}
    for prefix, axis, positions, sections, cuts in (
        ("X", 0, xs, x_sections, x_cuts),
        ("Y", 1, ys, y_sections, y_cuts),
    ):
        for k, (pos, shapes, cutters) in enumerate(zip(positions, sections, cuts), start=1):
            slotted = difference(shapes, cutters)
            slotted.sort(key=lambda s: min(p[0] for p in s.outer))
            for n, shape in enumerate(slotted):
                pid = f"{prefix}{k}" if len(slotted) == 1 else f"{prefix}{k}{_suffix(n)}"
                ribs3d[pid] = [[to_model(axis, pos, uv) for uv in loop] for loop in shape.loops()]
                cut_shapes = offset([shape], inp.kerf / 2) if inp.kerf else [shape]
                loops = [loop for s in cut_shapes for loop in s.loops()]
                parts.append(Part(id=pid, cut=loops, label=pid, label_at=interior_point(shape)))
    if not parts:
        raise WaffleError("No rib cuts the mesh; is it a closed mesh with some volume?")
    return WaffleResult(parts=parts, ribs3d=ribs3d, x_positions=xs, y_positions=ys)


def _suffix(n: int) -> str:
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(ord("a") + r) + s
    return s
