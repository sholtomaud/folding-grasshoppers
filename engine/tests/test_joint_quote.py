"""Joint counts for the quote (#70, part 3).

The engine counts the joints the method made, for the whole design and each
type: joints.count, joints.glue_tab.count, joints.lock_tab.count... A formula
can use them, for example lock tab assembly minutes, so assembly time comes
from the real joints instead of a guess. Scene objects add up.
"""

import pytest

from cardboard_engine.components.unfold import JOINTS
from cardboard_engine.design import DesignSession


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


def cube(session, **kw):
    run(session, "create_shape", shape="box", width_mm=100, depth_mm=100, height_mm=100, **kw)


def test_joint_counts_are_fields_with_units(session):
    units = session.field_units()
    assert units["joints.count"] == "joint"
    for kind in JOINTS:
        assert units[f"joints.{kind}.count"] == "joint"


def test_estimate_records_the_joint_counts(session):
    cube(session)
    run(session, "unfold", joint="lock_tab", edge_joints={"6": "jigsaw_edge"}, thickness_mm=3)
    run(session, "estimate_cost")
    m = session.last_quote["fields"]
    # The quote only records the fields its formulas use; the measurements hold them all.
    measured = session._measure(session._need_parts())
    assert measured["joints.lock_tab.count"] == 6
    assert measured["joints.jigsaw_edge.count"] == 1
    assert measured["joints.glue_tab.count"] == 0
    assert measured["joints.count"] == 7
    assert "joints.count" not in m


def test_a_formula_prices_lock_tabs(session):
    cube(session)
    run(session, "unfold", joint="lock_tab", thickness_mm=3)
    session.settings.set("labour_rate", 60, scope="project", set_by="test")
    session.settings.set("assembly_minutes_per_part", 0.5, scope="project", set_by="test")
    text = run(session, "add_formula", key="lock_assembly", label="Lock tab assembly", unit="AUD",
               expr="round(joints.lock_tab.count * assembly_minutes_per_part * labour_rate, 2)",
               example={"inputs": {"joints.lock_tab.count": 7, "assembly_minutes_per_part": 0.5,
                                   "labour_rate": 60}, "expected": 3.5})
    assert "lock_assembly" in text
    run(session, "estimate_cost")
    lines = {l["key"]: l["value"] for l in session.last_quote["lines"]}
    assert lines["lock_assembly"] == pytest.approx(3.5)


def test_waffles_have_no_joints_and_scenes_add_up(session):
    cube(session, object="box a")
    run(session, "unfold", object="box a", joint="glue_tab")
    cube(session, object="box b")
    run(session, "unfold", object="box b", joint="lock_tab", thickness_mm=3)
    run(session, "create_shape", object="stool", shape="box", width_mm=400, depth_mm=400, height_mm=450)
    run(session, "waffle_slice", object="stool", x_ribs=3, y_ribs=3, thickness_mm=6)
    measured = session._measure(session._need_parts())
    assert measured["joints.glue_tab.count"] == 7
    assert measured["joints.lock_tab.count"] == 7
    assert measured["joints.count"] == 14


def test_counts_survive_save_and_open(session, tmp_path):
    cube(session)
    run(session, "unfold", joint="lock_tab", thickness_mm=3)
    folder = session.save_project(name="Lock box")["folder"]
    fresh = DesignSession(home=str(tmp_path))
    fresh.open_project(folder)
    assert fresh._measure(fresh._need_parts())["joints.lock_tab.count"] == 7
