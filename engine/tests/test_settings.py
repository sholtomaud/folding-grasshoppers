"""Project settings (#26): typed values in layers, most specific wins.

Layers, least to most specific: default (the Fold Theory rate card, in
~/Documents/Fold Theory/Settings/settings.json), workflow, project (the
project folder's settings.json), concept. Every resolved value says which
layer it came from and who set it when.
"""

import json
import os

import pytest

from cardboard_engine.design import DesignSession
from cardboard_engine.settings import (
    BUILTIN_DEFINITIONS, LAYERS, SETTINGS_SCHEMA, SettingsError, Definition, settings_dir, validate_value,
)


@pytest.fixture
def session(tmp_path):
    return DesignSession(home=str(tmp_path))


@pytest.fixture
def client(tmp_path, monkeypatch, lifecycle):
    """An engine whose home folder is a temporary one."""
    from cardboard_engine.server import create_app
    from tests.conftest import TOKEN

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path / "data"))
    app = create_app(shutdown_token=TOKEN, lifecycle=lifecycle)
    app.testing = True
    return app.test_client()


def test_layers_in_order():
    assert LAYERS == ("default", "workflow", "project", "concept")


def test_project_value_wins_over_default_and_new_project_gets_default(tmp_path):
    s = DesignSession(home=str(tmp_path))
    s.settings.set("sheet_price", 42.00, scope="all", set_by="SM")
    s.settings.set("sheet_price", 38.50, scope="project", set_by="SM")
    r = s.settings.resolve("sheet_price")
    assert r["value"] == 38.50
    assert r["layer"] == "project"
    assert r["set_by"] == "SM"
    assert r["set_at"]
    fresh = DesignSession(home=str(tmp_path))
    r2 = fresh.settings.resolve("sheet_price")
    assert r2["value"] == 42.00
    assert r2["layer"] == "default"


def test_defaults_are_a_json_file_in_documents(tmp_path):
    s = DesignSession(home=str(tmp_path))
    s.settings.set("machine_rate", 60, scope="all", set_by="SM")
    path = os.path.join(settings_dir(str(tmp_path)), "settings.json")
    assert path == os.path.join(str(tmp_path), "Documents", "Fold Theory", "Settings", "settings.json")
    data = json.load(open(path))
    assert data["schema"] == SETTINGS_SCHEMA
    assert data["values"]["machine_rate"]["value"] == 60
    assert data["values"]["machine_rate"]["set_by"] == "SM"


def test_negative_price_is_rejected_with_a_clear_message(session):
    with pytest.raises(SettingsError) as e:
        session.settings.set("sheet_price", -5, scope="project", set_by="SM")
    assert "Sheet price" in str(e.value)
    assert "0" in str(e.value)
    assert session.settings.resolve("sheet_price")["value"] is None


def test_text_that_is_not_a_number_is_rejected():
    d = BUILTIN_DEFINITIONS["sheet_price"]
    with pytest.raises(SettingsError) as e:
        validate_value(d, "abc")
    assert "number" in str(e.value)
    assert validate_value(d, "38.50") == 38.5


def test_definitions_are_typed_with_label_unit_and_range():
    d = BUILTIN_DEFINITIONS["sheet_price"]
    assert isinstance(d, Definition)
    assert d.type == "currency"
    assert d.label == "Sheet price"
    assert d.unit == "AUD/sheet"
    assert d.minimum == 0
    assert BUILTIN_DEFINITIONS["gst_pct"].unit == "%"


def test_unknown_setting_is_rejected(session):
    with pytest.raises(SettingsError) as e:
        session.settings.set("sheet_prise", 3, scope="project", set_by="SM")
    assert "sheet_price" in str(e.value)  # suggests the real keys


def test_workflow_layer_sits_between_default_and_project(session):
    session.settings.set("machine_rate", 50, scope="all", set_by="SM")
    session.workflows.save({"name": "Big board", "steps": [{"tools": ["nest_sheets"]}],
                            "settings": {"machine_rate": 70}})
    r = session.settings.resolve("machine_rate")
    assert (r["value"], r["layer"]) == (70, "workflow")
    session.settings.set("machine_rate", 80, scope="project", set_by="SM")
    assert session.settings.resolve("machine_rate")["layer"] == "project"


def test_concept_layer_is_most_specific(session):
    session.settings.set("margin_pct", 20, scope="project", set_by="SM")
    session.settings.set("margin_pct", 25, scope="concept", set_by="SM")
    assert session.settings.resolve("margin_pct")["layer"] == "concept"


def test_legacy_workflow_prices_become_workflow_settings(tmp_path):
    """Workflows saved before settings kept prices as estimate_cost inputs; they still count."""
    from cardboard_engine.workflow import workflows_dir

    folder = workflows_dir(str(tmp_path))
    os.makedirs(folder)
    json.dump({"schema": "cardboardcad.workflow/1", "name": "Old", "steps": [{"tools": ["estimate_cost"]}],
               "defaults": {"estimate_cost": {"sheet_price": 12.5, "machine_rate": None}}},
              open(os.path.join(folder, "Old.json"), "w"))
    s = DesignSession(home=str(tmp_path))
    s.workflows.set_settings(active="Old")
    wf = s.workflows.active()
    assert wf["settings"] == {"sheet_price": 12.5}
    assert "estimate_cost" not in wf["defaults"]
    assert s.settings.resolve("sheet_price")["layer"] == "workflow"


def test_project_settings_are_saved_with_the_project_and_reopened(tmp_path):
    s = DesignSession(home=str(tmp_path))
    s.run_tool("create_shape", {"shape": "box"})
    s.settings.set("sheet_price", 38.5, scope="project", set_by="SM")
    out = s.save_project(name="Acme")
    data = json.load(open(os.path.join(out["folder"], "settings.json")))
    assert data["schema"] == SETTINGS_SCHEMA
    assert data["values"]["sheet_price"]["value"] == 38.5
    t = DesignSession(home=str(tmp_path))
    assert t.settings.resolve("sheet_price")["value"] is None
    t.open_project(out["folder"])
    assert t.settings.resolve("sheet_price")["value"] == 38.5


def test_setting_a_value_in_an_open_project_writes_its_settings_file(tmp_path):
    s = DesignSession(home=str(tmp_path))
    s.run_tool("create_shape", {"shape": "box"})
    out = s.save_project(name="Acme")
    s.settings.set("machine_rate", 65, scope="project", set_by="SM")
    data = json.load(open(os.path.join(out["folder"], "settings.json")))
    assert data["values"]["machine_rate"]["value"] == 65


def test_listing_reports_every_setting_with_its_layer(session):
    session.settings.set("sheet_price", 38.5, scope="project", set_by="SM")
    rows = {r["key"]: r for r in session.settings.listing()["settings"]}
    assert rows["sheet_price"]["layer"] == "project"
    assert rows["gst_pct"]["layer"] == "default"
    assert rows["machine_rate"]["value"] is None  # not set anywhere: must be asked for
    assert rows["sheet_price"]["label"] == "Sheet price"


def test_settings_api(client):
    r = client.get("/api/settings")
    assert r.status_code == 200
    keys = {row["key"] for row in r.get_json()["settings"]}
    assert {"sheet_price", "machine_rate", "gst_pct"} <= keys
    r = client.post("/api/settings", json={"key": "sheet_price", "value": "abc", "scope": "project"})
    assert r.status_code == 422
    assert "number" in r.get_json()["message"]
    r = client.post("/api/settings", json={"key": "sheet_price", "value": 38.5, "scope": "project", "set_by": "SM"})
    assert r.status_code == 200
    row = next(x for x in r.get_json()["settings"] if x["key"] == "sheet_price")
    assert (row["value"], row["layer"]) == (38.5, "project")
