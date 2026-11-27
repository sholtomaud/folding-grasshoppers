"""A small text-only PDF writer for quotes and invoices (#47).

No dependency: the engine writes PDF 1.4 itself, with the standard Helvetica
and Courier fonts (nothing embedded). Body text is Courier, so columns of
amounts line up by padding. The output has no dates or ids in it, so the same
document always gives the same bytes and a frozen quote's PDF never changes.

``pdf_text`` reads the text back out of a PDF this module wrote (for tests,
and to check a PDF's numbers against its JSON).
"""

from __future__ import annotations

import re
import textwrap
from typing import List, Tuple

PAGE_W, PAGE_H = 595, 842  # A4, points
MARGIN = 50
BODY_SIZE = 10
LINE_H = 14
WRAP = 80  # Courier 10 pt characters across the page
LABEL_W, VALUE_W = 52, 14


def _escape(s: str) -> bytes:
    raw = s.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


class TextPdf:
    """Lines of text, laid out top to bottom, onto as many A4 pages as they need."""

    def __init__(self) -> None:
        self._rows: List[Tuple[str, int, str]] = []  # (font, size, text); size 0 is a blank row

    def heading(self, text: str) -> None:
        self._rows.append(("F1", 16, text))
        self.gap()

    def subheading(self, text: str) -> None:
        self._rows.append(("F1", 11, text))

    def text(self, text: str) -> None:
        for para in str(text).splitlines() or [""]:
            for line in textwrap.wrap(para, WRAP) or [""]:
                self._rows.append(("F2", BODY_SIZE, line))

    def line(self, label: str, value: str, unit: str = "") -> None:
        label = str(label)[:LABEL_W]
        self._rows.append(("F2", BODY_SIZE, f"{label:<{LABEL_W}}{value:>{VALUE_W}} {unit}".rstrip()))

    def gap(self) -> None:
        self._rows.append(("", 0, ""))

    def _pages(self) -> List[bytes]:
        pages, ops, y = [], [], PAGE_H - MARGIN
        for font, size, text in self._rows:
            step = LINE_H if size <= BODY_SIZE else size + 8
            if y - step < MARGIN and ops:
                pages.append(b"\n".join(ops))
                ops, y = [], PAGE_H - MARGIN
            y -= step
            if size:
                ops.append(b"BT /%s %d Tf %d %d Td (%s) Tj ET" % (font.encode(), size, MARGIN, y, _escape(text)))
        pages.append(b"\n".join(ops))
        return pages

    def render(self) -> bytes:
        pages = self._pages()
        n = len(pages)
        # Objects: 1 catalog, 2 pages, 3 Helvetica-Bold, 4 Courier, then (page, content) pairs.
        objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
                b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
                    b" ".join(b"%d 0 R" % (5 + 2 * i) for i in range(n)), n),
                b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
                b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>"]
        for i, content in enumerate(pages):
            objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Resources << /Font << /F1 3 0 R "
                        b"/F2 4 0 R >> >> /Contents %d 0 R >>" % (PAGE_W, PAGE_H, 6 + 2 * i))
            objs.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content) + 1, content))
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for k, body in enumerate(objs, start=1):
            offsets.append(len(out))
            out += b"%d 0 obj\n%s\nendobj\n" % (k, body)
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
        out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
        out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
        return bytes(out)


_TJ = re.compile(rb"\(((?:\\.|[^\\)])*)\) Tj")


def pdf_text(data: bytes) -> str:
    """The text of a PDF written by TextPdf, one line per row."""
    lines = []
    for m in _TJ.finditer(data):
        raw = re.sub(rb"\\(.)", rb"\1", m.group(1))
        lines.append(raw.decode("cp1252", errors="replace"))
    return "\n".join(lines)
