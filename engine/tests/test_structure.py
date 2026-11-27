"""Indicative structural check for seats, counters and shelves (#45).

Not an engineering sign-off. The vertical capacity of the ribs is the
board's edge crush strength (the supplier's ECT, kN/m) times the edge length
standing on the floor, divided by a safety factor (3 for seats). There is a
warning for wide unbraced rib panels that would buckle before they crush, and
one for counter and shelf tops that span too far between ribs. Board data
comes from settings, and the first build of a new seat should be load-tested.
"""

import pytest

from cardboard_engine.design import TOOLS, DesignSession
from cardboard_engine.settings import BUILTIN_DEFINITIONS
from cardboard_engine.structure import G, StructureError, check_waffle

ECT = 7.0  # kN/m, a double-wall board from the supplier's sheet
SLOT = 6.2  # 6 mm board + 0.2 mm clearance


@pytest.fixture
def session(tmp_path):
    s = DesignSession(home=str(tmp_path))
    s.settings.set("board_ect", ECT, scope="project", set_by="test")
    return s


def run(session, tool_name, **args):
    r = session.run_tool(tool_name, args)
    assert not r.is_error, r.text
    return r.text


def fails(session, tool_name, **args):
    r = session.run_tool(tool_name, args)
    assert r.is_error, r.text
    return r.text


def stool(session, ribs=5, **extra):
    run(session, "create_shape", shape="box", width_mm=400, depth_mm=400, height_mm=450, **extra)
    run(session, "waffle_slice", x_ribs=ribs, y_ribs=ribs, thickness_mm=6)


def by_hand(n, side=400.0, sf=3.0, load_kg=100.0):
    """X ribs stand on their full length; Y ribs are slotted from the bottom, n slots each."""
    edge_mm = n * side + n * (side - n * SLOT)
    capacity_kn = ECT * edge_mm / 1000 / sf
    return edge_mm, capacity_kn / (load_kg * G / 1000)


# ---- settings and tool ------------------------------------------------------


def test_board_settings_and_tool_are_offered():
    for key in ("board_ect", "seat_safety_factor", "load_safety_factor", "max_panel_ratio", "max_span_ratio"):
        assert key in BUILTIN_DEFINITIONS, key
    assert BUILTIN_DEFINITIONS["board_ect"].default is None, "ECT comes from the supplier, never a guess"
    assert BUILTIN_DEFINITIONS["board_ect"].unit == "kN/m"
    assert BUILTIN_DEFINITIONS["seat_safety_factor"].default == 3
    tool = {t["name"]: t for t in TOOLS}["check_structure"]
    props = tool["input_schema"]["properties"]
    assert props["use"]["enum"] == ["seat", "counter", "shelf"]
    assert "object" in props and "load_kg" in props


# ---- the worked example ----------------------------------------------------------


def test_five_by_five_stool_against_the_hand_calculation(session):
    stool(session)
    edge, n = by_hand(5)
    assert edge == pytest.approx(3845.0)
    assert n == pytest.approx(9.1486, abs=1e-3)
    text = run(session, "check_structure", use="seat", load_kg=100)
    r = session.last_structure
    assert r["loaded_edge_mm"] == pytest.approx(edge, abs=0.01)
    assert r["capacity_ratio"] == pytest.approx(n, rel=1e-6)
    assert r["status"] == "pass"
    assert "about 9.1 x the 100 kg design load" in text
    assert "ECT 7 kN/m x 3845 mm loaded edge / safety factor 3" in text
    assert "indicative" in text.lower() and "load-test" in text.lower()


def test_halving_the_ribs_lowers_the_capacity_in_proportion(session):
    stool(session, ribs=6)
    run(session, "check_structure", use="seat", load_kg=100)
    six = session.last_structure
    stool(session, ribs=3)
    run(session, "check_structure", use="seat", load_kg=100)
    three = session.last_structure
    (e6, n6), (e3, n3) = by_hand(6), by_hand(3)
    assert six["capacity_ratio"] == pytest.approx(n6, rel=1e-6)
    assert three["capacity_ratio"] == pytest.approx(n3, rel=1e-6)
    assert three["capacity_ratio"] / six["capacity_ratio"] == pytest.approx(e3 / e6, rel=1e-9)
    assert three["capacity_ratio"] / six["capacity_ratio"] == pytest.approx(0.5, abs=0.02)


def test_a_weak_seat_fails(session):
    stool(session, ribs=1)
    text = run(session, "check_structure", use="seat", load_kg=400)
    assert session.last_structure["status"] == "fail"
    assert session.last_structure["capacity_ratio"] < 1
    assert "FAIL" in text and "more ribs" in text


def test_the_load_comes_from_the_brief(session):
    stool(session)
    run(session, "set_brief", values={"seats": 4, "load_per_seat_kg": 120})
    run(session, "check_structure", use="seat")
    assert session.last_structure["load_kg"] == 120
    assert session.last_structure["capacity_ratio"] == pytest.approx(by_hand(5, load_kg=120)[1], rel=1e-6)
    assert "shelf_load_kg" in fails(session, "check_structure", use="shelf")


def test_safety_factor_is_a_setting(session):
    stool(session)
    session.settings.set("seat_safety_factor", 4, scope="project", set_by="test")
    run(session, "check_structure", use="seat", load_kg=100)
    assert session.last_structure["capacity_ratio"] == pytest.approx(by_hand(5, sf=4)[1], rel=1e-6)
    assert "cannot be given" in fails(session, "check_structure", use="seat", load_kg=100, board_ect=20)


# ---- warnings ---------------------------------------------------------------------


def test_unbraced_ribs_get_a_slenderness_warning(session):
    run(session, "create_shape", shape="box", width_mm=400, depth_mm=400, height_mm=450)
    run(session, "waffle_slice", x_ribs=3, y_ribs=0, thickness_mm=6)
    text = run(session, "check_structure", use="seat", load_kg=100)
    r = session.last_structure
    assert r["status"] == "warn"
    assert r["panel_ratio"] == pytest.approx(400 / 6)
    assert "buckle" in text and "400 mm" in text


def test_counter_top_span_warning(session):
    run(session, "create_shape", shape="box", width_mm=1800, depth_mm=600, height_mm=1100)
    run(session, "waffle_slice", x_ribs=4, y_ribs=0, thickness_mm=6)
    text = run(session, "check_structure", use="counter", load_kg=50)
    r = session.last_structure
    assert r["span_mm"] == pytest.approx(360)
    assert "span" in text and "360 mm" in text
    assert r["status"] == "warn"
    run(session, "waffle_slice", x_ribs=8, y_ribs=3, thickness_mm=6)
    run(session, "check_structure", use="counter", load_kg=50)
    assert session.last_structure["span_mm"] == pytest.approx(150)
    assert session.last_structure["status"] == "pass"


def test_seats_have_no_span_check(session):
    stool(session)
    run(session, "check_structure", use="seat", load_kg=100)
    assert session.last_structure["span_mm"] is None


# ---- what cannot be checked ----------------------------------------------------------


def test_without_the_supplier_ect_claude_must_ask(tmp_path):
    s = DesignSession(home=str(tmp_path))
    stool(s)
    text = fails(s, "check_structure", use="seat", load_kg=100)
    assert "board_ect" in text and "supplier" in text


def test_only_waffles_can_be_checked(session):
    run(session, "create_shape", shape="box", width_mm=400, depth_mm=400, height_mm=450)
    assert "waffle" in fails(session, "check_structure", use="seat", load_kg=100)
    run(session, "contour_stack", thickness_mm=6)
    assert "waffle" in fails(session, "check_structure", use="seat", load_kg=100)


def test_each_scene_object_is_checked_on_its_own(session):
    run(session, "create_shape", object="counter", shape="box", width_mm=1800, depth_mm=600, height_mm=1100)
    run(session, "waffle_slice", object="counter", x_ribs=4, y_ribs=2, thickness_mm=6)
    run(session, "create_shape", object="stool 1", shape="box", width_mm=400, depth_mm=400, height_mm=450)
    run(session, "waffle_slice", object="stool 1", x_ribs=5, y_ribs=5, thickness_mm=6)
    text = run(session, "check_structure", object="stool 1", use="seat", load_kg=100)
    assert session.last_structure["capacity_ratio"] == pytest.approx(by_hand(5)[1], rel=1e-6)
    assert "stool 1" in text
    run(session, "check_structure", object="counter", use="counter", load_kg=50)
    assert session.last_structure["object"] == "counter"


def test_check_does_not_change_a_saved_project(session):
    stool(session)
    session.save_project(name="Stool")
    run(session, "check_structure", use="seat", load_kg=100)
    assert session.state()["project"]["unsaved_changes"] is False


def test_pure_function_checks_its_inputs():
    with pytest.raises(StructureError, match="use"):
        check_waffle({}, 0.0, 6.0, use="bench", load_kg=100, ect_kn_m=7, safety_factor=3)
    with pytest.raises(StructureError, match="floor"):
        check_waffle({"X1": [[(0, 0, 10), (0, 100, 10), (0, 100, 50)]]}, 0.0, 6.0, use="seat", load_kg=100,
                     ect_kn_m=7, safety_factor=3)
