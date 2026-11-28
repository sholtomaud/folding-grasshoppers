"""Concepts A-D with frozen versions and a compare view (#46), engine side.

A project can hold several concepts (A, B, C...), each its own design: scene,
models, methods, parts, nesting and quote. The brief, settings and formulas
belong to the project. Freezing a concept copies it, in the saved-project
layout, into concepts/<name>/vN/; a frozen version is never edited. Compare
lists every concept's footprint, sheets, build hours, price and brief checks,
from the engine's numbers. Reopening restores every concept and version.
"""

import json
import os

import pytest

from cardboard_engine.design import DesignSession


@pytest.fixture
def session(tmp_path):
    s = DesignSession(home=str(tmp_path))
    for key, value in (("sheet_price", 12.5), ("machine_rate", 60), ("labour_rate", 50),
                       ("assembly_minutes_per_part", 2)):
        s.settings.set(key, value, scope="project", set_by="test")
    return s


def run(session, tool_name, **args):
    r = session.run_tool(tool_name, args)
    assert not r.is_error, r.text
    return r.text


def stool(session, method="waffle_slice", **args):
    run(session, "create_shape", shape="cylinder", width_mm=350, depth_mm=350, height_mm=300, segments=24)
    run(session, method, **args)
    run(session, "nest_sheets", sheet_width_mm=1200, sheet_height_mm=800)
    run(session, "estimate_cost")


def three_concepts(session):
    stool(session, "waffle_slice", x_ribs=4, y_ribs=4, thickness_mm=6)
    run(session, "new_concept", blank=True)
    stool(session, "contour_stack", thickness_mm=50)
    run(session, "new_concept", blank=True)
    run(session, "create_shape", shape="box", width_mm=350, depth_mm=350, height_mm=450)
    run(session, "unfold")
    run(session, "nest_sheets", sheet_width_mm=2400, sheet_height_mm=1200)
    run(session, "estimate_cost")


def test_a_design_with_no_concepts_works_as_before(session):
    stool(session)
    assert session.concepts == []
    assert "no concepts" in run(session, "list_concepts").lower()


def test_new_concepts_get_letters_and_switching_keeps_each_design(session):
    stool(session, "waffle_slice", x_ribs=4, y_ribs=4)
    text = run(session, "new_concept")
    assert "Concept A" in text and "Concept B" in text
    assert [c["name"] for c in session.concepts] == ["Concept A", "Concept B"]
    assert session.active_concept == "Concept B"
    assert session.method == "waffle", "a new concept starts as a copy of the current one"
    run(session, "contour_stack", thickness_mm=20)
    run(session, "switch_concept", name="A")
    assert session.method == "waffle"
    run(session, "switch_concept", name="concept b")
    assert session.method == "contour"
    run(session, "new_concept", name="Lamp", blank=True)
    assert session.model is None and session.parts is None
    assert [c["letter"] for c in session.concepts] == ["A", "B", "C"]
    r = session.run_tool("switch_concept", {"name": "Z"})
    assert r.is_error and "Concept A" in r.text


def test_settings_brief_and_formulas_belong_to_the_project(session):
    run(session, "set_brief", values={"max_height_mm": 1000})
    stool(session)
    run(session, "new_concept", blank=True)
    assert session.brief["max_height_mm"] == 1000
    assert session.settings.resolve("sheet_price")["value"] == 12.5


def test_compare_shows_the_engine_totals_of_each_concept(session):
    three_concepts(session)
    data = session.compare_concepts()
    assert [r["name"] for r in data] == ["Concept A", "Concept B", "Concept C"]
    for row in data:
        assert row["total"] is not None and row["sheets"] >= 1
        assert row["footprint_mm"][0] == pytest.approx(350, abs=1)
        assert row["build_hours"] is not None
    run(session, "switch_concept", name="B")
    assert data[1]["total"] == session.last_quote["total"]
    text = run(session, "compare_concepts")
    for row in data:
        assert row["name"] in text


def test_freezing_keeps_v1_byte_for_byte(session, tmp_path):
    stool(session, "waffle_slice", x_ribs=4, y_ribs=4)
    text = run(session, "freeze_concept")
    assert "v1" in text
    assert [c["name"] for c in session.concepts] == ["Concept A"], "the design becomes concept A"
    folder = session.save_project(name="Acme Expo")["folder"]
    v1 = os.path.join(folder, "concepts", "Concept-A", "v1")
    files = {}
    for root, _, names in os.walk(v1):
        for n in names:
            with open(os.path.join(root, n), "rb") as fh:
                files[os.path.relpath(os.path.join(root, n), v1)] = fh.read()
    assert "project.json" in files and "estimate/quote.json" in files
    assert any(p.endswith(".dxf") for p in files)
    frozen_total = json.loads(files["estimate/quote.json"])["total"]
    # Change the concept and freeze again.
    run(session, "waffle_slice", x_ribs=6, y_ribs=6)
    run(session, "nest_sheets", sheet_width_mm=1200, sheet_height_mm=800)
    run(session, "estimate_cost")
    run(session, "freeze_concept")
    session.save_project(name="Acme Expo")
    for rel, data in files.items():
        with open(os.path.join(v1, rel), "rb") as fh:
            assert fh.read() == data, f"{rel} changed"
    assert os.path.isfile(os.path.join(folder, "concepts", "Concept-A", "v2", "project.json"))
    assert session.concepts[0]["versions"][0]["total"] == frozen_total


def test_a_concept_with_no_model_cannot_be_frozen(session):
    stool(session)
    run(session, "new_concept", blank=True)
    r = session.run_tool("freeze_concept", {})
    assert r.is_error and "model" in r.text


def test_reopening_restores_every_concept_and_version(session, tmp_path):
    three_concepts(session)
    run(session, "freeze_concept", name="A")
    run(session, "switch_concept", name="B")
    before = {r["name"]: r for r in session.compare_concepts()}
    folder = session.save_project(name="Acme Expo")["folder"]
    with open(os.path.join(folder, "project.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    assert manifest["concepts"]["active"] == "Concept B"
    fresh = DesignSession(home=str(tmp_path))
    fresh.open_project(folder)
    assert [c["name"] for c in fresh.concepts] == ["Concept A", "Concept B", "Concept C"]
    assert fresh.active_concept == "Concept B" and fresh.method == "contour"
    after = {r["name"]: r for r in fresh.compare_concepts()}
    for name, row in before.items():
        assert after[name]["total"] == row["total"]
        assert after[name]["sheets"] == row["sheets"]
    assert [v["version"] for v in fresh.concepts[0]["versions"]] == [1]
    run(fresh, "switch_concept", name="C")
    assert fresh.method == "unfold"
    # Saving again keeps the frozen version and the other concepts' files.
    fresh.save_project(name="Acme Expo")
    assert os.path.isfile(os.path.join(folder, "concepts", "Concept-A", "v1", "project.json"))
    again = DesignSession(home=str(tmp_path))
    again.open_project(folder)
    assert again.active_concept == "Concept C"
    run(again, "switch_concept", name="B")
    assert again.method == "contour"


def test_the_state_lists_the_concepts_for_the_strip(session):
    three_concepts(session)
    st = session.state()
    assert [c["letter"] for c in st["concepts"]["items"]] == ["A", "B", "C"]
    assert st["concepts"]["active"] == "Concept C"
    assert all(c["total"] is not None for c in st["concepts"]["items"])
