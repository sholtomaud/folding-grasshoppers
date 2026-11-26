import math

import pytest

from cardboard_engine.components.cut_estimate import (
    CutEstimateError,
    CutEstimateInput,
    estimate,
)

# Reference vector (also in README.md):
#   t_s = (2000/20 + 500/40 + 10*0.5) * 1.15 = 135.125 s
KNOWN = CutEstimateInput(
    cut_length=2000,
    score_length=500,
    pierce_count=10,
    sheet_count=2,
    cut_speed=20,
    score_speed=40,
    pierce_time=0.5,
    travel_overhead=0.15,
    sheet_price=3.5,
    machine_rate=60,
)


def test_known_vector():
    r = estimate(KNOWN)
    assert r.time_min == pytest.approx(135.125 / 60)
    assert r.material_cost == pytest.approx(7.0)
    assert r.machine_cost == pytest.approx(135.125 / 3600 * 60)
    assert r.total_cost == pytest.approx(7.0 + 135.125 / 60)


def test_defaults_only_cut_length():
    r = estimate(CutEstimateInput(cut_length=1200))
    # (1200/20) * 1.15 = 69 s
    assert r.time_min == pytest.approx(69 / 60)
    assert r.material_cost == 0
    assert r.machine_cost == 0
    assert r.total_cost == 0


def test_zero_everything_but_speeds_is_valid():
    r = estimate(CutEstimateInput(cut_length=0, sheet_count=0, travel_overhead=0))
    assert r.time_min == 0


@pytest.mark.parametrize("field", ["cut_speed", "score_speed"])
@pytest.mark.parametrize("value", [0, -1])
def test_non_positive_speed_raises(field, value):
    with pytest.raises(CutEstimateError, match="greater than 0"):
        estimate(CutEstimateInput(cut_length=100, **{field: value}))


@pytest.mark.parametrize(
    "field",
    [
        "cut_length",
        "score_length",
        "pierce_count",
        "sheet_count",
        "pierce_time",
        "travel_overhead",
        "sheet_price",
        "machine_rate",
    ],
)
def test_negative_raises(field):
    kwargs = {"cut_length": 100, field: -0.001}
    with pytest.raises(CutEstimateError, match="must not be negative"):
        estimate(CutEstimateInput(**kwargs))


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_non_finite_raises(bad):
    with pytest.raises(CutEstimateError, match="finite"):
        estimate(CutEstimateInput(cut_length=bad))


def test_multiple_errors_reported_together():
    with pytest.raises(CutEstimateError) as exc:
        estimate(CutEstimateInput(cut_length=-1, cut_speed=0))
    assert "Cut length" in str(exc.value) and "Cut speed" in str(exc.value)


def test_from_mapping():
    inp = CutEstimateInput.from_mapping({"cut_length": 10, "sheet_price": 2.5})
    assert inp.cut_length == 10.0 and inp.sheet_price == 2.5 and inp.sheet_count == 1.0


@pytest.mark.parametrize(
    "data, msg",
    [
        ({}, "Missing required field"),
        ({"cut_length": "10"}, "must be a number"),
        ({"cut_length": True}, "must be a number"),
        ({"cut_length": 1, "bogus": 2}, "Unknown field"),
        ([1, 2], "JSON object"),
        (None, "JSON object"),
    ],
)
def test_from_mapping_rejects(data, msg):
    with pytest.raises(CutEstimateError, match=msg):
        CutEstimateInput.from_mapping(data)
