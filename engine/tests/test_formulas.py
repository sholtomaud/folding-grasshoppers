"""Formula engine (#29): deterministic quote lines from fields, in JSON.

A small, safe expression language: arithmetic, min, max, round, ceil,
floor, abs, if, sum and count over parts or sheets, with units checked.
No Python eval, no loops, no names outside the fields and formulas.
"""

import json
import os

import pytest

from cardboard_engine.formulas import (
    BUILTIN_FORMULAS, FORMULAS_SCHEMA, FormulaError, check_example, evaluate, evaluate_all, parse, reproduce,
    formula_version, validate_formula,
)
from cardboard_engine.design import DesignSession


def F(key, expr, unit, label=None, **kw):
    return {"key": key, "label": label or key, "expr": expr, "unit": unit, **kw}


def fields(**kw):
    """name=(value, unit) -> field mapping."""
    return {k: {"value": v, "unit": u} for k, (v, u) in kw.items()}


def test_board_cost_is_sheets_times_price():
    out = evaluate("sheets * sheet_price", fields(sheets=(3, "sheet"), sheet_price=(38.50, "AUD/sheet")), "AUD")
    assert out == pytest.approx(115.50)


def test_install_hours_rounds_up():
    out = evaluate("ceil(parts / 40)", fields(parts=(95, "count")), "h")
    assert out == 3


def test_adding_minutes_to_area_is_a_unit_error():
    with pytest.raises(FormulaError) as e:
        evaluate("cut_minutes + board_area", fields(cut_minutes=(12, "min"), board_area=(5000, "mm2")), "min")
    assert "unit" in str(e.value).lower()


def test_result_must_match_the_declared_unit():
    with pytest.raises(FormulaError) as e:
        evaluate("sheets * sheet_price", fields(sheets=(3, "sheet"), sheet_price=(38.5, "AUD/sheet")), "min")
    assert "unit" in str(e.value).lower()


def test_units_convert():
    # 90 s of cutting at 60 AUD/h is 1.50
    out = evaluate("cut_time * machine_rate", fields(cut_time=(1.5, "min"), machine_rate=(60, "AUD/h")), "AUD")
    assert out == pytest.approx(1.5)
    assert evaluate("length", fields(length=(2.5, "m")), "mm") == pytest.approx(2500)
    assert evaluate("gst_pct * 200", fields(gst_pct=(10, "%")), "") == pytest.approx(20)


@pytest.mark.parametrize("expr", [
    "__import__('os')",
    "__import__(os)",
    "open(x)",
    "x.__class__",
    "[1, 2]",
    "lambda: 1",
    "x; y",
    "1 if x else 2",
    "x = 1",
    "a ** b",
    "'text'",
])
def test_anything_outside_the_grammar_is_rejected_when_parsed(expr):
    with pytest.raises(FormulaError):
        parse(expr)


def test_functions():
    f = fields(a=(3, ""), b=(7, ""), m=(12.345, "AUD"))
    assert evaluate("min(a, b) + max(a, b)", f, "") == 10
    assert evaluate("round(m, 2)", f, "AUD") == pytest.approx(12.35)
    assert evaluate("round(m)", f, "AUD") == 12
    assert evaluate("floor(b / a)", f, "") == 2
    assert evaluate("abs(a - b)", f, "") == 4
    assert evaluate("if(a > b, a, b)", f, "") == 7
    assert evaluate("if(a <= b and b != 0, 1, 0)", f, "") == 1
    assert evaluate("-a + b ^ 2", f, "") == 46


def test_sum_and_count_over_parts():
    f = {"parts.area": {"value": [100.0, 250.0, 50.0], "unit": "mm2"}}
    assert evaluate("sum(parts.area)", f, "mm2") == 400
    assert evaluate("count(parts.area)", f, "") == 3
    assert evaluate("max(parts.area)", f, "mm2") == 250
    assert evaluate("sum(parts.area * 2)", f, "mm2") == 800
    with pytest.raises(FormulaError):
        evaluate("parts.area", f, "mm2")  # a list is not a single number


def test_unknown_name_is_reported():
    with pytest.raises(FormulaError) as e:
        evaluate("sheets * sheet_prise", fields(sheets=(3, "sheet"), sheet_price=(1, "AUD")), "AUD")
    assert "sheet_prise" in str(e.value)


def test_division_by_zero_is_an_error_not_infinity():
    with pytest.raises(FormulaError):
        evaluate("a / b", fields(a=(1, ""), b=(0, "")), "")


def test_formulas_can_use_each_other_in_any_order():
    fs = [F("total", "material + machine", "AUD"), F("material", "sheets * sheet_price", "AUD"),
          F("machine", "minutes * rate", "AUD")]
    out = evaluate_all(fs, fields(sheets=(2, ""), sheet_price=(10, "AUD"), minutes=(30, "min"), rate=(60, "AUD/h")))
    assert out["total"]["value"] == pytest.approx(50)
    assert out["material"]["version"] == formula_version(fs[1])


def test_cycles_are_rejected():
    fs = [F("a", "b + 1", ""), F("b", "a + 1", "")]
    with pytest.raises(FormulaError) as e:
        evaluate_all(fs, {})
    assert "cycle" in str(e.value).lower() or "circular" in str(e.value).lower()


def test_a_formula_needs_a_worked_example_that_the_engine_checks():
    good = F("glue", "joints * glue_per_joint", "AUD", example={
        "inputs": {"joints": {"value": 40, "unit": "count"}, "glue_per_joint": {"value": 0.15, "unit": "AUD"}},
        "expected": 6.0})
    check_example(good)
    bad = {**good, "example": {**good["example"], "expected": 7.0}}
    with pytest.raises(FormulaError) as e:
        check_example(bad)
    assert "6" in str(e.value) and "7" in str(e.value)
    with pytest.raises(FormulaError):
        validate_formula({**good, "example": None})


def test_builtin_formulas_check_their_own_examples():
    assert BUILTIN_FORMULAS
    for f in BUILTIN_FORMULAS:
        validate_formula(f)  # raises if its worked example is wrong


def test_version_changes_with_the_expression():
    a = F("x", "a + 1", "")
    assert formula_version(a) == formula_version(dict(a))
    assert formula_version(a) != formula_version({**a, "expr": "a + 2"})


def test_an_old_quote_reproduces_after_the_formulas_change(tmp_path):
    s = DesignSession(home=str(tmp_path))
    s.settings.set("sheet_price", 38.5, scope="project", set_by="SM")
    s.settings.set("machine_rate", 60, scope="project", set_by="SM")
    s.run_tool("create_shape", {"shape": "box", "width_mm": 200, "depth_mm": 100, "height_mm": 80})
    s.run_tool("unfold", {})
    r = s.run_tool("estimate_cost", {})
    assert not r.is_error, r.text
    quote = json.loads(json.dumps(s.last_quote))
    total = quote["total"]
    s.formulas.save_formula(F("material", "sheets * sheet_price * 2", "AUD", example={
        "inputs": {"sheets": {"value": 1, "unit": "sheet"}, "sheet_price": {"value": 1, "unit": "AUD/sheet"}},
        "expected": 2}), scope="all", set_by="SM")
    s.run_tool("estimate_cost", {})
    assert s.last_quote["total"] != total
    again = reproduce(quote)
    assert again["total"] == total
    assert {line["key"]: line["version"] for line in again["lines"]} == \
        {line["key"]: line["version"] for line in quote["lines"]}


def test_saved_formulas_are_json_in_settings(tmp_path):
    s = DesignSession(home=str(tmp_path))
    s.formulas.save_formula(F("install_hours", "ceil(parts / 40)", "h", example={
        "inputs": {"parts": {"value": 95, "unit": "count"}}, "expected": 3}), scope="all", set_by="SM")
    path = os.path.join(str(tmp_path), "Documents", "Fold Theory", "Settings", "formulas.json")
    data = json.load(open(path))
    assert data["schema"] == FORMULAS_SCHEMA
    assert [f["key"] for f in data["formulas"]] == ["install_hours"]
    assert data["formulas"][0]["set_by"] == "SM"


def test_add_formula_tool_checks_the_example(tmp_path):
    s = DesignSession(home=str(tmp_path))
    r = s.run_tool("add_formula", {"key": "install_hours", "label": "Install crew hours", "expr": "ceil(parts / 40)",
                                   "unit": "h", "example": {"inputs": {"parts": 95}, "expected": 4}})
    assert r.is_error
    assert "3" in r.text
    r = s.run_tool("add_formula", {"key": "install_hours", "label": "Install crew hours", "expr": "ceil(parts / 40)",
                                   "unit": "h", "example": {"inputs": {"parts": 95}, "expected": 3}})
    assert not r.is_error, r.text
    assert any(f["key"] == "install_hours" for f in s.formulas.all())


def test_add_formula_rejects_unknown_fields(tmp_path):
    s = DesignSession(home=str(tmp_path))
    r = s.run_tool("add_formula", {"key": "x", "label": "X", "expr": "wibble * 2", "unit": "AUD",
                                   "example": {"inputs": {"wibble": 1}, "expected": 2}})
    assert r.is_error
    assert "wibble" in r.text
