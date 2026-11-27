"""The geometry components as Grasshopper Hops sees them."""

import base64
import json
import struct

import pytest
import rhino3dm

from cardboard_engine.components.parts import PartsDoc

from . import meshes

GEOMETRY_ROUTES = ["/waffle_slice", "/export"]


def meta(client, route):
    r = client.get(route)
    assert r.status_code == 200, route
    return json.loads(r.data)


@pytest.mark.parametrize("route", GEOMETRY_ROUTES)
def test_geometry_components_are_branded(client, route):
    m = meta(client, route)
    assert m["Category"] == "Fold Theory"
    data = base64.b64decode(m["Icon"])
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", data[16:24]) == (24, 24)


def test_waffle_metadata(client):
    m = meta(client, "/waffle_slice")
    assert [i["Nickname"] for i in m["Inputs"]] == ["M", "Nx", "Ny", "T", "C", "K"]
    assert [i["ParamType"] for i in m["Inputs"]] == ["Mesh", "Integer", "Integer", "Number", "Number", "Number"]
    assert [o["Nickname"] for o in m["Outputs"]] == ["P", "Cut", "R3", "L"]
    defaults = {i["Nickname"]: i.get("Default") for i in m["Inputs"]}
    assert defaults == {"M": None, "Nx": 5, "Ny": 5, "T": 3.0, "C": 0.2, "K": 0.0}


def item(type_, data):
    return [{"type": type_, "data": data}]


def solve(client, pointer, values):
    payload = {
        "pointer": pointer,
        "values": [{"ParamName": k, "InnerTree": {"{0}": v}} for k, v in values.items()],
    }
    r = client.post("/solve", json=payload)
    assert r.status_code == 200, r.data
    return {o["ParamName"]: [x["data"] for x in o["InnerTree"]["0"]] for o in json.loads(r.data)["values"]}


def waffle_values(mesh, nx=3, ny=2, t=3.0):
    return {
        "Mesh": item("Rhino.Geometry.Mesh", json.dumps(mesh.Encode())),
        "X ribs": item("System.Int32", json.dumps(nx)),
        "Y ribs": item("System.Int32", json.dumps(ny)),
        "Thickness (mm)": item("System.Double", json.dumps(t)),
        "Slot clearance (mm)": item("System.Double", json.dumps(0.2)),
        "Kerf (mm)": item("System.Double", json.dumps(0.0)),
    }


def test_waffle_solve_returns_parts_and_curves(client):
    mesh = meshes.to_rhino(*meshes.box(100, 60, 40))
    out = solve(client, "/waffle_slice", waffle_values(mesh))
    doc = PartsDoc.from_json(json.loads(out["Parts"][0]))
    assert [p.id for p in doc.parts] == ["X1", "X2", "X3", "Y1", "Y2"]
    assert [json.loads(s) for s in out["Labels"]] == ["X1", "X2", "X3", "Y1", "Y2"]
    curves = [rhino3dm.CommonObject.Decode(json.loads(c)) for c in out["Cut curves"]]
    assert len(curves) == 5 and all(c.IsClosed for c in curves)
    ribs = [rhino3dm.CommonObject.Decode(json.loads(c)) for c in out["Ribs 3D"]]
    assert len(ribs) == 5


def test_waffle_solve_error_is_a_hops_error(client):
    mesh = meshes.to_rhino(*meshes.box())
    payload = {
        "pointer": "/waffle_slice",
        "values": [
            {"ParamName": k, "InnerTree": {"{0}": v}} for k, v in waffle_values(mesh, nx=0, ny=0).items()
        ],
    }
    r = client.post("/solve", json=payload)
    assert r.status_code != 200 or "error" in r.data.decode().lower()
    assert b"at least one rib" in r.data


def test_export_solve_returns_dxf_text(client, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    mesh = meshes.to_rhino(*meshes.box(100, 60, 40))
    parts = solve(client, "/waffle_slice", waffle_values(mesh))["Parts"][0]
    out = solve(
        client,
        "/export",
        {
            "Parts": item("System.String", parts),
            "Format": item("System.String", json.dumps("dxf")),
            "Name": item("System.String", json.dumps("box")),
            "Folder": item("System.String", json.dumps("")),
            "Write": item("System.Boolean", json.dumps(True)),
        },
    )
    texts = [json.loads(t) for t in out["Text"]]
    paths = [json.loads(p) for p in out["Paths"]]
    assert len(texts) == 1 and "ENTITIES" in texts[0]
    assert paths == [str(tmp_path / "Documents" / "Fold Theory" / "box-sheet1.dxf")]
    assert (tmp_path / "Documents" / "Fold Theory" / "box-sheet1.dxf").read_text() == texts[0]


def test_api_waffle_slice_plain_json(client):
    v, f = meshes.box(100, 60, 40)
    r = client.post(
        "/api/waffle_slice",
        json={"mesh": {"vertices": v, "faces": f}, "nx": 2, "ny": 2, "thickness": 3.0},
    )
    assert r.status_code == 200, r.data
    assert [p["id"] for p in r.get_json()["parts"]] == ["X1", "X2", "Y1", "Y2"]
    bad = client.post("/api/waffle_slice", json={"mesh": {"vertices": v, "faces": f}, "nx": 0, "ny": 0, "thickness": 3})
    assert bad.status_code == 422
