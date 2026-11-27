"""Design brief with constraints and pass/fail checks (#44).

The brief holds the stand's limits (footprint, height, venue), the items
wanted, loads and seating, board and sheet, shipping, assembly time, budget
and branding. Claude fills it in with set_brief; people edit it in the Brief
view. After every change the engine runs the checks, and Claude is told about
a failing check after each design step.
"""

import json
import os

import pytest

from cardboard_engine.brief import BriefError, check_brief, clean_values, FIELDS
from cardboard_engine.design import TOOLS, DesignSession
from cardboard_engine.views import ACTIONS


@pytest.fixture
def session(tmp_path):
    s = DesignSession(home=str(tmp_path))
    s.settings.set("sheet_price", 12.5, scope="project", set_by="test")
    s.settings.set("machine_rate", 60, scope="project", set_by="test")
    return s


def run(session, tool_name, **args):
    r = session.run_tool(tool_name, args)
    assert not r.is_error, r.text
    return r.text


def checks_by_key(session):
    return {c["key"]: c for c in session.brief_checks()}


def tall_box(session, height):
    run(session, "create_shape", shape="box", width_mm=1800, depth_mm=600, height_mm=height)


# ---- the brief itself ------------------------------------------------------


def test_tools_and_action_are_offered():
    names = {t["name"] for t in TOOLS}
    assert {"set_brief", "check_brief"} <= names
    assert "check_brief" in ACTIONS


def test_fields_cover_the_issue():
    keys = set(FIELDS)
    for k in ("footprint_width_mm", "footprint_depth_mm", "max_height_mm", "venue_limits", "items", "seats",
              "load_per_seat_kg", "board_grade", "sheet_width_mm", "sheet_height_mm", "pallet_length_mm",
              "pallet_width_mm", "assembly_time_h", "budget", "branding"):
        assert k in keys, k


def test_values_are_checked():
    assert clean_values({"max_height_mm": 2400, "venue_limits": "No glue on the floor"}) == {
        "max_height_mm": 2400, "venue_limits": "No glue on the floor"}
    with pytest.raises(BriefError, match="max_height_mm must be a number"):
        clean_values({"max_height_mm": "tall"})
    with pytest.raises(BriefError, match="greater than 0"):
        clean_values({"max_height_mm": -5})
    with pytest.raises(BriefError, match="seats must be a whole number"):
        clean_values({"seats": 2.5})
    with pytest.raises(BriefError, match="Unknown brief field"):
        clean_values({"colour": "red"})
    with pytest.raises(BriefError, match="items"):
        clean_values({"items": [{"quantity": 2}]})
    assert clean_values({"items": [{"name": "stool", "quantity": 4}]}) == {
        "items": [{"name": "stool", "quantity": 4, "notes": ""}]}


def test_set_brief_merges_and_clears(session):
    run(session, "set_brief", values={"max_height_mm": 2400, "budget": 8000})
    run(session, "set_brief", values={"branding": "Acme blue"})
    assert session.brief == {"max_height_mm": 2400, "budget": 8000, "branding": "Acme blue"}
    run(session, "set_brief", values={"budget": None})
    assert "budget" not in session.brief
    bad = session.run_tool("set_brief", {"values": {"max_height_mm": "x"}})
    assert bad.is_error and session.brief["max_height_mm"] == 2400


# ---- checks ------------------------------------------------------------------


def test_height_check_passes_then_fails(session):
    run(session, "set_brief", values={"max_height_mm": 2400})
    tall_box(session, 2380)
    c = checks_by_key(session)["height"]
    assert c["status"] == "pass" and c["message"] == "2380 of 2400 mm"
    text = run(session, "scale_model", height_mm=2450)
    c = checks_by_key(session)["height"]
    assert c["status"] == "fail" and c["message"] == "2450 of 2400 mm"
    assert "Brief check failed" in text and "2450 of 2400 mm" in text


def test_height_without_a_model_is_a_warning(session):
    run(session, "set_brief", values={"max_height_mm": 2400})
    assert checks_by_key(session)["height"]["status"] == "warn"


def test_footprint_check_allows_turning_the_stand(session):
    run(session, "set_brief", values={"footprint_width_mm": 700, "footprint_depth_mm": 2000})
    tall_box(session, 1100)
    c = checks_by_key(session)["footprint"]
    assert c["status"] == "pass" and "turned" in c["message"]
    run(session, "set_brief", values={"footprint_width_mm": 1500, "footprint_depth_mm": 1000})
    assert checks_by_key(session)["footprint"]["status"] == "fail"


def test_budget_check_against_the_engine_quote():
    facts = {"quote_total": 8650.0, "currency": "AUD"}
    c = {x["key"]: x for x in check_brief({"budget": 8000}, facts)}["budget"]
    assert c["status"] == "fail" and c["message"] == "8650.00 of 8000.00 AUD"
    c = {x["key"]: x for x in check_brief({"budget": 9000}, facts)}["budget"]
    assert c["status"] == "pass"
    c = {x["key"]: x for x in check_brief({"budget": 9000}, {})}["budget"]
    assert c["status"] == "warn" and "estimate_cost" in c["message"]


def test_budget_check_uses_the_real_quote(session):
    tall_box(session, 1100)
    run(session, "waffle_slice", x_ribs=3, y_ribs=2, thickness_mm=6)
    run(session, "nest_sheets", sheet_width_mm=2400, sheet_height_mm=1200)
    run(session, "estimate_cost")
    total = session.last_quote["total"]
    run(session, "set_brief", values={"budget": round(total / 2, 2)})
    assert checks_by_key(session)["budget"]["status"] == "fail"


def test_pallet_check_reports_the_stack_height():
    facts = {"sheet_count": 12, "thickness_mm": 6.0, "sheet_size": [1200, 800]}
    c = {x["key"]: x for x in check_brief({"pallet_length_mm": 1200, "pallet_width_mm": 1000}, facts)}["pallet"]
    assert c["status"] == "pass"
    assert c["message"] == "12 sheets x 6 mm = 72 mm stack on a 1200 x 1000 mm pallet"
    big = dict(facts, sheet_size=[2500, 1250])
    c = {x["key"]: x for x in check_brief({"pallet_length_mm": 1200, "pallet_width_mm": 1000}, big)}["pallet"]
    assert c["status"] == "warn" and "overhang" in c["message"]
    c = {x["key"]: x for x in check_brief({"pallet_length_mm": 1200, "pallet_width_mm": 1000,
                                           "pallet_max_height_mm": 50}, facts)}["pallet"]
    assert c["status"] == "fail" and "72 of 50 mm" in c["message"]


def test_pallet_check_from_the_session(session):
    run(session, "set_brief", values={"pallet_length_mm": 1200, "pallet_width_mm": 1000})
    run(session, "create_shape", shape="box", width_mm=900, depth_mm=600, height_mm=700)
    run(session, "waffle_slice", x_ribs=3, y_ribs=2, thickness_mm=6)
    run(session, "nest_sheets", sheet_width_mm=1200, sheet_height_mm=1000)
    c = checks_by_key(session)["pallet"]
    n = session.nested["sheet_count"]
    assert c["message"].startswith(f"{n} sheets x 6 mm = {6 * n} mm stack")


def test_sheet_size_check(session):
    run(session, "set_brief", values={"sheet_width_mm": 2400, "sheet_height_mm": 1200})
    tall_box(session, 1100)
    run(session, "waffle_slice", x_ribs=3, y_ribs=2, thickness_mm=6)
    assert checks_by_key(session)["sheet"]["status"] == "warn"
    run(session, "nest_sheets", sheet_width_mm=1200, sheet_height_mm=2400)
    assert checks_by_key(session)["sheet"]["status"] == "pass"


def test_only_set_fields_are_checked(session):
    assert session.brief_checks() == []
    run(session, "set_brief", values={"branding": "Acme blue"})
    assert session.brief_checks() == []


def test_check_brief_tool_lists_every_check(session):
    run(session, "set_brief", values={"max_height_mm": 2400, "budget": 8000})
    tall_box(session, 2450)
    text = run(session, "check_brief")
    assert "FAIL height: 2450 of 2400 mm" in text
    assert "WARN budget" in text


def test_scene_height_counts_every_object(session):
    run(session, "set_brief", values={"max_height_mm": 2400})
    run(session, "create_shape", object="wall", shape="box", width_mm=3000, depth_mm=100, height_mm=2300)
    run(session, "create_shape", object="sign", shape="box", width_mm=1000, depth_mm=50, height_mm=300)
    run(session, "move_object", name="sign", x_mm=0, y_mm=0, z_mm=2200)
    assert checks_by_key(session)["height"]["message"] == "2500 of 2400 mm"


# ---- views, files, HTTP --------------------------------------------------------


def test_brief_view_and_bindings(session):
    run(session, "set_brief", values={"max_height_mm": 2400, "items": [{"name": "stool", "quantity": 4}]})
    tall_box(session, 2450)
    ids = [v["id"] for v in session.views.listing()]
    assert "brief" in ids
    out = session.views.render("brief")
    assert out["data"]["brief.max_height_mm"]["value"] == 2400
    assert out["data"]["brief.max_height_mm"]["unit"] == "mm"
    assert out["data"]["brief.items"] == [{"name": "stool", "quantity": 4, "notes": ""}]
    checks = out["data"]["brief.checks"]
    assert checks[0]["status"] == "fail" and checks[0]["label"] == "Height"


def test_save_writes_brief_json_and_md_and_open_restores_it(session, tmp_path):
    run(session, "set_brief", values={"max_height_mm": 2400, "budget": 8000, "branding": "Acme blue",
                                      "items": [{"name": "counter", "quantity": 1, "notes": "with till"}]})
    tall_box(session, 2380)
    folder = session.save_project(name="Acme")["folder"]
    data = json.loads(open(os.path.join(folder, "brief.json")).read())
    assert data["schema"] == "foldtheory.brief/1"
    assert data["values"]["max_height_mm"] == 2400
    md = open(os.path.join(folder, "brief.md")).read()
    assert "# Brief" in md and "Maximum height: 2400 mm" in md and "counter (1): with till" in md
    assert "pass" in md.lower()
    fresh = DesignSession(home=str(tmp_path))
    fresh.open_project(folder)
    assert fresh.brief["branding"] == "Acme blue"
    assert {c["key"]: c for c in fresh.brief_checks()}["height"]["status"] == "pass"


def test_reset_clears_the_brief(session):
    run(session, "set_brief", values={"max_height_mm": 2400})
    session.reset()
    assert session.brief == {}


def test_brief_routes(client):
    r = client.post("/api/brief", json={"values": {"max_height_mm": 2400}})
    assert r.status_code == 200, r.data
    body = client.get("/api/brief").get_json()
    assert body["values"] == {"max_height_mm": 2400}
    assert body["checks"][0]["key"] == "height"
    assert any(f["key"] == "budget" for f in body["fields"])
    r = client.post("/api/brief", json={"values": {"max_height_mm": "x"}})
    assert r.status_code == 422 and "number" in r.get_json()["message"]
    assert client.post("/api/brief", json=[1]).status_code == 400
