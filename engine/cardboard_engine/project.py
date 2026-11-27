"""Project folders: a saved design that can be opened again.

A project is a plain folder, so it can be zipped, mailed, or synced with
Google Drive or OneDrive as it is::

    ~/Documents/Fold Theory/Projects/<name>/
        project.json            manifest (schema cardboardcad.project/1)
        chat.json               the Design Assistant conversation (written by the app)
        model/model.obj         the 3D model, in mm
        parts/parts.json        cardboardcad.parts/1, with sheet placements
        cut-files/<stem>-sheet<N>.dxf and .svg
        estimate/estimate.json and estimate.csv
        preview/model.svg       a picture of the design

``project.json`` keeps a *recipe*: the method, nesting and estimate inputs.
Opening a project loads ``model/model.obj`` and runs the recipe again, which
gives the same parts and sheets (every step is deterministic). The other
files are outputs for people and machines; they are rewritten on every save.

Files and folders the designer adds (inspiration images, notes) are left
alone. Only files listed in the previous manifest are ever removed.

Any local process can reach the engine, so projects are read and written
only inside the home folder, and a folder that is not empty is only written
to when it already holds a ``project.json``.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .brand import documents_dir

MANIFEST = "project.json"
CHAT = "chat.json"
PROJECT_SCHEMA = "cardboardcad.project/1"
MODEL_FILE = "model/model.obj"
MAX_MANIFEST_BYTES = 10 * 1024 * 1024
MAX_CHAT_BYTES = 50 * 1024 * 1024
MAX_MODEL_BYTES = 200 * 1024 * 1024
MAX_NAME = 100
MAX_SVG_TRIANGLES = 40_000


class ProjectError(ValueError):
    """A project that cannot be saved or opened. The message is for the designer."""


# ---- names and folders -----------------------------------------------------


def default_projects_dir(home: str) -> str:
    return os.path.join(documents_dir(home), "Projects")


def safe_project_name(raw) -> str:
    """A folder name that works on macOS, Windows, Google Drive and OneDrive."""
    if not isinstance(raw, str):
        raise ProjectError("The project name must be text")
    s = re.sub(r"[/\\:|]+", " - ", raw)
    s = re.sub(r"[^\w \-().,&'+]", "", s)
    s = re.sub(r"\s+", " ", s)
    pieces = [p.strip() for p in s.split(" - ")]
    pieces = [p for p in pieces if p.strip(" .-_")]
    s = " - ".join(pieces).strip(" .-_")[:MAX_NAME].strip(" .-_")
    if not s:
        raise ProjectError("Give the project a name (letters or numbers)")
    return s


def _inside_home(path: str, home: str, what: str) -> str:
    p = (path or "").strip()
    if p == "~" or p.startswith("~/"):
        p = os.path.join(home, p[2:])
    if not os.path.isabs(p):
        raise ProjectError(f"The {what} must be an absolute path (or start with ~/)")
    home_real = os.path.realpath(home)
    real = os.path.realpath(p)
    if real != home_real and not real.startswith(home_real + os.sep):
        raise ProjectError(f"The {what} must be inside your home folder ({home})")
    return real


def resolve_save_dir(folder: str, name: str, home: str) -> Tuple[str, str]:
    """(folder, name) to save into. No folder: ~/Documents/Fold Theory/Projects/<name>."""
    if folder and folder.strip():
        directory = _inside_home(folder, home, "project folder")
        clean = safe_project_name(name) if name and name.strip() else safe_project_name(os.path.basename(directory))
        return directory, clean
    clean = safe_project_name(name)
    return os.path.join(default_projects_dir(home), clean), clean


def resolve_open_dir(path: str, home: str) -> str:
    real = _inside_home(path, home, "project folder")
    if os.path.basename(real) == MANIFEST and os.path.isfile(real):
        real = os.path.dirname(real)
    if not os.path.isfile(os.path.join(real, MANIFEST)):
        raise ProjectError(f"{path} is not a Fold Theory project (it has no {MANIFEST})")
    return real


def _safe_rel(rel) -> Optional[str]:
    """A relative path inside the project, or None."""
    if not isinstance(rel, str) or not rel or rel.startswith(("/", "\\")):
        return None
    parts = rel.replace("\\", "/").split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    return "/".join(parts)


# ---- reading and writing ---------------------------------------------------


def _write_atomic(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".cardboardcad-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _read_text(path: str, limit: int, what: str) -> str:
    if not os.path.isfile(path):
        raise ProjectError(f"The project is missing {what}")
    if os.path.getsize(path) > limit:
        raise ProjectError(f"{what} is larger than {limit // (1024 * 1024)} MB")
    with open(path, "rb") as fh:
        return fh.read().decode("utf-8", errors="replace")


def read_manifest(directory: str) -> dict:
    text = _read_text(os.path.join(directory, MANIFEST), MAX_MANIFEST_BYTES, MANIFEST)
    try:
        m = json.loads(text)
    except ValueError:
        raise ProjectError(f"{MANIFEST} is not valid JSON") from None
    if not isinstance(m, dict) or m.get("schema") != PROJECT_SCHEMA:
        raise ProjectError(f"{MANIFEST} is not a {PROJECT_SCHEMA} manifest")
    return m


def check_writable(directory: str) -> Optional[dict]:
    """The folder's existing manifest (or None). Refuses folders that are someone else's."""
    if os.path.exists(directory) and not os.path.isdir(directory):
        raise ProjectError(f"{directory} is a file, not a folder")
    if not os.path.isdir(directory) or not os.listdir(directory):
        return None
    if not os.path.isfile(os.path.join(directory, MANIFEST)):
        raise ProjectError(
            f"{directory} is not a Fold Theory project and is not empty; choose an empty folder or a new name")
    try:
        return read_manifest(directory)
    except ProjectError:
        return None  # a broken manifest of ours: overwrite it


def read_chat(directory: str):
    path = os.path.join(directory, CHAT)
    if not os.path.isfile(path):
        return None
    try:
        return json.loads(_read_text(path, MAX_CHAT_BYTES, CHAT))
    except ValueError:
        return None


def write_files(directory: str, files: Dict[str, str], previous: Optional[dict]) -> List[str]:
    """Write ``files`` (relative path -> text), the manifest last; remove stale files of ours."""
    keep = set(files) | {CHAT}
    old = previous.get("files", []) if isinstance(previous, dict) else []
    for rel in old if isinstance(old, list) else []:
        rel = _safe_rel(rel)
        if rel is None or rel in keep:
            continue
        path = os.path.join(directory, rel)
        if os.path.isfile(path) and not os.path.islink(path):
            os.unlink(path)
            parent = os.path.dirname(path)
            if parent != directory and os.path.isdir(parent) and not os.listdir(parent):
                os.rmdir(parent)
    for rel in sorted(files, key=lambda r: r == MANIFEST):
        _write_atomic(os.path.join(directory, rel), files[rel])
    return sorted(files)


# ---- estimate as a spreadsheet --------------------------------------------

_ESTIMATE_UNITS = {
    "cut_length_mm": ("cut_length", "mm"),
    "score_length_mm": ("score_length", "mm"),
    "pierce_count": ("pierce_count", "count"),
    "sheet_count": ("sheet_count", "count"),
    "time_min": ("time", "min"),
    "material_cost": ("material_cost", "currency"),
    "machine_cost": ("machine_cost", "currency"),
    "total_cost": ("total_cost", "currency"),
}


def estimate_csv(est: dict) -> str:
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["item", "value", "unit"])
    for key, value in est.items():
        name, unit = _ESTIMATE_UNITS.get(key, (key, ""))
        if isinstance(value, float):
            value = round(value, 4)
        w.writerow([name, value, unit])
    return buf.getvalue()


# ---- preview picture -------------------------------------------------------

_COS30 = math.cos(math.radians(30))


def model_svg(triangles: Sequence[Sequence[Sequence[float]]], size: int = 800) -> str:
    """An isometric picture of the triangles, shaded like kraft board (painter's algorithm)."""
    tris = list(triangles)
    if len(tris) > MAX_SVG_TRIANGLES:
        step = math.ceil(len(tris) / MAX_SVG_TRIANGLES)
        tris = tris[::step]
    light = (-0.45, -0.65, 0.62)
    drawn = []
    for t in tris:
        (ax, ay, az), (bx, by, bz), (cx, cy, cz) = t[0], t[1], t[2]
        ux, uy, uz = bx - ax, by - ay, bz - az
        vx, vy, vz = cx - ax, cy - ay, cz - az
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        n = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
        shade = abs(nx * light[0] + ny * light[1] + nz * light[2]) / n
        depth = sum(-p[0] - p[1] + p[2] for p in t[:3]) / 3  # towards a viewer at (-1, -1, 1)
        pts = [((p[0] - p[1]) * _COS30, -((p[0] + p[1]) * 0.5 + p[2])) for p in t[:3]]
        drawn.append((depth, shade, pts))
    drawn.sort(key=lambda d: d[0])
    if drawn:
        xs = [x for _, _, pts in drawn for x, _ in pts]
        ys = [y for _, _, pts in drawn for _, y in pts]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    else:
        x0, x1, y0, y1 = 0.0, 1.0, 0.0, 1.0
    pad = 0.05 * max(x1 - x0, y1 - y0, 1e-6)
    vb = (x0 - pad, y0 - pad, (x1 - x0) + 2 * pad, (y1 - y0) + 2 * pad)
    scale = size / max(vb[2], vb[3])
    w, h = max(1, round(vb[2] * scale)), max(1, round(vb[3] * scale))
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="{vb[0]:.3f} {vb[1]:.3f} {vb[2]:.3f} {vb[3]:.3f}">',
        f'<rect x="{vb[0]:.3f}" y="{vb[1]:.3f}" width="{vb[2]:.3f}" height="{vb[3]:.3f}" fill="#f7f4ee"/>',
    ]
    stroke = max(vb[2], vb[3]) / 1500
    for _, shade, pts in drawn:
        k = 0.55 + 0.45 * shade
        r, g, b = round(196 * k), round(154 * k), round(108 * k)
        p = " ".join(f"{x:.2f},{y:.2f}" for x, y in pts)
        out.append(f'<polygon points="{p}" fill="rgb({r},{g},{b})" stroke="rgb({r},{g},{b})" '
                   f'stroke-width="{stroke:.3f}" stroke-linejoin="round"/>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def flatten(items: Iterable[Iterable]) -> list:
    return [x for it in items for x in it]
