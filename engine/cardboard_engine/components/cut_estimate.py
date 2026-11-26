"""Cut time and cost estimate for a laser/knife cutter.

Units: lengths in mm, speeds in mm/s, times in seconds, rate per hour.

    t_s   = (Lc / Vc + Ls / Vs + N * Tp) * (1 + k)
    T     = t_s / 60
    Cm    = S * Ps
    Cmach = (t_s / 3600) * R
    C     = Cm + Cmach
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, fields


class CutEstimateError(ValueError):
    """Raised for invalid inputs. Hops shows the message as a component error."""


@dataclass(frozen=True)
class CutEstimateInput:
    cut_length: float  # Lc, mm (required)
    score_length: float = 0.0  # Ls, mm
    pierce_count: float = 0.0  # N
    sheet_count: float = 1.0  # S
    cut_speed: float = 20.0  # Vc, mm/s
    score_speed: float = 40.0  # Vs, mm/s
    pierce_time: float = 0.5  # Tp, s
    travel_overhead: float = 0.15  # k, fraction
    sheet_price: float = 0.0  # Ps
    machine_rate: float = 0.0  # R, per hour

    @classmethod
    def from_mapping(cls, data: dict) -> "CutEstimateInput":
        """Build from a plain JSON object (snake_case keys). Unknown keys are rejected."""
        if not isinstance(data, dict):
            raise CutEstimateError("Request body must be a JSON object")
        names = {f.name for f in fields(cls)}
        unknown = sorted(set(data) - names)
        if unknown:
            raise CutEstimateError(f"Unknown field(s): {', '.join(unknown)}")
        if "cut_length" not in data:
            raise CutEstimateError("Missing required field: cut_length")
        kwargs = {}
        for key, value in data.items():
            # bool is an int subclass; reject it explicitly.
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise CutEstimateError(f"{key} must be a number")
            kwargs[key] = float(value)
        return cls(**kwargs)


@dataclass(frozen=True)
class CutEstimateResult:
    time_min: float  # T
    material_cost: float  # Cm
    machine_cost: float  # Cmach
    total_cost: float  # C

    def to_dict(self) -> dict:
        return asdict(self)


_LABELS = {
    "cut_length": "Cut length (Lc)",
    "score_length": "Score length (Ls)",
    "pierce_count": "Pierce count (N)",
    "sheet_count": "Sheet count (S)",
    "cut_speed": "Cut speed (Vc)",
    "score_speed": "Score speed (Vs)",
    "pierce_time": "Pierce time (Tp)",
    "travel_overhead": "Travel overhead (k)",
    "sheet_price": "Sheet price (Ps)",
    "machine_rate": "Machine rate (R)",
}

_MUST_BE_POSITIVE = ("cut_speed", "score_speed")


def validate(inp: CutEstimateInput) -> None:
    errors = []
    for f in fields(inp):
        value = getattr(inp, f.name)
        label = _LABELS[f.name]
        if not math.isfinite(value):
            errors.append(f"{label} must be a finite number")
        elif f.name in _MUST_BE_POSITIVE and value <= 0:
            errors.append(f"{label} must be greater than 0 (got {value:g})")
        elif value < 0:
            errors.append(f"{label} must not be negative (got {value:g})")
    if errors:
        raise CutEstimateError("; ".join(errors))


def estimate(inp: CutEstimateInput) -> CutEstimateResult:
    """Pure estimate. Raises CutEstimateError on invalid input; never returns zeros silently."""
    validate(inp)
    t_s = (
        inp.cut_length / inp.cut_speed
        + inp.score_length / inp.score_speed
        + inp.pierce_count * inp.pierce_time
    ) * (1.0 + inp.travel_overhead)
    material = inp.sheet_count * inp.sheet_price
    machine = (t_s / 3600.0) * inp.machine_rate
    return CutEstimateResult(
        time_min=t_s / 60.0,
        material_cost=material,
        machine_cost=machine,
        total_cost=material + machine,
    )
