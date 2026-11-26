"""HTTP routes the app uses for the Design Assistant and its preview pane."""

import json


def tool(client, name, **args):
    return client.post("/api/design/tool", json={"name": name, "input": args})


def test_tools_route_gives_system_prompt_and_tools(client):
    r = client.get("/api/design/tools")
    assert r.status_code == 200
    body = r.get_json()
    assert "mm" in body["system"]
    assert any(t["name"] == "waffle_slice" for t in body["tools"])


def test_tool_route_runs_a_tool_and_bumps_the_version(client):
    v0 = client.get("/api/design/version").get_json()["version"]
    r = tool(client, "create_shape", shape="box", width_mm=100, depth_mm=60, height_mm=40)
    assert r.status_code == 200
    body = r.get_json()
    assert body["is_error"] is False
    assert "box" in body["content"]
    assert body["version"] > v0
    assert client.get("/api/design/version").get_json()["version"] == body["version"]


def test_tool_errors_are_200_with_is_error(client):
    body = tool(client, "waffle_slice").get_json()
    assert body["is_error"] is True
    assert "model" in body["content"].lower()


def test_malformed_tool_request_is_400(client):
    assert client.post("/api/design/tool", json={"input": {}}).status_code == 400
    assert client.post("/api/design/tool", data="name=x", content_type="text/plain").status_code == 400
    assert client.post("/api/design/tool", json={"name": "model_info", "input": [1]}).status_code == 400


def test_state_route(client):
    tool(client, "create_shape", shape="cone", width_mm=120, depth_mm=120, height_mm=200)
    s = client.get("/api/design/state").get_json()
    assert s["model"]["name"] == "cone"
    assert s["scene"]["objects"][0]["kind"] == "model"


def test_reset_route(client):
    tool(client, "create_shape", shape="box", width_mm=10, depth_mm=10, height_mm=10)
    assert client.post("/api/design/reset").status_code == 200
    assert client.get("/api/design/state").get_json()["model"] is None


def test_preview_page_is_self_contained_html(client):
    r = client.get("/preview")
    assert r.status_code == 200
    assert r.mimetype == "text/html"
    html = r.get_data(as_text=True)
    assert "/api/design/state" in html and "/api/design/version" in html
    assert "<script src=" not in html, "no external scripts: it must work offline"
    assert "http://" not in html.replace("http://www.w3.org", "")
    assert r.headers["Cache-Control"] == "no-store"


def test_design_routes_are_not_counted_as_grasshopper_calls(client):
    tool(client, "model_info")
    client.get("/preview")
    assert client.get("/api/hops_activity").get_json()["requests"] == 0
