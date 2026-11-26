"""Rhino / Grasshopper live sync.

Hops only asks the engine for an answer when one of its inputs changes. So
the live link has three pieces:

* ``GET /api/design/version`` (already there): a counter that goes up on
  every change to the app's design.
* A small listener script for a Grasshopper Python component
  (``assets/rhino_live_listener.py``, also served at
  ``/rhino/live_listener.py``). It polls the counter in the background and
  re-solves only when it changes, outputting the version number.
* The ``LiveDesign`` Hops component: give it that version and it returns
  the app's current design as Rhino geometry.

``SendToCardboardCAD`` goes the other way: a mesh from Rhino becomes the
app's model.
"""

import importlib.util
import json
import os
import threading

import pytest
import rhino3dm

from cardboard_engine.components.parts import PartsDoc
from cardboard_engine.design import DesignSession

from . import meshes

LISTENER = os.path.join(os.path.dirname(__file__), "..", "cardboard_engine", "assets", "rhino_live_listener.py")


def tool(client, name, **args):
    body = client.post("/api/design/tool", json={"name": name, "input": args}).get_json()
    assert not body["is_error"], body["content"]
    return body


def item(type_, data):
    return [{"type": type_, "data": data}]


def solve(client, pointer, values):
    payload = {
        "pointer": pointer,
        "values": [{"ParamName": k, "InnerTree": {"{0}": v}} for k, v in values.items()],
    }
    r = client.post("/solve", json=payload)
    assert r.status_code == 200, r.data
    out = {}
    for o in json.loads(r.data)["values"]:
        tree = o["InnerTree"]
        out[o["ParamName"]] = [x["data"] for x in tree.get("0", tree.get("{0}", []))]
    return out


def decode(items):
    return [rhino3dm.CommonObject.Decode(json.loads(x)) for x in items]


def meta(client, route):
    r = client.get(route)
    assert r.status_code == 200
    return json.loads(r.data)


# ---- the live payload ------------------------------------------------------


def test_live_payload_of_an_empty_design():
    s = DesignSession(home="/tmp")
    live = s.live()
    assert live["version"] == s.version
    assert live["changed"] is True
    assert live["model"] is None and live["parts3d"] == [] and live["sheets"] == []


def test_live_payload_says_unchanged_when_the_caller_is_up_to_date():
    s = DesignSession(home="/tmp")
    s.run_tool("create_shape", {"shape": "box"})
    assert s.live(since=s.version) == {"version": s.version, "changed": False}
    assert s.live(since=s.version - 1)["changed"] is True


def test_live_payload_has_model_parts_and_sheets():
    s = DesignSession(home="/tmp")
    s.run_tool("create_shape", {"shape": "box", "width_mm": 100, "depth_mm": 60, "height_mm": 40})
    s.run_tool("waffle_slice", {"x_ribs": 2, "y_ribs": 2, "thickness_mm": 3})
    s.run_tool("nest_sheets", {"sheet_width_mm": 600, "sheet_height_mm": 400})
    live = s.live()
    m = live["model"]
    assert len(m["vertices"]) == len(s.model.vertices) and len(m["faces"]) == len(s.model.faces)
    assert m["name"] == "box"
    assert [p["id"] for p in live["parts3d"]] == ["X1", "X2", "Y1", "Y2"]
    assert all(len(loop[0]) == 3 for p in live["parts3d"] for loop in p["loops"])
    assert live["thickness_mm"] == 3
    sheet = live["sheets"][0]
    assert sheet["size"] == [600, 400]
    assert {p["id"] for p in sheet["parts"]} == {"X1", "X2", "Y1", "Y2"}
    assert sheet["origin"] == [0, 0]
    assert PartsDoc.from_json(live["parts"]).parts


def test_live_sheets_are_laid_side_by_side():
    s = DesignSession(home="/tmp")
    s.run_tool("create_shape", {"shape": "box", "width_mm": 500, "depth_mm": 500, "height_mm": 500})
    s.run_tool("waffle_slice", {"x_ribs": 6, "y_ribs": 6})
    s.run_tool("nest_sheets", {"sheet_width_mm": 600, "sheet_height_mm": 600})
    sheets = s.live()["sheets"]
    assert len(sheets) > 1
    xs = [sh["origin"][0] for sh in sheets]
    assert xs == sorted(xs) and xs[1] >= 600


def test_live_route(client):
    v = tool(client, "create_shape", shape="cone")["version"]
    full = client.get("/api/design/live").get_json()
    assert full["version"] == v and full["model"]["vertices"]
    assert client.get(f"/api/design/live?since={v}").get_json() == {"version": v, "changed": False}
    assert client.get("/api/design/live?since=abc").status_code == 400


# ---- LiveDesign Hops component ---------------------------------------------


def test_live_design_metadata(client):
    m = meta(client, "/live_design")
    assert m["Category"] == "Fold Theory"
    assert [i["Nickname"] for i in m["Inputs"]] == ["V"]
    assert [o["Nickname"] for o in m["Outputs"]] == ["V", "M", "R3", "Cut", "Sc", "Sh", "L", "P", "S"]


def test_live_design_is_listed(client):
    uris = {c["Uri"] for c in json.loads(client.get("/").data)}
    assert {"/live_design", "/send_to_cardboardcad"} <= uris


def test_live_design_solve_returns_rhino_geometry(client):
    tool(client, "create_shape", shape="box", width_mm=100, depth_mm=60, height_mm=40)
    tool(client, "waffle_slice", x_ribs=2, y_ribs=2)
    v = tool(client, "nest_sheets", sheet_width_mm=600, sheet_height_mm=400)["version"]
    out = solve(client, "/live_design", {"Version": item("System.Int32", json.dumps(v))})
    assert json.loads(out["Version"][0]) == v
    (mesh,) = decode(out["Model"])
    assert isinstance(mesh, rhino3dm.Mesh)
    state = client.get("/api/design/state").get_json()
    assert len(mesh.Faces) >= state["model"]["faces"]
    assert len(decode(out["Parts 3D"])) == 4
    cuts = decode(out["Cut curves"])
    assert len(cuts) >= 4 and all(c.IsClosed for c in cuts)
    (outline,) = decode(out["Sheet outlines"])
    assert outline.IsClosed
    assert sorted(json.loads(x) for x in out["Labels"]) == ["X1", "X2", "Y1", "Y2"]
    assert PartsDoc.from_json(json.loads(out["Parts"][0])).parts
    assert "box" in json.loads(out["Summary"][0])


def test_live_design_with_no_design_is_a_clear_message(client):
    out = solve(client, "/live_design", {"Version": item("System.Int32", "0")})
    assert out["Model"] == [] or out["Model"] == [None] or all(x in (None, "null") for x in out["Model"])
    assert "no design" in json.loads(out["Summary"][0]).lower()


def test_live_design_calls_count_as_grasshopper_activity(client):
    solve(client, "/live_design", {"Version": item("System.Int32", "0")})
    assert client.get("/api/hops_activity").get_json()["requests"] >= 1


# ---- SendToCardboardCAD Hops component -------------------------------------


def send_values(mesh, name="from rhino", send=True):
    return {
        "Mesh": item("Rhino.Geometry.Mesh", json.dumps(mesh.Encode())),
        "Name": item("System.String", json.dumps(name)),
        "Send": item("System.Boolean", json.dumps(send)),
    }


def test_send_metadata(client):
    m = meta(client, "/send_to_cardboardcad")
    assert [i["Nickname"] for i in m["Inputs"]] == ["M", "N", "S"]
    assert [o["Nickname"] for o in m["Outputs"]] == ["V", "S"]


def test_send_replaces_the_apps_model(client):
    mesh = meshes.to_rhino(*meshes.box(200, 100, 50))
    out = solve(client, "/send_to_cardboardcad", send_values(mesh, "Stand v2"))
    state = client.get("/api/design/state").get_json()
    assert state["model"]["name"] == "Stand v2"
    assert state["model"]["size_mm"] == [200, 100, 50]
    assert json.loads(out["Version"][0]) == state["version"]
    assert "Stand v2" in json.loads(out["Status"][0])
    assert state["steps"][-1]["tool"] == "send_from_rhino"


def test_send_does_nothing_until_send_is_true(client):
    mesh = meshes.to_rhino(*meshes.box())
    out = solve(client, "/send_to_cardboardcad", send_values(mesh, send=False))
    assert client.get("/api/design/state").get_json()["model"] is None
    assert "Send" in json.loads(out["Status"][0])


# ---- the Grasshopper listener script ---------------------------------------


def load_listener():
    spec = importlib.util.spec_from_file_location("rhino_live_listener", LISTENER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # outside Grasshopper it only defines helpers
    return mod


def test_listener_script_is_served(client):
    r = client.get("/rhino/live_listener.py")
    assert r.status_code == 200
    assert r.mimetype == "text/x-python"
    with open(LISTENER, "rb") as fh:
        assert r.data == fh.read()


def test_listener_polls_the_version_endpoint():
    mod = load_listener()
    seen = {}

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"version": 42}'

    def opener(url, timeout):
        seen["url"], seen["timeout"] = url, timeout
        return Reply()

    assert mod.poll_version(5001, opener=opener) == 42
    assert seen["url"] == "http://127.0.0.1:5001/api/design/version"
    assert 0 < seen["timeout"] <= 1


def test_listener_returns_none_when_the_app_is_closed():
    mod = load_listener()

    def opener(url, timeout):
        raise OSError("connection refused")

    assert mod.poll_version(5001, opener=opener) is None


def test_listener_interval_is_clamped():
    mod = load_listener()
    assert mod.clamp_interval(None) == mod.DEFAULT_INTERVAL_MS
    assert mod.clamp_interval(1) == mod.MIN_INTERVAL_MS
    assert mod.clamp_interval(10**9) == mod.MAX_INTERVAL_MS
    assert mod.clamp_interval(750) == 750


def test_watcher_fires_only_on_change():
    mod = load_listener()
    versions = iter([1, 1, None, 2, 2, 3])
    changes = []
    w = mod.VersionWatcher(poll=lambda: next(versions), on_change=changes.append)
    for _ in range(6):
        w.check()
    assert changes == [1, 2, 3]
    assert w.version == 3
    assert w.connected is True


def test_watcher_reports_disconnects():
    mod = load_listener()
    versions = iter([5, None])
    changes = []
    w = mod.VersionWatcher(poll=lambda: next(versions), on_change=changes.append)
    w.check()
    w.check()
    assert w.connected is False
    assert w.version == 5  # keeps the last design
    assert changes == [5]


def test_watcher_thread_stops():
    mod = load_listener()
    stop = threading.Event()
    calls = []

    def poll():
        calls.append(1)
        if len(calls) >= 3:
            stop.set()
        return len(calls)

    w = mod.VersionWatcher(poll=poll, on_change=lambda v: None)
    t = w.start(interval_ms=mod.MIN_INTERVAL_MS, stop=stop)
    t.join(timeout=5)
    assert not t.is_alive()
    assert len(calls) >= 3
    assert t.daemon
