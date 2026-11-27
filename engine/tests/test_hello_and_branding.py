"""Hello-world demo component and Fold Theory branding on Hops components."""

import base64
import json
import struct

import pytest

from .test_server import hops_outputs

HOPS_ROUTES = ["/hello", "/echo", "/cut_estimate"]


def png_size(data: bytes):
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    return struct.unpack(">II", data[16:24])


@pytest.mark.parametrize("route", HOPS_ROUTES)
def test_hops_components_carry_fold_theory_icon(client, route):
    meta = json.loads(client.get(route).data)
    assert "Icon" in meta, f"{route} has no icon"
    assert png_size(base64.b64decode(meta["Icon"])) == (24, 24)


@pytest.mark.parametrize("route", HOPS_ROUTES)
def test_hops_components_are_in_fold_theory_category(client, route):
    meta = json.loads(client.get(route).data)
    assert meta["Category"] == "Fold Theory"


def test_hello_metadata(client):
    meta = json.loads(client.get("/hello").data)
    assert [i["Nickname"] for i in meta["Inputs"]] == ["Name"]
    assert meta["Inputs"][0]["ParamType"] == "Text"
    assert [o["Nickname"] for o in meta["Outputs"]] == ["Msg"]


def test_hello_solve(client):
    payload = {
        "pointer": "/hello",
        "values": [
            {
                "ParamName": "Name",
                "InnerTree": {"{0}": [{"type": "System.String", "data": json.dumps("Grasshopper")}]},
            }
        ],
    }
    r = client.post("/solve", json=payload)
    assert r.status_code == 200, r.data
    assert hops_outputs(r) == {"Message": "Hello, Grasshopper, from Fold Theory"}


def test_api_hello(client):
    r = client.post("/api/hello", json={"name": "SwiftUI"})
    assert r.status_code == 200
    assert r.get_json() == {"message": "Hello, SwiftUI, from Fold Theory"}


@pytest.mark.parametrize("body", [{}, {"name": ""}, {"name": "   "}])
def test_api_hello_defaults_to_world(client, body):
    r = client.post("/api/hello", json=body)
    assert r.get_json() == {"message": "Hello, world, from Fold Theory"}


def test_api_hello_rejects_non_string(client):
    r = client.post("/api/hello", json={"name": 3})
    assert r.status_code == 422
