"""Read models exported from Rhino (or anything else) as OBJ or STL.

Units are taken as mm; ``scale_mesh`` fixes a model drawn in other units.
Any local process can reach the engine, so files are only read from inside
the home folder, with a size limit, and only .obj / .stl.
"""

from __future__ import annotations

import math
import os
import struct
from typing import Dict, List, Tuple

from .geometry import GeometryError, Mesh

MAX_FILE_BYTES = 200 * 1024 * 1024
EXTENSIONS = (".stl", ".obj")


class MeshFileError(ValueError):
    """A model file that cannot be read."""


def _mesh(vertices, faces) -> Mesh:
    try:
        return Mesh.from_lists(vertices, faces)
    except GeometryError as exc:
        raise MeshFileError(str(exc)) from None


# ---- OBJ -------------------------------------------------------------------


def parse_obj(text: str) -> Mesh:
    verts: List[Tuple[float, float, float]] = []
    faces: List[Tuple[int, ...]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        parts = raw.split("#", 1)[0].split()
        if not parts:
            continue
        try:
            if parts[0] == "v":
                x, y, z = (float(c) for c in parts[1:4])
                verts.append((x, y, z))
            elif parts[0] == "f":
                idx = []
                for item in parts[1:]:
                    i = int(item.split("/")[0])
                    i = i - 1 if i > 0 else len(verts) + i
                    if not 0 <= i < len(verts):
                        raise IndexError
                    idx.append(i)
                if len(idx) < 3:
                    raise ValueError
                if len(idx) <= 4:
                    faces.append(tuple(idx))
                else:
                    faces += [(idx[0], idx[k], idx[k + 1]) for k in range(1, len(idx) - 1)]
        except (ValueError, IndexError):
            raise MeshFileError(f"OBJ line {lineno} is not a valid vertex or face: {raw.strip()[:60]!r}") from None
    if not faces:
        raise MeshFileError("The OBJ file has no faces (export it as a mesh, not curves or points)")
    return _mesh(verts, faces)


# ---- STL -------------------------------------------------------------------


def _welded(triangles) -> Mesh:
    index: Dict[Tuple[float, float, float], int] = {}
    verts: List[Tuple[float, float, float]] = []
    faces = []
    for tri in triangles:
        f = []
        for p in tri:
            if not all(math.isfinite(c) for c in p):
                raise MeshFileError("The STL file has a vertex that is not a finite number")
            i = index.get(p)
            if i is None:
                i = index[p] = len(verts)
                verts.append(p)
            f.append(i)
        if len(set(f)) == 3:
            faces.append(tuple(f))
    if not faces:
        raise MeshFileError("The STL file has no triangles")
    return _mesh(verts, faces)


def parse_stl(data: bytes) -> Mesh:
    if len(data) >= 84:
        (count,) = struct.unpack_from("<I", data, 80)
        if len(data) == 84 + 50 * count:  # binary, even when the header says "solid"
            tris = []
            for k in range(count):
                vals = struct.unpack_from("<12f", data, 84 + 50 * k)
                tris.append(tuple((vals[3 + 3 * i], vals[4 + 3 * i], vals[5 + 3 * i]) for i in range(3)))
            return _welded(tris)
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError:
        raise MeshFileError("This is not an STL file (neither binary nor ASCII STL)") from None
    if not text.lstrip().lower().startswith("solid"):
        raise MeshFileError("This is not an STL file (neither binary nor ASCII STL)")
    tris, current = [], []
    for raw in text.splitlines():
        parts = raw.split()
        if parts and parts[0].lower() == "vertex":
            try:
                current.append(tuple(float(c) for c in parts[1:4]))
            except ValueError:
                raise MeshFileError(f"Bad STL vertex line: {raw.strip()[:60]!r}") from None
        elif parts and parts[0].lower() == "endloop":
            if len(current) != 3:
                raise MeshFileError("STL facets must be triangles")
            tris.append(tuple(current))
            current = []
    return _welded(tris)


# ---- files -----------------------------------------------------------------


def load_mesh_file(path: str, home: str) -> Mesh:
    p = (path or "").strip()
    if p == "~" or p.startswith("~/"):
        p = os.path.join(home, p[2:])
    if not os.path.isabs(p):
        raise MeshFileError("The model path must be absolute (or start with ~/)")
    home_real = os.path.realpath(home)
    real = os.path.realpath(p)
    if not real.startswith(home_real + os.sep):
        raise MeshFileError(f"Model files must be inside your home folder ({home})")
    ext = os.path.splitext(real)[1].lower()
    if ext not in EXTENSIONS:
        raise MeshFileError("Choose a .stl or .obj file (in Rhino: File > Export Selected)")
    if not os.path.isfile(real):
        raise MeshFileError(f"File not found: {p}")
    if os.path.getsize(real) > MAX_FILE_BYTES:
        raise MeshFileError(f"The file is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
    with open(real, "rb") as fh:
        data = fh.read()
    if ext == ".stl":
        return parse_stl(data)
    return parse_obj(data.decode("utf-8", errors="replace"))


def scale_mesh(mesh: Mesh, factor: float) -> Mesh:
    """Scale about the centre of the model's base (so it stays on the ground)."""
    if isinstance(factor, bool) or not isinstance(factor, (int, float)) or not math.isfinite(factor) or factor <= 0:
        raise MeshFileError("Scale factor must be a number greater than 0")
    (lx, ly, lz), (hx, hy, _) = mesh.bbox()
    cx, cy = (lx + hx) / 2, (ly + hy) / 2
    verts = [(cx + (x - cx) * factor, cy + (y - cy) * factor, lz + (z - lz) * factor) for x, y, z in mesh.vertices]
    return Mesh(tuple(verts), mesh.faces)


def to_obj(mesh: Mesh, name: str = "") -> str:
    """Write a mesh as OBJ text. Coordinates use ``repr`` so they read back exactly."""
    lines = ["# Fold Theory model, units mm"]
    if name:
        lines.append("o " + "".join(c if c.isprintable() and c not in "\r\n" else "_" for c in name))
    lines += [f"v {x!r} {y!r} {z!r}" for x, y, z in mesh.vertices]
    lines += ["f " + " ".join(str(i + 1) for i in face) for face in mesh.faces]
    return "\n".join(lines) + "\n"
