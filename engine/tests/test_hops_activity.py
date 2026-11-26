"""GET /api/hops_activity: lets the app show when Grasshopper last called the engine.

Hops never holds a connection open; it sends a request each time a component
loads or solves. The engine counts the Hops-protocol requests it serves (the
component routes and /solve) and reports the most recent one.
"""

import json


def solve_hello(client, name="Grasshopper"):
    payload = {
        "pointer": "/hello",
        "values": [
            {
                "ParamName": "Name",
                "InnerTree": {"{0}": [{"type": "System.String", "data": json.dumps(name)}]},
            }
        ],
    }
    return client.post("/solve", json=payload)


def activity(client):
    r = client.get("/api/hops_activity")
    assert r.status_code == 200
    return r.get_json()


def test_no_hops_requests_yet(client):
    a = activity(client)
    assert a["requests"] == 0
    assert a["last_request_at"] is None
    assert a["seconds_since_last"] is None
    assert a["last_path"] is None
    assert a["last_status"] is None


def test_component_metadata_request_counts(client):
    client.get("/hello")
    a = activity(client)
    assert a["requests"] == 1
    assert a["last_path"] == "/hello"
    assert a["last_status"] == 200
    assert isinstance(a["last_request_at"], str) and a["last_request_at"].endswith("Z")
    assert 0 <= a["seconds_since_last"] < 5


def test_solve_counts_and_reports_pointer(client):
    client.get("/hello")
    assert solve_hello(client).status_code == 200
    a = activity(client)
    assert a["requests"] == 2
    assert a["last_path"] == "/solve /hello"
    assert a["last_status"] == 200


def test_failed_solve_records_error_status(client):
    payload = {
        "pointer": "/cut_estimate",
        "values": [
            {"ParamName": "Lc", "InnerTree": {"{0}": [{"type": "System.Double", "data": "-1.0"}]}},
        ],
    }
    r = client.post("/solve", json=payload)
    assert r.status_code != 200
    a = activity(client)
    assert a["requests"] == 1
    assert a["last_status"] == r.status_code


def test_app_routes_do_not_count(client):
    client.get("/health")
    client.get("/version")
    client.post("/api/hello", json={"name": "app"})
    client.post("/api/cut_estimate", json={"cut_length": 100})
    client.get("/api/hops_activity")
    assert activity(client)["requests"] == 0
