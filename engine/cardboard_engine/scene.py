"""Scene (#43): several named objects in one design.

A stand is a counter plus stools plus a back wall. Each object has its own
model, position and construction method, and its own parts. The design
session keeps the *active* object's state in its usual attributes (so every
single-object tool works unchanged) and the other objects' state here.

Part ids and labels get the object's prefix when the scene's parts are put
together for nesting, costing and export: ``C-X1``, ``S1-X1``. A design with
no objects added has no prefix, exactly as before scenes existed.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .components.geometry import Mesh
from .components.parts import PartsDoc

# The per-object attributes of DesignSession (everything else is scene-wide).
OBJECT_ATTRS = ("model", "model_name", "method", "method_summary", "parts", "parts3d", "generated",
                "board_thickness", "overrides", "joint_counts", "cut_edges", "applied_ops")
MAX_OBJECTS = 50
MAX_NAME = 60
_PREFIX = re.compile(r"^[A-Z][A-Z0-9]{0,5}$")


class SceneError(ValueError):
    """A scene request that cannot be done. The message is for Claude and the designer."""


def blank_state() -> Dict[str, Any]:
    return {"model": None, "model_name": None, "method": None, "method_summary": None, "parts": None,
            "parts3d": {}, "generated": {}, "board_thickness": 0.0, "overrides": {},
            "joint_counts": {}, "cut_edges": [], "applied_ops": []}


def copy_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """A copy another object can own. Meshes and parts are never changed in place, so sharing them is safe."""
    out = dict(state)
    for key in ("parts3d", "generated", "overrides", "joint_counts"):
        out[key] = dict(state.get(key) or {})
    out["cut_edges"] = list(state.get("cut_edges") or [])
    out["applied_ops"] = [dict(e) for e in state.get("applied_ops") or []]
    return out


def clean_name(raw) -> str:
    if not isinstance(raw, str):
        raise SceneError("The object name must be text")
    s = re.sub(r"\s+", " ", raw.replace("::", " ")).strip()[:MAX_NAME].strip()
    if not s:
        raise SceneError("Give the object a name, for example \"counter\" or \"stool 1\"")
    return s


def make_prefix(name: str, taken: Iterable[str]) -> str:
    """A short label prefix from the name: "counter" -> C, "stool 1" -> S1, "back wall" -> BW."""
    taken = {t.upper() for t in taken}
    words = re.findall(r"[A-Za-z]+", name)
    number = re.search(r"(\d+)\s*$", name)
    letters = "".join(w[0] for w in words[:3]).upper() or "O"
    base = (letters + (number.group(1) if number else ""))[:6]
    if base not in taken:
        return base
    n = 2
    while f"{base[:5]}{n}" in taken:
        n += 1
    return f"{base[:5]}{n}"


def check_prefix(raw, taken: Iterable[str]) -> str:
    if not isinstance(raw, str):
        raise SceneError("The prefix must be text")
    p = raw.strip().upper()
    if not _PREFIX.match(p):
        raise SceneError("A prefix is 1 to 6 letters or digits starting with a letter, for example C or S1")
    if p in {t.upper() for t in taken}:
        raise SceneError(f"Another object already uses the prefix {p}")
    return p


def check_position(x, y, z) -> List[float]:
    return [float(x), float(y), float(z)]


def prefixed_parts(prefix: str, parts: Sequence) -> list:
    """The object's parts with its prefix on their ids and labels, and no sheet placement yet."""
    out = []
    for p in parts:
        label = f"{prefix}-{p.label}" if p.label else None
        out.append(dataclasses.replace(p, id=f"{prefix}-{p.id}", label=label, placement=None))
    return out


def combine(objects: Iterable[Tuple[str, Optional[PartsDoc]]]) -> Optional[PartsDoc]:
    """One parts document for the scene, from (prefix, parts) of each object. None if no object has parts."""
    parts: list = []
    any_parts = False
    for prefix, doc in objects:
        if doc is None:
            continue
        any_parts = True
        parts += prefixed_parts(prefix, doc.parts)
    return PartsDoc(parts=parts) if any_parts else None


def moved(mesh: Mesh, position: Sequence[float]) -> Mesh:
    dx, dy, dz = position
    if not (dx or dy or dz):
        return mesh
    return Mesh(vertices=tuple((x + dx, y + dy, z + dz) for x, y, z in mesh.vertices), faces=mesh.faces)


def moved_loops(loops, position: Sequence[float]):
    dx, dy, dz = position
    if not (dx or dy or dz):
        return loops
    return [[(p[0] + dx, p[1] + dy, p[2] + dz) for p in loop] for loop in loops]
