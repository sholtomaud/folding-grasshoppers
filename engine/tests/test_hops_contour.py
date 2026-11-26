"""/contour_stack as Grasshopper Hops sees it."""

import json

import rhino3dm

from cardboard_engine.components.parts import PartsDoc

from . import meshes
from .test_hops_geometry import item, meta, solve


def contour_values(mesh, t=10.0, etch=True, axis="z"):
    return {
        "Mesh": item("Rhino.Geometry.Mesh", json.dumps(mesh.Encode())),
        "Layer thickness (mm)": item("System.Double", json.dumps(t)),
        "Kerf (mm)": item("System.Double", json.dumps(0.0)),
        "Etch": item("System.Boolean", json.dumps(etch)),
        "Axis": item("System.String", json.dumps(axis)),
    }


def test_contour_metadata_and_branding(client):
    m = meta(client, "/contour_stack")
    assert m["Category"] == "Fold Theory" and m["Icon"]
    assert [i["Nickname"] for i in m["Inputs"]] == ["M", "T", "K", "E", "A"]
    assert [o["Nickname"] for o in m["Outputs"]] == ["P", "Cut", "Etch", "L3", "L"]
    defaults = {i["Nickname"]: i.get("Default") for i in m["Inputs"]}
    assert defaults == {"M": None, "T": 3.0, "K": 0.0, "E": True, "A": "z"}


def test_contour_solve(client):
    mesh = meshes.to_rhino(*meshes.uv_sphere(50, rings=24, segments=48))
    out = solve(client, "/contour_stack", contour_values(mesh))
    doc = PartsDoc.from_json(json.loads(out["Parts"][0]))
    assert [p.id for p in doc.parts] == [f"L{i:02d}" for i in range(1, 11)]
    assert [json.loads(s) for s in out["Labels"]] == [p.id for p in doc.parts]
    cut = [rhino3dm.CommonObject.Decode(json.loads(c)) for c in out["Cut curves"]]
    assert len(cut) == 10 and all(c.IsClosed for c in cut)
    assert len(out["Etch curves"]) >= 6
    assert len(out["Layers 3D"]) == 10


def test_contour_bad_axis_is_a_hops_error(client):
    mesh = meshes.to_rhino(*meshes.box())
    payload = {
        "pointer": "/contour_stack",
        "values": [{"ParamName": k, "InnerTree": {"{0}": v}} for k, v in contour_values(mesh, axis="w").items()],
    }
    r = client.post("/solve", json=payload)
    assert b"Axis must be x, y or z" in r.data


def test_api_contour_stack(client):
    v, f = meshes.box(100, 60, 40)
    r = client.post("/api/contour_stack", json={"mesh": {"vertices": v, "faces": f}, "thickness": 10})
    assert r.status_code == 200, r.data
    assert [p["id"] for p in r.get_json()["parts"]] == ["L01", "L02", "L03", "L04"]
    bad = client.post("/api/contour_stack", json={"mesh": {"vertices": v, "faces": f}, "thickness": 0})
    assert bad.status_code == 422
