"""/unfold as Grasshopper Hops sees it."""

import json

import rhino3dm

from cardboard_engine.components.parts import PartsDoc

from . import meshes
from .test_hops_geometry import item, meta, solve


def unfold_values(mesh, tw=8.0, ta=45.0, fa=1.0):
    return {
        "Mesh": item("Rhino.Geometry.Mesh", json.dumps(mesh.Encode())),
        "Tab width (mm)": item("System.Double", json.dumps(tw)),
        "Tab angle (deg)": item("System.Double", json.dumps(ta)),
        "Flat angle (deg)": item("System.Double", json.dumps(fa)),
    }


def test_unfold_metadata_and_branding(client):
    m = meta(client, "/unfold")
    assert m["Category"] == "Fold Theory" and m["Icon"]
    assert [i["Nickname"] for i in m["Inputs"]] == ["M", "Tw", "Ta", "Fa"]
    assert [o["Nickname"] for o in m["Outputs"]] == ["P", "Cut", "Score", "L", "N"]
    defaults = {i["Nickname"]: i.get("Default") for i in m["Inputs"]}
    assert defaults == {"M": None, "Tw": 8.0, "Ta": 45.0, "Fa": 1.0}


def test_unfold_solve_box(client):
    mesh = meshes.to_rhino(*meshes.box_quads(100, 60, 40))
    out = solve(client, "/unfold", unfold_values(mesh))
    doc = PartsDoc.from_json(json.loads(out["Parts"][0]))
    assert [p.id for p in doc.parts] == ["N1"]
    assert json.loads(out["Net count"][0]) == 1
    cut = [rhino3dm.CommonObject.Decode(json.loads(c)) for c in out["Cut curves"]]
    assert len(cut) == 1 and cut[0].IsClosed
    assert len(out["Score curves"]) == 12  # 5 folds + 7 tab folds


def test_unfold_bad_tab_angle_is_a_hops_error(client):
    mesh = meshes.to_rhino(*meshes.box_quads())
    payload = {
        "pointer": "/unfold",
        "values": [{"ParamName": k, "InnerTree": {"{0}": v}} for k, v in unfold_values(mesh, ta=95).items()],
    }
    r = client.post("/solve", json=payload)
    assert b"Tab angle must be between 0 and 90" in r.data


def test_api_unfold(client):
    v, f = meshes.box_quads(100, 60, 40)
    r = client.post("/api/unfold", json={"mesh": {"vertices": v, "faces": f}, "tab_width": 0})
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert [p["id"] for p in body["parts"]] == ["N1"]
    bad = client.post("/api/unfold", json={"mesh": {"vertices": v, "faces": f}, "tab_angle": 0})
    assert bad.status_code == 422
