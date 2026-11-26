"""/nest as Grasshopper Hops sees it."""

import json

import rhino3dm

from cardboard_engine.components.parts import PartsDoc

from . import meshes
from .test_hops_geometry import item, meta, solve, waffle_values


def nest_values(parts, w=600.0, h=400.0, rotations="0,90"):
    return {
        "Parts": item("System.String", parts),
        "Sheet width (mm)": item("System.Double", json.dumps(w)),
        "Sheet height (mm)": item("System.Double", json.dumps(h)),
        "Gap (mm)": item("System.Double", json.dumps(5.0)),
        "Margin (mm)": item("System.Double", json.dumps(10.0)),
        "Rotations": item("System.String", json.dumps(rotations)),
    }


def test_nest_metadata_and_branding(client):
    m = meta(client, "/nest")
    assert m["Category"] == "Fold Theory" and m["Icon"]
    assert [i["Nickname"] for i in m["Inputs"]] == ["P", "W", "H", "G", "E", "R"]
    assert [o["Nickname"] for o in m["Outputs"]] == ["P", "S", "Cut", "N", "U"]
    defaults = {i["Nickname"]: i.get("Default") for i in m["Inputs"]}
    assert defaults == {"P": None, "W": 1200.0, "H": 800.0, "G": 5.0, "E": 10.0, "R": "0,90"}


def test_waffle_to_nest_to_export(client, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    mesh = meshes.to_rhino(*meshes.box(200, 150, 100))
    parts = solve(client, "/waffle_slice", waffle_values(mesh, nx=3, ny=3))["Parts"][0]
    out = solve(client, "/nest", nest_values(parts, w=500, h=300))
    count = json.loads(out["Sheet count"][0])
    doc = PartsDoc.from_json(json.loads(out["Parts"][0]))
    assert count >= 2
    assert all(p.placement is not None for p in doc.parts)
    assert doc.sheet_size == (500, 300)
    sheets = [rhino3dm.CommonObject.Decode(json.loads(c)) for c in out["Sheets"]]
    assert len(sheets) == count
    assert len(out["Use %"]) == count and all(0 < json.loads(u) <= 100 for u in out["Use %"])
    assert len(out["Placed curves"]) == 6

    exported = solve(
        client,
        "/export",
        {
            "Parts": item("System.String", out["Parts"][0]),
            "Format": item("System.String", json.dumps("svg")),
            "Name": item("System.String", json.dumps("box")),
            "Folder": item("System.String", json.dumps("")),
            "Write": item("System.Boolean", json.dumps(True)),
        },
    )
    assert len(exported["Paths"]) == count


def test_nest_too_big_is_a_hops_error(client):
    mesh = meshes.to_rhino(*meshes.box(200, 150, 100))
    parts = solve(client, "/waffle_slice", waffle_values(mesh, nx=1, ny=1))["Parts"][0]
    payload = {
        "pointer": "/nest",
        "values": [{"ParamName": k, "InnerTree": {"{0}": v}} for k, v in nest_values(parts, w=100, h=100).items()],
    }
    r = client.post("/solve", json=payload)
    assert b"Too big" in r.data


def test_api_nest(client):
    doc = {
        "schema": "cardboardcad.parts/1",
        "parts": [{"id": "A", "cut": [[[0, 0], [50, 0], [50, 50], [0, 50]]]}],
    }
    r = client.post("/api/nest", json={"parts": doc, "sheet_width": 200, "sheet_height": 100})
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["sheet_count"] == 1
    assert body["parts"]["parts"][0]["placement"]["x"] == 10
    bad = client.post("/api/nest", json={"parts": doc, "sheet_width": 0, "sheet_height": 100})
    assert bad.status_code == 422
