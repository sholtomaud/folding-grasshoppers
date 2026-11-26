"""Contour stacks: a model built from flat layers of board.

The mesh is cut into bands ``thickness`` thick along ``axis`` (z by
default), starting at the bottom of its bounding box. Each band becomes one
layer: the section through the middle of the band. A last band thinner than
``thickness`` still gets a layer.

With ``etch`` on, each layer is etched with the parts of its neighbours'
outlines that fall on it, so the stack can be lined up while gluing.

Part frames: z layers use (x, y); x layers use (y, z); y layers use (x, z).
Layers that fall apart are labelled ``L05a``, ``L05b``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List

import pyclipper

from .geometry import GeometryError, Loop3, Mesh, section_2d, to_model
from .region import SCALE, Shape, interior_point, offset, regions_from_loops
from .parts import Part
from .waffle import _suffix

MAX_LAYERS = 1000
AXES = {"x": 0, "y": 1, "z": 2}


class ContourError(ValueError):
    """Invalid contour input. Hops shows the message as a component error."""


@dataclass(frozen=True)
class ContourInput:
    thickness: float = 3.0  # mm per layer
    kerf: float = 0.0  # mm
    etch: bool = True
    axis: str = "z"


@dataclass
class ContourResult:
    parts: List[Part]
    layers3d: Dict[str, List[Loop3]]
    levels: List[float] = field(default_factory=list)


def validate(inp: ContourInput) -> None:
    errors = []
    for name, label in (("thickness", "Layer thickness"), ("kerf", "Kerf")):
        v = getattr(inp, name)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            errors.append(f"{label} must be a finite number")
        elif name == "thickness" and v <= 0:
            errors.append(f"{label} must be greater than 0 (got {v:g})")
        elif v < 0:
            errors.append(f"{label} must not be negative (got {v:g})")
    if str(inp.axis).strip().lower() not in AXES:
        errors.append(f"Axis must be x, y or z (got {inp.axis!r})")
    if errors:
        raise ContourError("; ".join(errors))


def _clip_lines(loops, shapes: List[Shape]):
    """The parts of closed ``loops`` that lie inside ``shapes``, as open polylines."""
    if not loops or not shapes:
        return []
    pc = pyclipper.Pyclipper()
    for loop in loops:
        path = [(int(round(x * SCALE)), int(round(y * SCALE))) for x, y in loop]
        path.append(path[0])
        pc.AddPath(path, pyclipper.PT_SUBJECT, False)
    for s in shapes:
        for loop in s.loops():
            pc.AddPath([(int(round(x * SCALE)), int(round(y * SCALE))) for x, y in loop], pyclipper.PT_CLIP, True)
    tree = pc.Execute2(pyclipper.CT_INTERSECTION, pyclipper.PFT_NONZERO, pyclipper.PFT_EVENODD)
    out = []
    for path in pyclipper.OpenPathsFromPolyTree(tree):
        line = [(x / SCALE, y / SCALE) for x, y in path]
        length = sum(math.dist(line[i], line[i + 1]) for i in range(len(line) - 1))
        if len(line) >= 2 and length > 0.5:  # drop slivers where outlines touch
            out.append(line)
    return out


def contour_stack(mesh: Mesh, inp: ContourInput) -> ContourResult:
    validate(inp)
    axis = AXES[inp.axis.strip().lower()]
    lo, hi = mesh.bbox()
    height = hi[axis] - lo[axis]
    count = max(1, math.ceil(height / inp.thickness - 1e-9))
    if count > MAX_LAYERS:
        raise ContourError(f"{count} layers is too many (limit {MAX_LAYERS}); use thicker layers")
    levels = [lo[axis] + min(inp.thickness * (k + 0.5), (inp.thickness * k + height) / 2) for k in range(count)]
    try:
        sections = [regions_from_loops(section_2d(mesh, axis, v)) for v in levels]
    except GeometryError as exc:
        raise ContourError(str(exc)) from None

    width = max(2, len(str(count)))
    parts: List[Part] = []
    layers3d: Dict[str, List[Loop3]] = {}
    for k, (level, shapes) in enumerate(zip(levels, sections)):
        if not shapes:
            continue
        etch = []
        if inp.etch:
            for n in (k - 1, k + 1):
                if 0 <= n < count:
                    etch += _clip_lines([loop for s in sections[n] for loop in s.loops()], shapes)
        shapes = sorted(shapes, key=lambda s: (min(p[1] for p in s.outer), min(p[0] for p in s.outer)))
        for i, shape in enumerate(shapes):
            pid = f"L{k + 1:0{width}d}" + ("" if len(shapes) == 1 else _suffix(i))
            mine = _clip_lines_for(etch, shape) if len(shapes) > 1 else etch
            layers3d[pid] = [[to_model(axis, level, uv) for uv in loop] for loop in shape.loops()]
            cut_shapes = offset([shape], inp.kerf / 2) if inp.kerf else [shape]
            parts.append(
                Part(
                    id=pid,
                    cut=[loop for s in cut_shapes for loop in s.loops()],
                    etch=mine,
                    label=pid,
                    label_at=interior_point(shape),
                )
            )
    if not parts:
        raise ContourError("No layer cuts the mesh; is it a closed mesh with some volume?")
    return ContourResult(parts=parts, layers3d=layers3d, levels=levels)


def _clip_lines_for(lines, shape: Shape):
    """Keep the etch lines (already clipped to the whole layer) that lie on ``shape``."""
    from .region import contains, offset as grow

    region = grow([shape], 0.01)
    keep = []
    for line in lines:
        mid = line[len(line) // 2] if len(line) > 2 else ((line[0][0] + line[1][0]) / 2, (line[0][1] + line[1][1]) / 2)
        if any(contains(s, mid) for s in region):
            keep.append(line)
    return keep
