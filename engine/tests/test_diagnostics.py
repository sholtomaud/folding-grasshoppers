"""Diagnostics Claude can read (#61).

Grasshopper's LiveDesign listener, the Hops components, the Rhino plugin,
the engine and validation of forms, views and formulas write structured
entries into one rolling log on the Mac. The Design Assistant reads them
with read_diagnostics. Keys and tokens are redacted.
"""

import json
import os

import pytest

from cardboard_engine.design import DesignSession
from cardboard_engine.diagnostics import SOURCES, DiagnosticsLog, redact


@pytest.fixture
def log(tmp_path):
    return DiagnosticsLog(str(tmp_path / "diag"))


@pytest.fixture
def client(tmp_path, monkeypatch, lifecycle):
    from cardboard_engine.server import create_app
    from tests.conftest import TOKEN

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path / "data"))
    app = create_app(shutdown_token=TOKEN, lifecycle=lifecycle)
    app.testing = True
    return app.test_client()


def test_sources():
    for s in ("grasshopper", "rhino", "engine", "validation", "app"):
        assert s in SOURCES


def test_entries_are_structured_and_filterable(log):
    log.write("grasshopper", "info", "LiveDesign turned off", design_version=7, details={"on": False})
    log.write("engine", "error", "waffle_slice failed")
    out = log.read(source="grasshopper")
    assert len(out) == 1
    e = out[0]
    assert set(e) >= {"id", "time", "source", "level", "message", "design_version", "details"}
    assert e["design_version"] == 7
    assert log.read(level="error")[0]["source"] == "engine"
    assert len(log.read()) == 2


def test_level_filter_includes_more_severe(log):
    log.write("engine", "info", "a")
    log.write("engine", "warning", "b")
    log.write("engine", "error", "c")
    assert [e["message"] for e in log.read(level="warning")] == ["b", "c"]


def test_since_filter(log):
    first = log.write("engine", "info", "a")
    log.write("engine", "info", "b")
    assert [e["message"] for e in log.read(since_id=first["id"])] == ["b"]


def test_api_keys_and_tokens_are_redacted(log):
    log.write("app", "error", "bad key sk-ant-api03-abcdefghijklmnop123456 in request",
              details={"header": "Bearer abcdefghijklmnopqrstuvwxyz0123", "X-Engine-Token": "secret-token-value"})
    raw = open(log.path, encoding="utf-8").read()
    assert "sk-ant-api03-abcdefghijklmnop123456" not in raw
    assert "abcdefghijklmnopqrstuvwxyz0123" not in raw
    assert "secret-token-value" not in raw
    assert "[redacted]" in raw
    assert redact("x sk-ant-abc123456789012345 y") == "x [redacted] y"


def test_log_rotates_at_a_fixed_size(tmp_path):
    log = DiagnosticsLog(str(tmp_path / "d"), max_bytes=2000)
    for i in range(100):
        log.write("engine", "info", f"entry {i} " + "x" * 50)
    assert os.path.getsize(log.path) <= 2000
    assert os.path.isfile(log.path + ".1")
    msgs = [e["message"] for e in log.read(limit=500)]
    assert msgs[-1].startswith("entry 99")


def test_unknown_source_or_level_is_rejected(log):
    with pytest.raises(ValueError):
        log.write("nowhere", "info", "x")
    with pytest.raises(ValueError):
        log.write("engine", "loud", "x")


def test_listener_toggle_is_logged_and_claude_can_read_it(client):
    r = client.post("/api/diagnostics", json={"source": "grasshopper", "level": "info",
                                              "message": "LiveDesign listener turned off",
                                              "details": {"on": False, "last_version": 4}})
    assert r.status_code == 200
    client.post("/api/diagnostics", json={"source": "grasshopper", "level": "info",
                                          "message": "LiveDesign listener turned on",
                                          "details": {"on": True, "last_version": 4}})
    r = client.post("/api/design/tool", json={"name": "read_diagnostics", "input": {"source": "grasshopper"}})
    body = r.get_json()
    assert body["is_error"] is False
    assert "turned off" in body["content"] and "turned on" in body["content"]
    assert "last_version" in body["content"]


def test_diagnostics_api_filters(client):
    client.post("/api/diagnostics", json={"source": "rhino", "level": "error", "message": "conflict on rib X3"})
    out = client.get("/api/diagnostics?source=rhino").get_json()["entries"]
    assert out[0]["message"] == "conflict on rib X3"
    assert client.post("/api/diagnostics", json={"source": "bogus", "message": "x"}).status_code == 400


def test_a_rejected_view_writes_a_validation_error(client):
    view = {"schema": "cardboardcad.view/1", "id": "costs", "title": "Costs",
            "blocks": [{"type": "table", "bind": "quote.nonexistent"}]}
    client.post("/api/views", json={"view": view, "scope": "all"})
    out = client.get("/api/diagnostics?source=validation&level=error").get_json()["entries"]
    assert out and "quote.nonexistent" in out[-1]["message"]


def test_tool_errors_are_logged_from_the_engine(client):
    client.post("/api/design/tool", json={"name": "waffle_slice", "input": {}})  # no model yet
    out = client.get("/api/diagnostics?source=engine").get_json()["entries"]
    assert out and "waffle_slice" in out[-1]["message"]


def test_live_design_calls_are_logged_with_the_version(app_with_home):
    app, session = app_with_home
    from cardboard_engine.hops_live import note_live_call

    note_live_call(session, "LiveDesign", 3)
    out = session.diagnostics.read(source="grasshopper")
    assert out[-1]["details"]["listener_version"] == 3
    assert out[-1]["design_version"] == session.version


def test_form_rejection_is_logged(tmp_path):
    s = DesignSession(home=str(tmp_path), diagnostics=DiagnosticsLog(str(tmp_path / "d")))
    r = s.run_tool("request_inputs", {"fields": [{"key": "sheet_price"}]})
    s.submit_form(r.form["id"], {"sheet_price": "abc"}, scope="project", set_by="SM")
    out = s.diagnostics.read(source="validation")
    assert out and "sheet_price" in out[-1]["message"]


@pytest.fixture
def app_with_home(tmp_path, monkeypatch, lifecycle):
    from cardboard_engine.server import create_app
    from tests.conftest import TOKEN

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path / "data"))
    app = create_app(shutdown_token=TOKEN, lifecycle=lifecycle)
    return app, app.config["DESIGN"]


def test_listener_reports_on_and_off_to_the_diagnostics_endpoint():
    from cardboard_engine.assets import rhino_live_listener as L

    sent = []

    class Reply:
        def read(self):
            return b"{}"

        def close(self):
            pass

    def opener(req, timeout=None):
        sent.append((req.get_full_url(), json.loads(req.data.decode("utf-8")), req.get_method()))
        return Reply()

    assert L.report(5001, "LiveDesign listener turned off", {"on": False, "last_version": 4}, opener=opener)
    url, body, method = sent[0]
    assert url == "http://127.0.0.1:5001/api/diagnostics"
    assert method == "POST"
    assert body["source"] == "grasshopper"
    assert body["details"]["last_version"] == 4

    def broken(req, timeout=None):
        raise OSError("app closed")

    assert L.report(5001, "x", {}, opener=broken) is False  # never raises inside Grasshopper
