"""Parts documents (the JSON passed between components) and DXF/SVG export."""

import io
import json
import os
import xml.etree.ElementTree as ET

import ezdxf
import pytest

from cardboard_engine.components.export import (
    ExportError,
    export_files,
    resolve_export_dir,
    safe_file_stem,
    to_dxf,
    to_svg,
)
from cardboard_engine.components.parts import Part, PartsDoc, PartsError, Placement

SQUARE = [(0, 0), (50, 0), (50, 30), (0, 30)]
HOLE = [(10, 10), (20, 10), (20, 20), (10, 20)]


def doc(**kw):
    return PartsDoc(
        parts=[
            Part(id="A", cut=[SQUARE, HOLE], score=[[(25, 0), (25, 30)]], label="A"),
            Part(id="B", cut=[[(0, 0), (20, 0), (10, 15)]], label="B"),
        ],
        **kw,
    )


# ---- parts document --------------------------------------------------------


def test_parts_json_round_trip():
    d = doc()
    d.parts[1] = Part(id="B", cut=d.parts[1].cut, placement=Placement(sheet=1, x=5, y=6, rotation=90))
    back = PartsDoc.from_json(d.to_json())
    assert back == d
    assert json.loads(d.to_json())["schema"] == "cardboardcad.parts/1"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json",
        "[]",
        '{"schema": "other/1", "parts": []}',
        '{"schema": "cardboardcad.parts/1", "parts": [{"id": "A", "cut": [[[0, 0], [1, 0]]]}]}',
        '{"schema": "cardboardcad.parts/1", "parts": [{"id": "A", "cut": [[[0, 0], [1, "x"], [0, 1]]]}]}',
    ],
)
def test_parts_json_rejects_bad_documents(text):
    with pytest.raises(PartsError):
        PartsDoc.from_json(text)


def test_merge_documents():
    a, b = doc(), doc()
    merged = PartsDoc.merge([a, b])
    assert [p.id for p in merged.parts] == ["A", "B", "A-2", "B-2"]


# ---- DXF -------------------------------------------------------------------


def read_dxf(text):
    return ezdxf.read(io.StringIO(text))


def test_dxf_reads_back_with_ezdxf():
    dwg = read_dxf(to_dxf(doc().layout()[0]))
    msp = dwg.modelspace()
    cut = [e for e in msp if e.dxf.layer == "CUT"]
    score = [e for e in msp if e.dxf.layer == "SCORE"]
    labels = [e for e in msp if e.dxftype() == "TEXT"]
    assert len(cut) == 3  # square, hole, triangle
    assert all(e.is_closed for e in cut)
    assert len(score) == 1 and not score[0].is_closed
    assert sorted(t.dxf.text for t in labels) == ["A", "B"]
    assert {"CUT", "SCORE", "ETCH", "LABEL"} <= {layer.dxf.name for layer in dwg.layers}


def test_dxf_units_are_mm():
    dwg = read_dxf(to_dxf(doc().layout()[0]))
    assert dwg.header["$INSUNITS"] == 4


def test_layout_keeps_parts_apart():
    sheet = doc().layout(gap=5)[0]
    boxes = [p.bbox() for p in sheet.parts]
    (a0, a1), (b0, b1) = boxes
    assert b0[0] >= a1[0] + 5 - 1e-9  # B to the right of A


def test_placement_rotates_and_moves():
    d = PartsDoc(parts=[Part(id="A", cut=[SQUARE], placement=Placement(sheet=0, x=100, y=0, rotation=90))])
    placed = d.layout()[0].parts[0]
    (x0, y0), (x1, y1) = placed.bbox()
    assert (x1 - x0, y1 - y0) == (pytest.approx(30), pytest.approx(50))
    assert (x0, y0) == (pytest.approx(100), pytest.approx(0))


def test_one_sheet_per_placement_sheet():
    d = PartsDoc(
        parts=[
            Part(id="A", cut=[SQUARE], placement=Placement(sheet=0, x=0, y=0)),
            Part(id="B", cut=[SQUARE], placement=Placement(sheet=1, x=0, y=0)),
        ],
        sheet_size=(600, 400),
    )
    sheets = d.layout()
    assert [s.index for s in sheets] == [0, 1]
    assert [p.id for p in sheets[1].parts] == ["B"]


# ---- SVG -------------------------------------------------------------------


def test_svg_is_mm_and_y_up():
    svg = to_svg(doc().layout()[0])
    root = ET.fromstring(svg)
    assert root.get("width").endswith("mm") and root.get("height").endswith("mm")
    ns = {"s": "http://www.w3.org/2000/svg"}
    groups = {g.get("id"): g for g in root.findall("s:g", ns)}
    assert {"CUT", "SCORE", "LABEL"} <= set(groups)
    assert len(groups["CUT"].findall("s:path", ns)) == 3
    texts = [t.text for t in root.iter("{http://www.w3.org/2000/svg}text")]
    assert sorted(texts) == ["A", "B"]


def test_svg_escapes_labels():
    d = PartsDoc(parts=[Part(id="A", cut=[SQUARE], label="<b>&")])
    svg = to_svg(d.layout()[0])
    ET.fromstring(svg)  # well-formed
    assert "&lt;b&gt;&amp;" in svg


# ---- writing files ---------------------------------------------------------


@pytest.mark.parametrize("name,stem", [("my cut", "my-cut"), ("../../etc/passwd", "etc-passwd"), ("", "cardboardcad")])
def test_safe_file_stem(name, stem):
    assert safe_file_stem(name) == stem


def test_export_dir_defaults_to_documents(tmp_path):
    assert resolve_export_dir("", home=str(tmp_path)) == os.path.join(str(tmp_path), "Documents", "CardboardCAD")


def test_export_dir_must_be_inside_home(tmp_path):
    with pytest.raises(ExportError):
        resolve_export_dir("/etc", home=str(tmp_path / "home"))
    with pytest.raises(ExportError):
        resolve_export_dir(str(tmp_path / "home" / ".." / "x"), home=str(tmp_path / "home"))


def test_export_files_writes_one_file_per_sheet(tmp_path):
    d = PartsDoc(
        parts=[
            Part(id="A", cut=[SQUARE], placement=Placement(sheet=0, x=0, y=0)),
            Part(id="B", cut=[SQUARE], placement=Placement(sheet=1, x=0, y=0)),
        ]
    )
    out = export_files(d, fmt="dxf", name="job", folder="", write=True, home=str(tmp_path))
    names = [os.path.basename(p) for p in out.paths]
    assert names == ["job-sheet1.dxf", "job-sheet2.dxf"]
    for p, text in zip(out.paths, out.texts):
        with open(p) as fh:
            assert fh.read() == text


def test_export_files_without_write_returns_text_only(tmp_path):
    out = export_files(doc(), fmt="svg", name="job", folder="", write=False, home=str(tmp_path))
    assert out.paths == [] and len(out.texts) == 1
    assert not os.path.exists(os.path.join(str(tmp_path), "Documents"))


def test_export_rejects_unknown_format(tmp_path):
    with pytest.raises(ExportError):
        export_files(doc(), fmt="pdf", name="x", folder="", write=False, home=str(tmp_path))
