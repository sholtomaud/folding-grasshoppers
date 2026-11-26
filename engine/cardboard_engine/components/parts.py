"""Flat parts: what the cutter makes, and the JSON passed between components.

Every geometry component (waffle, contour stack, unfold) produces a
``PartsDoc``; nesting adds a ``Placement`` to each part; export writes it as
DXF or SVG. In Grasshopper the document travels as one text value (the
``Parts`` output), so Hops components can be chained without losing which
line is a cut and which is a score.

Each part is drawn in its own frame (mm). Layers:

- ``cut``: closed loops cut right through (outer outline and holes)
- ``score``: open or closed polylines creased for folding
- ``etch``: marks engraved on the surface (alignment outlines)
- ``label``: one text label, at ``label_at`` (default: the part's centre)
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Tuple

Point2 = Tuple[float, float]
Poly = List[Point2]

SCHEMA = "cardboardcad.parts/1"
DEFAULT_GAP = 5.0  # mm between parts when nothing has placed them


class PartsError(ValueError):
    """Invalid parts document. Hops shows the message as a component error."""


@dataclass(frozen=True)
class Placement:
    """Where nesting put a part.

    The part is rotated about its own origin, then moved so that the lower-left
    corner of its rotated bounding box sits at (x, y) on sheet ``sheet``.
    """

    sheet: int = 0
    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0  # degrees, counter-clockwise


def _rotator(degrees: float):
    a = math.radians(degrees)
    c, s = math.cos(a), math.sin(a)
    if degrees % 90 == 0:  # exact for the common case
        c, s = round(c), round(s)
    return lambda pts: [(c * x - s * y, s * x + c * y) for x, y in pts]


@dataclass
class Part:
    id: str
    cut: List[Poly]
    score: List[Poly] = field(default_factory=list)
    etch: List[Poly] = field(default_factory=list)
    label: Optional[str] = None
    label_at: Optional[Point2] = None
    placement: Optional[Placement] = None

    def all_points(self):
        for group in (self.cut, self.score, self.etch):
            for poly in group:
                yield from poly

    def bbox(self) -> Tuple[Point2, Point2]:
        pts = list(self.all_points())
        if not pts:
            return (0.0, 0.0), (0.0, 0.0)
        xs, ys = zip(*pts)
        return (min(xs), min(ys)), (max(xs), max(ys))

    def label_point(self) -> Point2:
        if self.label_at is not None:
            return self.label_at
        (x0, y0), (x1, y1) = self.bbox()
        return ((x0 + x1) / 2, (y0 + y1) / 2)

    def _mapped(self, f) -> "Part":
        return replace(
            self,
            cut=[f(p) for p in self.cut],
            score=[f(p) for p in self.score],
            etch=[f(p) for p in self.etch],
            label_at=f([self.label_point()])[0],
            placement=None,
        )

    def moved(self, dx: float, dy: float) -> "Part":
        return self._mapped(lambda pts: [(x + dx, y + dy) for x, y in pts])

    def rotated(self, degrees: float) -> "Part":
        return self._mapped(_rotator(degrees))

    def transformed(self, placement: Placement) -> "Part":
        """This part with ``placement`` applied (the result has no placement)."""
        r = self.rotated(placement.rotation) if placement.rotation else self.moved(0, 0)
        (x0, y0), _ = r.bbox()
        return r.moved(placement.x - x0, placement.y - y0)

    # ---- JSON --------------------------------------------------------------
    def to_dict(self) -> dict:
        d: Dict[str, object] = {"id": self.id, "cut": _polys_out(self.cut)}
        if self.score:
            d["score"] = _polys_out(self.score)
        if self.etch:
            d["etch"] = _polys_out(self.etch)
        if self.label is not None:
            d["label"] = self.label
        if self.label_at is not None:
            d["label_at"] = list(self.label_at)
        if self.placement is not None:
            p = self.placement
            d["placement"] = {"sheet": p.sheet, "x": p.x, "y": p.y, "rotation": p.rotation}
        return d

    @classmethod
    def from_dict(cls, d) -> "Part":
        if not isinstance(d, dict):
            raise PartsError("Each part must be an object")
        pid = d.get("id")
        if not isinstance(pid, str) or not pid:
            raise PartsError("Each part needs a non-empty string id")
        cut = _polys_in(d.get("cut", []), pid, "cut", closed=True)
        if not cut:
            raise PartsError(f"Part {pid} has no cut outline")
        label = d.get("label")
        if label is not None and not isinstance(label, str):
            raise PartsError(f"Part {pid}: label must be a string")
        label_at = d.get("label_at")
        if label_at is not None:
            label_at = _point(label_at, pid, "label_at")
        placement = None
        if d.get("placement") is not None:
            p = d["placement"]
            if not isinstance(p, dict):
                raise PartsError(f"Part {pid}: placement must be an object")
            try:
                sheet = p.get("sheet", 0)
                if isinstance(sheet, bool) or not isinstance(sheet, int) or sheet < 0:
                    raise TypeError
                placement = Placement(
                    sheet=sheet,
                    x=_num(p.get("x", 0.0)),
                    y=_num(p.get("y", 0.0)),
                    rotation=_num(p.get("rotation", 0.0)),
                )
            except TypeError:
                raise PartsError(f"Part {pid}: bad placement") from None
        return cls(
            id=pid,
            cut=cut,
            score=_polys_in(d.get("score", []), pid, "score", closed=False),
            etch=_polys_in(d.get("etch", []), pid, "etch", closed=False),
            label=label,
            label_at=label_at,
            placement=placement,
        )


def _num(v) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise TypeError
    return float(v)


def _point(p, pid, what) -> Point2:
    try:
        if len(p) != 2:
            raise TypeError
        return (_num(p[0]), _num(p[1]))
    except TypeError:
        raise PartsError(f"Part {pid}: {what} has a bad point") from None


def _polys_in(raw, pid, what, closed) -> List[Poly]:
    if not isinstance(raw, list):
        raise PartsError(f"Part {pid}: {what} must be a list of polylines")
    out = []
    for poly in raw:
        if not isinstance(poly, list):
            raise PartsError(f"Part {pid}: {what} must be a list of polylines")
        pts = [_point(p, pid, what) for p in poly]
        if len(pts) < (3 if closed else 2):
            raise PartsError(f"Part {pid}: a {what} polyline has too few points")
        out.append(pts)
    return out


def _polys_out(polys) -> list:
    return [[[round(x, 6), round(y, 6)] for x, y in p] for p in polys]


@dataclass
class Sheet:
    """Parts with their placement applied, ready to draw. ``size`` may be None."""

    index: int
    parts: List[Part]
    size: Optional[Tuple[float, float]] = None


@dataclass
class PartsDoc:
    parts: List[Part]
    sheet_size: Optional[Tuple[float, float]] = None  # (width, height) mm, set by nesting
    units: str = "mm"

    def to_json(self) -> str:
        d: Dict[str, object] = {"schema": SCHEMA, "units": self.units, "parts": [p.to_dict() for p in self.parts]}
        if self.sheet_size is not None:
            d["sheet_size"] = list(self.sheet_size)
        return json.dumps(d, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str) -> "PartsDoc":
        if not isinstance(text, str) or not text.strip():
            raise PartsError("Parts input is empty; connect the P output of a CardboardCAD component")
        try:
            d = json.loads(text)
        except ValueError:
            raise PartsError("Parts input is not CardboardCAD parts JSON") from None
        if not isinstance(d, dict) or d.get("schema") != SCHEMA:
            raise PartsError(f"Parts input is not a {SCHEMA} document")
        if d.get("units", "mm") != "mm":
            raise PartsError("Only mm units are supported")
        raw = d.get("parts")
        if not isinstance(raw, list):
            raise PartsError("Parts document has no parts list")
        size = d.get("sheet_size")
        if size is not None:
            try:
                size = (_num(size[0]), _num(size[1]))
            except (TypeError, IndexError, KeyError):
                raise PartsError("sheet_size must be [width, height]") from None
        return cls(parts=[Part.from_dict(p) for p in raw], sheet_size=size)

    @classmethod
    def merge(cls, docs: Sequence["PartsDoc"]) -> "PartsDoc":
        """Concatenate documents, renaming repeated ids (A, A-2, A-3...). Placements are dropped."""
        seen: Dict[str, int] = {}
        out = []
        for doc in docs:
            for p in doc.parts:
                n = seen.get(p.id, 0) + 1
                seen[p.id] = n
                new_id = p.id if n == 1 else f"{p.id}-{n}"
                out.append(replace(p, id=new_id, placement=None))
        return cls(parts=out)

    def layout(self, gap: float = DEFAULT_GAP) -> List[Sheet]:
        """Apply placements. Parts without one go in a row on sheet 0, after any placed parts."""
        by_sheet: Dict[int, List[Part]] = {}
        loose = []
        for p in self.parts:
            if p.placement is None:
                loose.append(p)
            else:
                by_sheet.setdefault(p.placement.sheet, []).append(p.transformed(p.placement))
        if loose:
            row = by_sheet.setdefault(0, [])
            x = 0.0
            if row:
                x = max(q.bbox()[1][0] for q in row) + gap
            for p in loose:
                (x0, y0), (x1, _) = p.bbox()
                row.append(p.moved(x - x0, -y0))
                x += (x1 - x0) + gap
        return [Sheet(index=i, parts=by_sheet[i], size=self.sheet_size) for i in sorted(by_sheet)]
