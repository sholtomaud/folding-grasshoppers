"""DXF and SVG export of laid-out sheets.

DXF is ASCII R12 (AC1009): POLYLINE/VERTEX and TEXT only, no handles. It is
the dialect every laser and knife-cutter program reads (LightBurn, RDWorks,
Zünd Cut Center, Illustrator, Rhino). SVG uses real millimetre sizes, y up
like the model.

Layers, with the usual cutter colours:

========  =========  ==========================
Layer     DXF colour SVG stroke
========  =========  ==========================
CUT       1 red      #ff0000
SCORE     5 blue     #0000ff
ETCH      3 green    #00a000
LABEL     7 black    text, #000000
SHEET     8 grey     #999999 (sheet outline)
========  =========  ==========================

Files are written only under the user's home folder (default
``~/Documents/CardboardCAD``), with a sanitised name and a forced extension:
any local process can call the engine, so the path must not be free-form.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape, quoteattr

from .parts import Part, PartsDoc, Sheet

Point2 = Tuple[float, float]

LAYERS = (
    # name, DXF colour, SVG colour
    ("CUT", 1, "#ff0000"),
    ("SCORE", 5, "#0000ff"),
    ("ETCH", 3, "#00a000"),
    ("LABEL", 7, "#000000"),
    ("SHEET", 8, "#999999"),
)
FORMATS = ("dxf", "svg")
DEFAULT_NAME = "cardboardcad"
MAX_TEXT_HEIGHT = 5.0  # mm
SVG_MARGIN = 5.0  # mm around content when the sheet size is unknown


class ExportError(ValueError):
    """Invalid export request. Hops shows the message as a component error."""


def _fmt(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _text_height(part: Part) -> float:
    (x0, y0), (x1, y1) = part.bbox()
    return max(0.5, min(MAX_TEXT_HEIGHT, (y1 - y0) / 4, (x1 - x0) / max(1, len(part.label or "")) / 0.8))


def _sheet_rect(sheet: Sheet) -> Optional[List[Point2]]:
    if sheet.size is None:
        return None
    w, h = sheet.size
    return [(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)]


# ---- DXF -------------------------------------------------------------------


def to_dxf(sheet: Sheet) -> str:
    out: List[str] = []

    def g(code, value):
        out.append(str(code))
        out.append(value if isinstance(value, str) else _fmt(value) if isinstance(value, float) else str(value))

    g(0, "SECTION"); g(2, "HEADER")
    g(9, "$ACADVER"); g(1, "AC1009")
    g(9, "$INSUNITS"); g(70, 4)  # millimetres
    g(9, "$MEASUREMENT"); g(70, 1)  # metric
    g(0, "ENDSEC")

    g(0, "SECTION"); g(2, "TABLES")
    g(0, "TABLE"); g(2, "LAYER"); g(70, len(LAYERS) + 1)
    for name, colour in [("0", 7)] + [(n, c) for n, c, _ in LAYERS]:
        g(0, "LAYER"); g(2, name); g(70, 0); g(62, colour); g(6, "CONTINUOUS")
    g(0, "ENDTAB")
    g(0, "ENDSEC")

    def polyline(layer: str, pts: Sequence[Point2], closed: bool):
        g(0, "POLYLINE"); g(8, layer); g(66, 1)
        g(10, 0.0); g(20, 0.0); g(30, 0.0)
        g(70, 1 if closed else 0)
        for x, y in pts:
            g(0, "VERTEX"); g(8, layer); g(10, float(x)); g(20, float(y)); g(30, 0.0)
        g(0, "SEQEND"); g(8, layer)

    g(0, "SECTION"); g(2, "ENTITIES")
    rect = _sheet_rect(sheet)
    if rect:
        polyline("SHEET", rect, True)
    for part in sheet.parts:
        for loop in part.cut:
            polyline("CUT", loop, True)
        for poly in part.score:
            polyline("SCORE", poly, _is_closed(poly))
        for poly in part.etch:
            polyline("ETCH", poly, _is_closed(poly))
        if part.label:
            x, y = part.label_point()
            g(0, "TEXT"); g(8, "LABEL")
            g(10, float(x)); g(20, float(y)); g(30, 0.0)
            g(40, float(_text_height(part)))
            g(1, _dxf_text(part.label))
            g(72, 1)  # centre
            g(11, float(x)); g(21, float(y)); g(31, 0.0)
            g(73, 2)  # middle
        for t in part.texts:
            x, y = t.at
            g(0, "TEXT"); g(8, "LABEL")
            g(10, float(x)); g(20, float(y)); g(30, 0.0)
            g(40, float(t.size))
            g(1, _dxf_text(t.text))
            g(72, 1)
            g(11, float(x)); g(21, float(y)); g(31, 0.0)
            g(73, 2)
    g(0, "ENDSEC")
    g(0, "EOF")
    return "\n".join(out) + "\n"


def _dxf_text(s: str) -> str:
    # One line, printable ASCII; DXF R12 has no reliable Unicode.
    return "".join(c if 32 <= ord(c) < 127 else "?" for c in s)[:250]


def _is_closed(poly: Sequence[Point2]) -> bool:
    return len(poly) > 2 and tuple(poly[0]) == tuple(poly[-1])


# ---- SVG -------------------------------------------------------------------


def to_svg(sheet: Sheet) -> str:
    rect = _sheet_rect(sheet)
    if rect:
        x0, y0, x1, y1 = 0.0, 0.0, sheet.size[0], sheet.size[1]
    else:
        boxes = [p.bbox() for p in sheet.parts] or [((0.0, 0.0), (0.0, 0.0))]
        x0 = min(b[0][0] for b in boxes) - SVG_MARGIN
        y0 = min(b[0][1] for b in boxes) - SVG_MARGIN
        x1 = max(b[1][0] for b in boxes) + SVG_MARGIN
        y1 = max(b[1][1] for b in boxes) + SVG_MARGIN
    w, h = x1 - x0, y1 - y0

    def xy(p):  # model (y up) -> SVG (y down)
        return f"{_fmt(p[0] - x0)} {_fmt(y1 - p[1])}"

    def path(pts, closed):
        d = "M " + " L ".join(xy(p) for p in pts) + (" Z" if closed else "")
        return f'<path d="{d}"/>'

    colour = {n: c for n, _, c in LAYERS}
    groups = {"SHEET": [], "CUT": [], "SCORE": [], "ETCH": [], "LABEL": []}
    if rect:
        groups["SHEET"].append(path(rect, True))
    for part in sheet.parts:
        groups["CUT"] += [path(loop, True) for loop in part.cut]
        groups["SCORE"] += [path(p, _is_closed(p)) for p in part.score]
        groups["ETCH"] += [path(p, _is_closed(p)) for p in part.etch]
        if part.label:
            lx, ly = xy(part.label_point()).split()
            groups["LABEL"].append(
                f'<text x="{lx}" y="{ly}" font-size="{_fmt(_text_height(part))}" '
                f'text-anchor="middle" dominant-baseline="middle">{escape(part.label)}</text>'
            )
        for t in part.texts:
            tx, ty = xy(t.at).split()
            groups["LABEL"].append(
                f'<text x="{tx}" y="{ty}" font-size="{_fmt(t.size)}" '
                f'text-anchor="middle" dominant-baseline="middle">{escape(t.text)}</text>'
            )

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_fmt(w)}mm" height="{_fmt(h)}mm" '
        f'viewBox="0 0 {_fmt(w)} {_fmt(h)}">',
    ]
    for name in ("SHEET", "CUT", "SCORE", "ETCH", "LABEL"):
        if name == "LABEL":
            style = f'fill="{colour[name]}" font-family="Helvetica, Arial, sans-serif"'
        else:
            style = f'fill="none" stroke="{colour[name]}" stroke-width="0.1"'
        lines.append(f"<g id={quoteattr(name)} {style}>")
        lines.extend(groups[name])
        lines.append("</g>")
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


# ---- files -----------------------------------------------------------------


def safe_file_stem(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", name or "").strip("-_")[:80]
    return stem or DEFAULT_NAME


def resolve_export_dir(folder: str, home: str) -> str:
    """The folder to write into: ``folder`` (inside ``home``) or ~/Documents/CardboardCAD."""
    home_real = os.path.realpath(home)
    if not folder or not folder.strip():
        return os.path.join(home, "Documents", "CardboardCAD")
    f = folder.strip()
    if f == "~" or f.startswith("~/"):
        f = os.path.join(home, f[2:])
    if not os.path.isabs(f):
        raise ExportError("Folder must be an absolute path (or start with ~/)")
    real = os.path.realpath(f)
    if real != home_real and not real.startswith(home_real + os.sep):
        raise ExportError(f"Folder must be inside your home folder ({home})")
    return real


@dataclass
class ExportResult:
    texts: List[str]
    paths: List[str] = field(default_factory=list)


def export_files(doc: PartsDoc, fmt: str, name: str, folder: str, write: bool, home: str) -> ExportResult:
    fmt = (fmt or "").strip().lower().lstrip(".")
    if fmt not in FORMATS:
        raise ExportError(f"Format must be one of: {', '.join(FORMATS)} (got {fmt!r})")
    if not doc.parts:
        raise ExportError("Nothing to export: the parts list is empty")
    render = to_dxf if fmt == "dxf" else to_svg
    sheets = doc.layout()
    texts = [render(s) for s in sheets]
    result = ExportResult(texts=texts)
    if not write:
        return result
    directory = resolve_export_dir(folder, home)
    os.makedirs(directory, exist_ok=True)
    stem = safe_file_stem(name)
    for sheet, text in zip(sheets, texts):
        path = os.path.join(directory, f"{stem}-sheet{sheet.index + 1}.{fmt}")
        fd, tmp = tempfile.mkstemp(prefix=".cardboardcad-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        result.paths.append(path)
    return result
