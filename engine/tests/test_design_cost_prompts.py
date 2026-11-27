"""estimate_cost with no prices must not report 0.00: Claude is told to ask with a form (#28)."""

from cardboard_engine.design import DesignSession


def test_zero_prices_are_flagged_for_claude(tmp_path):
    s = DesignSession(home=str(tmp_path))
    s.run_tool("create_shape", {"shape": "box", "width_mm": 100, "depth_mm": 60, "height_mm": 40})
    s.run_tool("unfold", {})
    r = s.run_tool("estimate_cost", {})
    assert r.is_error
    assert "missing settings" in r.text.lower()
    assert "sheet_price" in r.text and "request_inputs" in r.text
    s.settings.set("sheet_price", 5, scope="project", set_by="test")
    s.settings.set("machine_rate", 40, scope="project", set_by="test")
    r = s.run_tool("estimate_cost", {})
    assert not r.is_error
    assert "missing" not in r.text.lower()
