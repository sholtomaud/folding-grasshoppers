"""Closed meshes for simple shapes, so a design can start from words.

The Design Assistant calls ``make_shape`` when someone asks for "a 300 mm
tall cylinder" and has no model file. Every shape sits on z = 0, is centred
on x = y = 0, and has outward-facing faces. Sizes are in mm: ``width`` along
X, ``depth`` along Y, ``height`` along Z (cylinders, cones and spheres become
elliptical when width and depth differ).
"""

from __future__ import annotations

import math
from typing import List, Tuple

from .geometry import Mesh

SHAPES = ("box", "cylinder", "cone", "sphere", "pyramid")
MIN_SEGMENTS = 3
MAX_SEGMENTS = 256
MAX_SIZE = 100_000.0  # mm


class ShapeError(ValueError):
    """Invalid shape request."""


def _size(name: str, value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ShapeError(f"{name} must be a number of mm")
    v = float(value)
    if not math.isfinite(v) or v <= 0 or v > MAX_SIZE:
        raise ShapeError(f"{name} must be greater than 0 and at most {MAX_SIZE:g} mm")
    return v


def make_shape(shape: str, width=100.0, depth=100.0, height=100.0, segments: int = 32) -> Mesh:
    kind = str(shape or "").strip().lower()
    if kind not in SHAPES:
        raise ShapeError(f"shape must be one of: {', '.join(SHAPES)} (got {shape!r})")
    w, d, h = _size("width", width), _size("depth", depth), _size("height", height)
    if isinstance(segments, bool) or not isinstance(segments, (int, float)) or int(segments) != segments:
        raise ShapeError("segments must be a whole number")
    n = int(segments)
    if not MIN_SEGMENTS <= n <= MAX_SEGMENTS:
        raise ShapeError(f"segments must be between {MIN_SEGMENTS} and {MAX_SEGMENTS}")
    rx, ry = w / 2, d / 2

    if kind == "box":
        return _prism([(-rx, -ry), (rx, -ry), (rx, ry), (-rx, ry)], h)
    if kind == "pyramid":
        return _cone_on([(-rx, -ry), (rx, -ry), (rx, ry), (-rx, ry)], h)
    ring = [(rx * math.cos(2 * math.pi * i / n), ry * math.sin(2 * math.pi * i / n)) for i in range(n)]
    if kind == "cylinder":
        return _prism(ring, h)
    if kind == "cone":
        return _cone_on(ring, h)
    return _ellipsoid(rx, ry, h, n)


def _prism(base: List[Tuple[float, float]], h: float) -> Mesh:
    """Counter-clockwise ``base`` extruded up by ``h``; caps are fans from a centre point."""
    n = len(base)
    verts = [(x, y, 0.0) for x, y in base] + [(x, y, h) for x, y in base]
    verts += [(0.0, 0.0, 0.0), (0.0, 0.0, h)]
    bc, tc = 2 * n, 2 * n + 1
    faces = []
    for i in range(n):
        j = (i + 1) % n
        faces.append((i, j, n + j, n + i))  # side, outward
        faces.append((bc, j, i))  # bottom, facing down
        faces.append((tc, n + i, n + j))  # top, facing up
    return Mesh.from_lists(verts, faces)


def _cone_on(base: List[Tuple[float, float]], h: float) -> Mesh:
    n = len(base)
    verts = [(x, y, 0.0) for x, y in base] + [(0.0, 0.0, 0.0), (0.0, 0.0, h)]
    bc, apex = n, n + 1
    faces = []
    for i in range(n):
        j = (i + 1) % n
        faces.append((i, j, apex))
        faces.append((bc, j, i))
    return Mesh.from_lists(verts, faces)


def _ellipsoid(rx: float, ry: float, h: float, segments: int) -> Mesh:
    rz = h / 2
    rings = max(2, segments // 2)
    verts = [(0.0, 0.0, 0.0)]
    for i in range(1, rings):
        phi = -math.pi / 2 + math.pi * i / rings
        for j in range(segments):
            th = 2 * math.pi * j / segments
            verts.append((rx * math.cos(phi) * math.cos(th), ry * math.cos(phi) * math.sin(th), rz + rz * math.sin(phi)))
    verts.append((0.0, 0.0, h))
    top = len(verts) - 1

    def at(i, j):
        return 1 + (i - 1) * segments + (j % segments)

    faces = []
    for j in range(segments):
        faces.append((0, at(1, j + 1), at(1, j)))
        faces.append((top, at(rings - 1, j), at(rings - 1, j + 1)))
    for i in range(1, rings - 1):
        for j in range(segments):
            faces.append((at(i, j), at(i, j + 1), at(i + 1, j + 1), at(i + 1, j)))
    return Mesh.from_lists(verts, faces)
