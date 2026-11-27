"""Two-way Rhino link: the design as tagged Rhino objects, and edits coming back.

The Fold Theory Rhino script (``assets/rhino_fold_theory.py``) asks
``/api/rhino/scene`` for the design as a list of objects. Each object has a
stable id (``model``, ``part:X3``, ``assembly:X3``, ``sheet:0``), the Rhino
layer it belongs on and a *revision*: a short hash of its geometry, so the
script only replaces objects that really changed.

Flat parts are drawn where nesting put them, on sheets laid side by side.
Each carries a *frame* (``rotation`` in degrees and an ``offset``) so that

    sheet point = rotate(local point, rotation) + offset

and an edited curve can be mapped back into the part's own frame
(``to_local``). A part's ``shape`` is a fingerprint of its geometry in its
own frame, independent of where it sits on the sheet.

An edited part becomes an :class:`Override`. It remembers the fingerprint of
the generated part it was drawn over; when the method runs again, the
override is applied only while the generated part is still the same shape
(``applied``), and is kept but not used otherwise (``stale``, or
``missing`` when the part is gone). Nothing here is ever silently dropped.

Pure functions only: ``design.py`` holds the state and the lock.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .components.parts import Part, PartsError

LAYER_ROOT = "Fold Theory"
DEFAULT_PROJECT_LAYER = "Design"
SHAPE_DECIMALS = 2  # fingerprints ignore differences under 0.01 mm
MAX_NOTICES = 20
MAX_NOTES = 50

Point2 = Tuple[float, float]


# ---- layers ------------------------------------------------------------------


def layer_root(project: Optional[str]) -> str:
    """``Fold Theory::<project>``. Rhino uses ``::`` between layer levels and refuses ``[ ] :``."""
    name = re.sub(r"[\[\]:]+", " ", project or "")
    name = re.sub(r"\s+", " ", name).strip() or DEFAULT_PROJECT_LAYER
    return f"{LAYER_ROOT}::{name}"


# ---- frames ------------------------------------------------------------------


def _rot(degrees: float):
    a = math.radians(degrees)
    c, s = math.cos(a), math.sin(a)
    if degrees % 90 == 0:
        c, s = round(c), round(s)
    return c, s


def to_sheet(frame: dict, pts: Sequence[Sequence[float]]) -> List[List[float]]:
    c, s = _rot(frame["rotation"])
    ox, oy = frame["offset"]
    return [[c * x - s * y + ox, s * x + c * y + oy] for x, y, *_ in pts]


def to_local(frame: dict, pts: Sequence[Sequence[float]]) -> List[List[float]]:
    c, s = _rot(frame["rotation"])
    ox, oy = frame["offset"]
    out = []
    for x, y, *_ in pts:
        dx, dy = x - ox, y - oy
        out.append([c * dx + s * dy, -s * dx + c * dy])
    return out


def frame_for(local: Part, laid: Part, sheet_origin_x: float, sheet_index: int) -> dict:
    """The frame that takes ``local`` (the part in its own frame) to ``laid`` on the drawn sheet."""
    rotation = float(local.placement.rotation) if local.placement is not None else 0.0
    c, s = _rot(rotation)
    lx, ly = local.cut[0][0]
    px, py = laid.cut[0][0]
    return {"sheet": sheet_index, "rotation": rotation,
            "offset": [px - (c * lx - s * ly) + sheet_origin_x, py - (s * lx + c * ly)]}


def check_frame(frame) -> dict:
    if not isinstance(frame, dict):
        raise PartsError("frame must be the part's frame object from the scene")
    try:
        rotation = _finite(frame.get("rotation"))
        off = frame.get("offset")
        if not isinstance(off, (list, tuple)) or len(off) != 2:
            raise TypeError
        offset = [_finite(off[0]), _finite(off[1])]
    except TypeError:
        raise PartsError("frame needs a rotation (degrees) and an offset [x, y]") from None
    return {"rotation": rotation, "offset": offset}


def _finite(v) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise TypeError
    return float(v)


# ---- fingerprints --------------------------------------------------------------


def _digest(obj) -> str:
    return hashlib.sha1(json.dumps(obj, separators=(",", ":"), sort_keys=True).encode()).hexdigest()[:16]


def _rounded(polys, decimals=SHAPE_DECIMALS):
    # + 0.0 turns -0.0 into 0.0 so the same point always hashes the same.
    return [[[round(c, decimals) + 0.0 for c in p[:2]] for p in poly] for poly in polys]


def shape_of(part: Part) -> str:
    """Fingerprint of a part's geometry in its own frame (not where it sits)."""
    return _digest({"cut": _rounded(part.cut), "score": _rounded(part.score), "etch": _rounded(part.etch)})


def _normalised(part: Part) -> Part:
    (x0, y0), _ = part.bbox()
    return part.moved(-x0, -y0)


def same_shape(a: Part, b: Part) -> bool:
    return shape_of(a) == shape_of(b)


def same_shape_moved(a: Part, b: Part) -> bool:
    """True when ``a`` is ``b`` moved (translated) without being reshaped."""
    return same_shape(_normalised(a), _normalised(b))


def revision(obj: dict) -> str:
    """Revision of a scene object: a hash of everything in it except bookkeeping."""
    return _digest({k: v for k, v in obj.items() if k not in ("revision", "version", "override")})


# ---- overrides -----------------------------------------------------------------


@dataclass
class Override:
    """A flat part reshaped in Rhino. Geometry is in the part's own frame (mm)."""

    part: str
    cut: List[List[Point2]]
    score: List[List[Point2]] = field(default_factory=list)
    etch: List[List[Point2]] = field(default_factory=list)
    base_shape: str = ""  # shape_of() the generated part it was drawn over
    summary: str = ""
    status: str = "applied"  # applied | stale | missing

    def as_part(self, generated: Part) -> Part:
        """The generated part with the Rhino geometry. Label and marks stay; the label moves to the centre."""
        return Part(id=generated.id, cut=[list(map(tuple, p)) for p in self.cut],
                    score=[list(map(tuple, p)) for p in self.score],
                    etch=[list(map(tuple, p)) for p in self.etch],
                    label=generated.label, label_at=None, placement=generated.placement,
                    texts=list(generated.texts))

    def info(self) -> dict:
        return {"part": self.part, "status": self.status, "summary": self.summary}

    def to_dict(self) -> dict:
        r = lambda polys: [[[round(c, 6) for c in p] for p in poly] for poly in polys]  # noqa: E731
        return {"part": self.part, "cut": r(self.cut), "score": r(self.score), "etch": r(self.etch),
                "base_shape": self.base_shape, "summary": self.summary}

    @classmethod
    def from_dict(cls, d) -> "Override":
        if not isinstance(d, dict) or not isinstance(d.get("part"), str) or not d["part"]:
            raise PartsError("Each Rhino override needs a part id")
        p = Part.from_dict({"id": d["part"], "cut": d.get("cut", []), "score": d.get("score", []),
                            "etch": d.get("etch", [])})
        base = d.get("base_shape", "")
        summary = d.get("summary", "")
        if not isinstance(base, str) or not isinstance(summary, str):
            raise PartsError(f"Rhino override for {d['part']}: base_shape and summary must be text")
        return cls(part=p.id, cut=p.cut, score=p.score, etch=p.etch, base_shape=base, summary=summary[:300])


def part_from_edit(pid: str, frame: dict, cut, score, etch) -> Part:
    """The edited curves (sheet coordinates) as a part in its own frame. Raises PartsError."""
    def local(raw, what):
        if not isinstance(raw, list):
            raise PartsError(f"Part {pid}: {what} must be a list of polylines")
        out = []
        for poly in raw:
            if not isinstance(poly, list):
                raise PartsError(f"Part {pid}: {what} must be a list of polylines")
            try:
                out.append(to_local(frame, [[_finite(p[0]), _finite(p[1])] for p in poly]))
            except (TypeError, IndexError, KeyError):
                raise PartsError(f"Part {pid}: {what} has a bad point") from None
        return out

    return Part.from_dict({"id": pid, "cut": local(cut, "cut"), "score": local(score or [], "score"),
                           "etch": local(etch or [], "etch")})


def _loop_length(poly) -> float:
    pts = list(poly) + [poly[0]]
    return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def describe_edit(before: Part, after: Part) -> str:
    """A short, deterministic description of how a part changed: holes and cut length."""
    bits = []
    holes = len(after.cut) - len(before.cut)
    if holes:
        word = "hole" if abs(holes) == 1 else "holes"
        bits.append(f"{abs(holes)} {'more' if holes > 0 else 'fewer'} {word}")
    c0 = sum(_loop_length(p) for p in before.cut)
    c1 = sum(_loop_length(p) for p in after.cut)
    if abs(c1 - c0) >= 0.05:
        bits.append(f"cut length {c0:.0f} to {c1:.0f} mm")
    for what in ("score", "etch"):
        n = len(getattr(after, what)) - len(getattr(before, what))
        if n:
            bits.append(f"{abs(n)} {'more' if n > 0 else 'fewer'} {what} line{'s' if abs(n) != 1 else ''}")
    if not bits:
        bits.append("outline reshaped")
    return ", ".join(bits)


def apply_overrides(parts: List[Part], overrides: Dict[str, Override]) -> Tuple[List[Part], Dict[str, Part], str]:
    """Apply the overrides that still fit. Returns (parts, generated parts by id, a note for Claude)."""
    generated = {p.id: p for p in parts}
    out = []
    for p in parts:
        ov = overrides.get(p.id)
        if ov is not None and ov.base_shape == shape_of(p):
            ov.status = "applied"
            out.append(ov.as_part(p))
        else:
            out.append(p)
    notes = []
    for ov in overrides.values():
        if ov.part not in generated:
            ov.status = "missing"
            notes.append(f"{ov.part} no longer exists, so its Rhino hand edit is not used (it is kept in case "
                         f"{ov.part} comes back).")
        elif ov.base_shape != shape_of(generated[ov.part]):
            ov.status = "stale"
            notes.append(f"The Rhino hand edit of {ov.part} no longer fits (the generated {ov.part} changed), so "
                         f"the generated {ov.part} is used. Tell the designer; they can redo the edit in Rhino.")
        else:
            notes.append(f"Rhino hand edit of {ov.part} kept.")
    return out, generated, " ".join(notes)


def mesh_from_body(raw):
    """The ``mesh`` of a POST body as a Mesh. Raises GeometryError / PartsError."""
    from .components.geometry import Mesh

    if not isinstance(raw, dict):
        raise PartsError("mesh must be {vertices, faces}")
    verts, faces = raw.get("vertices"), raw.get("faces")
    if not isinstance(verts, list) or not isinstance(faces, list):
        raise PartsError("mesh must be {vertices: [[x, y, z]...], faces: [[a, b, c]...]}")
    if any(not isinstance(v, (list, tuple)) for v in verts) or any(not isinstance(f, (list, tuple)) for f in faces):
        raise PartsError("mesh vertices and faces must be lists")
    for f in faces:
        if any(isinstance(i, bool) or not isinstance(i, int) for i in f):
            raise PartsError("mesh faces must be whole-number vertex indices")
    return Mesh.from_lists(verts, faces)
