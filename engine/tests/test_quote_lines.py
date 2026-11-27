"""Full quote lines (#47, roadmap 5.4 step 3).

The cut estimate grows into a quote: board, cutting, print, assembly and
install, packing and freight, design time, margin and GST. Each line is a
built-in formula over settings and engine measurements, so the engine works
out every number. A rate only has to be asked for when something uses it: a
design with no design hours does not need a design rate. The quote is saved as
``quote.json`` and ``quote.csv``.
"""

import csv
import io
import json
import os

import pytest

from cardboard_engine.design import DesignSession
from cardboard_engine.formulas import BUILTIN_FORMULAS, build_quote, quote_csv, reproduce
from cardboard_engine.settings import BUILTIN_DEFINITIONS


def f(value, unit):
    return {"value": value, "unit": unit}


# The issue's rate card: sheet 38.50, machine 90/h, margin 35%, GST 10%, and the rest of a small job.
RATE_CARD = {
    "sheets": f(3, "sheet"), "parts": f(24, "count"), "cut_length": f(12000, "mm"), "score_length": f(2000, "mm"),
    "pierces": f(40, "count"), "cut_speed": f(20, "mm/s"), "score_speed": f(40, "mm/s"),
    "pierce_time": f(0.5, "s"), "travel_overhead": f(0.15, "fraction"),
    "sheet_price": f(38.5, "AUD/sheet"), "machine_rate": f(90, "AUD/h"),
    "print_area": f(0.5, "m2"), "print_rate": f(60, "AUD/m2"),
    "assembly_hours": f(0, "h"), "assembly_minutes_per_part": f(5, "min/part"), "labour_rate": f(55, "AUD/h"),
    "pallet_count": f(1, "pallet"), "freight_rate": f(180, "AUD/pallet"),
    "design_hours": f(4, "h"), "design_rate": f(95, "AUD/h"),
    "margin_pct": f(35, "%"), "gst_pct": f(10, "%"),
}

# Worked out by hand:
#   machine time = (12000/20 + 2000/40 + 40 x 0.5) s x 1.15 = 770.5 s = 0.21402... h; x 90 = 19.2625 -> 19.26
#   board 3 x 38.50 = 115.50; print 0.5 x 60 = 30.00; assembly 24 x 5 min = 2 h x 55 = 110.00
#   packing 1 x 180 = 180.00; design 4 x 95 = 380.00
#   cost 834.76; margin 35% = 292.166 -> 292.17; GST 10% of 1126.93 = 112.693 -> 112.69; total 1239.62
BY_HAND = {"material": 115.50, "machine": 19.26, "print": 30.00, "assembly": 110.00, "packing": 180.00,
           "design": 380.00, "subtotal": 834.76, "margin": 292.17, "gst": 112.69, "total": 1239.62}


def lines(quote):
    return {l["key"]: l["value"] for l in quote["lines"]}


# ---- the formulas -----------------------------------------------------------------


def test_the_quote_has_every_line_of_the_issue_in_order():
    shown = [x["key"] for x in BUILTIN_FORMULAS if x["role"] != "working"]
    assert shown == ["material", "machine", "print", "assembly", "packing", "design", "subtotal", "margin", "gst",
                     "total"]
    labels = {x["key"]: x["label"] for x in BUILTIN_FORMULAS}
    assert labels["print"] == "Print"
    assert labels["assembly"] == "Assembly and install"
    assert labels["packing"] == "Packing and freight"
    assert labels["design"] == "Design time"


def test_rate_card_example_matches_the_hand_calculation_to_the_cent():
    q = build_quote(BUILTIN_FORMULAS, RATE_CARD, "AUD")
    got = lines(q)
    for key, want in BY_HAND.items():
        assert got[key] == pytest.approx(want, abs=1e-9), key
    assert q["total"] == pytest.approx(1239.62, abs=1e-9)
    assert reproduce(q)["total"] == q["total"]


def test_assembly_hours_replace_minutes_per_part():
    q = build_quote(BUILTIN_FORMULAS, dict(RATE_CARD, assembly_hours=f(3, "h")), "AUD")
    assert lines(q)["assembly"] == pytest.approx(165.00)


def test_new_settings_have_safe_defaults():
    for key in ("assembly_hours", "assembly_minutes_per_part", "pallet_count", "design_hours"):
        assert BUILTIN_DEFINITIONS[key].default == 0, key
    for key in ("print_rate", "freight_rate", "design_rate", "labour_rate"):
        assert BUILTIN_DEFINITIONS[key].default is None, f"{key} is a rate: never a guess"
    assert BUILTIN_DEFINITIONS["print_rate"].unit == "AUD/m2"
    assert BUILTIN_DEFINITIONS["freight_rate"].unit == "AUD/pallet"


# ---- in a session ---------------------------------------------------------------


@pytest.fixture
def session(tmp_path):
    s = DesignSession(home=str(tmp_path))
    s.settings.set("sheet_price", 38.5, scope="project", set_by="test")
    s.settings.set("machine_rate", 90, scope="project", set_by="test")
    s.settings.set("margin_pct", 35, scope="project", set_by="test")
    return s


def run(session, tool_name, **args):
    r = session.run_tool(tool_name, args)
    assert not r.is_error, r.text
    return r.text


def box(session):
    run(session, "create_shape", shape="box", width_mm=400, depth_mm=400, height_mm=450)
    run(session, "waffle_slice", x_ribs=3, y_ribs=3, thickness_mm=6)
    run(session, "nest_sheets", sheet_width_mm=1200, sheet_height_mm=1000)


def test_unused_rates_are_not_asked_for(session):
    box(session)
    run(session, "estimate_cost")
    q = session.last_quote
    got = lines(q)
    for key in ("print", "assembly", "packing", "design"):
        assert got[key] == 0, key
    assert got["subtotal"] == pytest.approx(got["material"] + got["machine"])
    assert set(q["unused_settings"]) >= {"print_rate", "labour_rate", "freight_rate", "design_rate"}


def test_a_rate_that_is_used_must_be_asked_for(session):
    box(session)
    session.settings.set("design_hours", 4, scope="project", set_by="test")
    r = session.run_tool("estimate_cost", {})
    assert r.is_error
    assert "design_rate" in r.text and "request_inputs" in r.text
    assert "labour_rate" not in r.text


def test_session_lines_follow_the_settings(session):
    box(session)
    for key, value in (("design_hours", 4), ("design_rate", 95), ("assembly_minutes_per_part", 5),
                       ("labour_rate", 55), ("pallet_count", 1), ("freight_rate", 180)):
        session.settings.set(key, value, scope="project", set_by="test")
    run(session, "estimate_cost")
    q = session.last_quote
    got = lines(q)
    parts = len(session._need_parts().parts)
    assert got["design"] == pytest.approx(380.00)
    assert got["packing"] == pytest.approx(180.00)
    assert got["assembly"] == pytest.approx(round(parts * 5 / 60 * 55, 2))
    assert q["unused_settings"] == ["print_rate"]
    assert q["total"] == pytest.approx(got["subtotal"] + got["margin"] + got["gst"])


# ---- quote.csv ------------------------------------------------------------------


def test_quote_csv_has_the_shown_lines():
    q = build_quote(BUILTIN_FORMULAS, RATE_CARD, "AUD")
    rows = list(csv.reader(io.StringIO(quote_csv(q))))
    assert rows[0] == ["key", "label", "role", "value", "unit"]
    keys = [r[0] for r in rows[1:]]
    assert keys == list(BY_HAND)
    by_key = {r[0]: r for r in rows[1:]}
    assert by_key["total"] == ["total", "Total", "total", "1239.62", "AUD"]
    assert by_key["machine"][3] == "19.26"


def test_save_writes_quote_json_and_csv_that_agree(session):
    box(session)
    run(session, "estimate_cost")
    folder = session.save_project(name="Quote test")["folder"]
    q = json.loads(open(os.path.join(folder, "estimate", "quote.json")).read())
    rows = list(csv.DictReader(open(os.path.join(folder, "estimate", "quote.csv"))))
    assert {r["key"]: float(r["value"]) for r in rows} == {
        l["key"]: round(l["value"], 2) for l in q["lines"] if l["role"] != "working"}
