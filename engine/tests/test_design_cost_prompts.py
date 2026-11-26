"""estimate_cost with no prices should say so, so Claude asks instead of reporting 0.00."""

from cardboard_engine.design import DesignSession


def test_zero_prices_are_flagged_for_claude(tmp_path):
    s = DesignSession(home=str(tmp_path))
    s.run_tool("create_shape", {"shape": "box", "width_mm": 100, "depth_mm": 60, "height_mm": 40})
    s.run_tool("unfold", {})
    r = s.run_tool("estimate_cost", {})
    assert not r.is_error
    assert "no sheet price or machine rate" in r.text.lower()
    r = s.run_tool("estimate_cost", {"sheet_price": 5, "machine_rate": 40})
    assert "no sheet price" not in r.text.lower()
