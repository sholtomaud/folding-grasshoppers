"""Extra text marks on parts (edge numbers on unfolded nets)."""

import io
import xml.etree.ElementTree as ET

import ezdxf
import pytest

from cardboard_engine.components.export import to_dxf, to_svg
from cardboard_engine.components.parts import Part, PartsDoc, PartsError, Placement, Text

SQUARE = [(0, 0), (50, 0), (50, 30), (0, 30)]


def part():
    return Part(id="N1", cut=[SQUARE], label="N1", texts=[Text("7", (10, 2), 3.0), Text("8", (40, 2), 3.0)])


def test_texts_round_trip():
    d = PartsDoc(parts=[part()])
    assert PartsDoc.from_json(d.to_json()) == d


def test_texts_follow_placement():
    p = part().transformed(Placement(x=100, y=0, rotation=180))
    assert p.texts[0].at == (pytest.approx(140), pytest.approx(28))


def test_texts_in_dxf_and_svg():
    sheet = PartsDoc(parts=[part()]).layout()[0]
    msp = ezdxf.read(io.StringIO(to_dxf(sheet))).modelspace()
    assert sorted(t.dxf.text for t in msp if t.dxftype() == "TEXT") == ["7", "8", "N1"]
    root = ET.fromstring(to_svg(sheet))
    assert sorted(t.text for t in root.iter("{http://www.w3.org/2000/svg}text")) == ["7", "8", "N1"]


def test_bad_texts_are_rejected():
    bad = '{"schema":"cardboardcad.parts/1","parts":[{"id":"A","cut":[[[0,0],[1,0],[0,1]]],"texts":[{"text":5,"at":[0,0]}]}]}'
    with pytest.raises(PartsError):
        PartsDoc.from_json(bad)
