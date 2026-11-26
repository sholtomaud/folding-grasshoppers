import json

import pytest

from cardboard_engine import __version__
from cardboard_engine.server import create_app

from .conftest import TOKEN

CUT_INPUT_NICKS = ["Lc", "Ls", "N", "S", "Vc", "Vs", "Tp", "k", "Ps", "R"]
CUT_INPUT_NAMES = [
    "Cut length (mm)",
    "Score length (mm)",
    "Pierce count",
    "Sheet count",
    "Cut speed (mm/s)",
    "Score speed (mm/s)",
    "Pierce time (s)",
    "Travel overhead (fraction)",
    "Sheet price",
    "Machine rate (per hour)",
]


def hops_payload(pointer, values: dict):
    """Build the JSON body a Grasshopper Hops component POSTs."""
    return {
        "pointer": pointer,
        "values": [
            {
                "ParamName": name,
                "InnerTree": {"{0}": [{"type": "System.Double", "data": json.dumps(v)}]},
            }
            for name, v in values.items()
        ],
    }


def hops_outputs(resp):
    body = json.loads(resp.data)
    return {
        o["ParamName"]: json.loads(o["InnerTree"]["0"][0]["data"]) for o in body["values"]
    }


# ---- plain routes ---------------------------------------------------------


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.get_json() == {"status": "healthy"}


def test_version(client):
    body = client.get("/version").get_json()
    assert body["engine"] == __version__
    assert body["python"].startswith("3.")
    assert body["packages"]["flask"]
    assert body["packages"]["ghhops-server"]


def test_shutdown_requires_token(client, lifecycle):
    assert client.post("/shutdown").status_code == 403
    assert client.post("/shutdown", headers={"X-Engine-Token": "wrong"}).status_code == 403
    assert not lifecycle.shutdown_event.is_set()


def test_shutdown_with_token(client, lifecycle):
    stopped = []
    lifecycle.bind_server(lambda: stopped.append(True))
    r = client.post("/shutdown", headers={"X-Engine-Token": TOKEN})
    assert r.status_code == 202
    assert lifecycle.shutdown_event.is_set()
    assert lifecycle.shutdown_reason == "POST /shutdown"


def test_shutdown_disabled_without_token():
    c = create_app(shutdown_token=None).test_client()
    assert c.post("/shutdown", headers={"X-Engine-Token": ""}).status_code == 403


def test_api_cut_estimate(client):
    r = client.post("/api/cut_estimate", json={"cut_length": 1200})
    assert r.status_code == 200
    body = r.get_json()
    assert body["time_min"] == pytest.approx(69 / 60)
    assert set(body) == {"time_min", "material_cost", "machine_cost", "total_cost"}


def test_api_cut_estimate_invalid(client):
    r = client.post("/api/cut_estimate", json={"cut_length": 10, "cut_speed": 0})
    assert r.status_code == 422
    assert "Cut speed" in r.get_json()["message"]
    assert client.post("/api/cut_estimate", data="nope").status_code == 422


# ---- Hops metadata --------------------------------------------------------


def test_hops_root_lists_components(client):
    body = json.loads(client.get("/").data)
    uris = {c["Uri"] for c in body}
    assert {"/echo", "/cut_estimate"} <= uris


def test_hops_cut_estimate_metadata(client):
    r = client.get("/cut_estimate")
    assert r.status_code == 200
    meta = json.loads(r.data)
    assert meta["Uri"] == "/cut_estimate"
    assert [i["Nickname"] for i in meta["Inputs"]] == CUT_INPUT_NICKS
    assert [i["Name"] for i in meta["Inputs"]] == CUT_INPUT_NAMES
    assert all(i["ParamType"] == "Number" for i in meta["Inputs"])
    assert [o["Nickname"] for o in meta["Outputs"]] == ["T", "Cm", "Cmach", "C"]
    defaults = {i["Nickname"]: i.get("Default") for i in meta["Inputs"]}
    assert defaults == {
        "Lc": None,
        "Ls": 0.0,
        "N": 0.0,
        "S": 1.0,
        "Vc": 20.0,
        "Vs": 40.0,
        "Tp": 0.5,
        "k": 0.15,
        "Ps": 0.0,
        "R": 0.0,
    }


def test_hops_echo_metadata(client):
    meta = json.loads(client.get("/echo").data)
    assert [i["Nickname"] for i in meta["Inputs"]] == ["N"]
    assert [o["Nickname"] for o in meta["Outputs"]] == ["N"]


# ---- Hops solve -----------------------------------------------------------


# Hops GETs the component URL for metadata, then POSTs to /solve with
# "pointer" naming the component. ghhops_server answers 405 to a POST on the
# component URL itself.


def test_hops_echo_solve(client):
    r = client.post("/solve", json=hops_payload("/echo", {"Number": 42.5}))
    assert r.status_code == 200, r.data
    assert hops_outputs(r) == {"Number": 42.5}


def test_hops_cut_estimate_solve(client):
    values = dict(
        zip(CUT_INPUT_NAMES, [2000, 500, 10, 2, 20, 40, 0.5, 0.15, 3.5, 60])
    )
    r = client.post("/solve", json=hops_payload("/cut_estimate", values))
    assert r.status_code == 200, r.data
    out = hops_outputs(r)
    assert out["Time (min)"] == pytest.approx(135.125 / 60)
    assert out["Material cost"] == pytest.approx(7.0)
    assert out["Machine cost"] == pytest.approx(135.125 / 60)
    assert out["Total cost"] == pytest.approx(7.0 + 135.125 / 60)


def test_post_to_component_url_is_405(client):
    assert client.post("/echo", json=hops_payload("/echo", {"Number": 1})).status_code == 405


def test_hops_cut_estimate_invalid_is_component_error(client):
    values = dict(zip(CUT_INPUT_NAMES, [100, 0, 0, 1, 0, 40, 0.5, 0.15, 0, 0]))
    r = client.post("/solve", json=hops_payload("/cut_estimate", values))
    # ghhops_server reports handler exceptions as a non-200 with "errors",
    # which Hops shows as a component error rather than outputting zeros.
    assert r.status_code != 200
    body = json.loads(r.data)
    assert body["values"] == []
    assert any("Cut speed" in e for e in body["errors"])
