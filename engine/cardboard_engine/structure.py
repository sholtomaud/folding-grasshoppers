"""Indicative structural check for waffle seats, counters and shelves (#45).

This is not an engineering sign-off. It stops obviously weak designs early
and lets Claude choose rib count and spacing for a load. Every number is
worked out here, so Claude repeats it instead of doing the arithmetic.

- Capacity: the board's edge crush strength (the supplier's ECT, kN/m) times
  the rib edge length standing on the floor, divided by a safety factor (3
  for seats, which take impact loads). The result is "about N x the design
  load"; under 1 fails.
- Slenderness: a rib panel between the cross ribs that brace it (or its
  height, if that is less) is compared with the board thickness. A wide thin
  panel buckles before the board crushes, so a ratio over the limit warns.
- Span: for counters and shelves, the top spans between ribs. The shorter of
  the largest gaps between X ribs and between Y ribs is compared with the
  board thickness, and a ratio over the limit warns.

The ribs are the waffle's slotted outlines in model space (no kerf): X ribs
lie in a plane of constant x, Y ribs in a plane of constant y.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

G = 9.80665  # m/s2
USES = ("seat", "counter", "shelf")
FLOOR_TOL = 0.5  # mm: an edge this close to the bottom of the model stands on the floor


class StructureError(ValueError):
    """A structural check that cannot be done. The message is for Claude and the designer."""


def _num(v: float) -> str:
    r = round(float(v), 1)
    return str(int(r)) if r == int(r) else f"{r:.1f}"


def _gaps(lo: float, hi: float, positions: Sequence[float]) -> List[float]:
    pts = [lo] + sorted(p for p in positions if lo < p < hi) + [hi]
    return [b - a for a, b in zip(pts, pts[1:])]


def _rib_axis(pid: str) -> Optional[int]:
    head = pid.split("-")[-1][:1]
    return {"X": 0, "Y": 1}.get(head)


def check_waffle(ribs3d: Dict[str, list], floor_z: float, thickness: float, *, use: str, load_kg: float,
                 ect_kn_m: float, safety_factor: float, max_panel_ratio: float = 50.0,
                 max_span_ratio: float = 50.0) -> dict:
    """The check for one waffle object. ``ribs3d``: rib id -> loops of (x, y, z) in mm."""
    if use not in USES:
        raise StructureError(f"use must be one of {', '.join(USES)} (got {use!r})")
    if not ribs3d:
        raise StructureError("The object has no ribs; make them with waffle_slice first")
    if thickness <= 0:
        raise StructureError("The board thickness is not known; slice the model again with waffle_slice")
    ribs: List[Tuple[int, float, Tuple[float, float], float]] = []  # axis, position, (u lo, u hi), height
    edge = 0.0
    for pid, loops in ribs3d.items():
        axis = _rib_axis(pid)
        pts = [p for loop in loops for p in loop]
        if axis is None or not pts:
            continue
        u = [p[1 - axis] for p in pts]
        z = [p[2] for p in pts]
        ribs.append((axis, pts[0][axis], (min(u), max(u)), max(z) - min(z)))
        for loop in loops:
            for a, b in zip(loop, list(loop[1:]) + [loop[0]]):
                if a[2] <= floor_z + FLOOR_TOL and b[2] <= floor_z + FLOOR_TOL:
                    edge += abs(b[1 - axis] - a[1 - axis])
    if edge <= 0:
        raise StructureError("No rib stands on the floor (the bottom of the model), so it cannot carry a load")

    load_kn = load_kg * G / 1000
    capacity_kn = ect_kn_m * edge / 1000 / safety_factor
    ratio = capacity_kn / load_kn

    positions = {0: [r[1] for r in ribs if r[0] == 0], 1: [r[1] for r in ribs if r[0] == 1]}
    worst_panel, worst_rib = 0.0, None
    for axis, _, (lo, hi), height in ribs:
        free = min(max(_gaps(lo, hi, positions[1 - axis])), height)
        if free > worst_panel:
            worst_panel, worst_rib = free, ("X" if axis == 0 else "Y")
    panel_ratio = worst_panel / thickness

    span = None
    if use != "seat":
        largest = []
        for axis in (0, 1):
            here = [r for r in ribs if r[0] == 1 - axis]  # ribs across this axis bound its extent
            lo = min((r[2][0] for r in here), default=None)
            hi = max((r[2][1] for r in here), default=None)
            if lo is None:
                lo = min(r[1] for r in ribs if r[0] == axis)
                hi = max(r[1] for r in ribs if r[0] == axis)
            largest.append(max(_gaps(lo, hi, positions[axis])) if hi > lo else 0.0)
        span = min(g for g in largest if g > 0) if any(g > 0 for g in largest) else None

    notes: List[str] = []
    status = "pass" if ratio >= 1 else "fail"
    if panel_ratio > max_panel_ratio:
        notes.append(f"{worst_rib} rib panels up to {_num(worst_panel)} mm wide are {_num(panel_ratio)} x the "
                     f"{_num(thickness)} mm board (limit {_num(max_panel_ratio)} x), so they may buckle before "
                     "the board crushes: add cross ribs")
    if span is not None and span / thickness > max_span_ratio:
        notes.append(f"the top spans {_num(span)} mm between ribs, {_num(span / thickness)} x the "
                     f"{_num(thickness)} mm board (limit {_num(max_span_ratio)} x): add ribs or a thicker top")
    if notes and status == "pass":
        status = "warn"
    return {"use": use, "status": status, "load_kg": load_kg, "loaded_edge_mm": round(edge, 3),
            "capacity_kn": capacity_kn, "load_kn": load_kn, "capacity_ratio": ratio, "ect_kn_m": ect_kn_m,
            "safety_factor": safety_factor, "thickness_mm": thickness, "panel_ratio": panel_ratio,
            "span_mm": span, "notes": notes}


def text(r: dict, name: str = "") -> str:
    who = f"{name} ({r['use']})" if name else r["use"]
    head = (f"{r['status'].upper()} {who}: the ribs carry about {r['capacity_ratio']:.1f} x the "
            f"{_num(r['load_kg'])} kg design load (ECT {_num(r['ect_kn_m'])} kN/m x {_num(r['loaded_edge_mm'])} mm "
            f"loaded edge / safety factor {_num(r['safety_factor'])}).")
    lines = [head]
    if r["capacity_ratio"] < 1:
        lines.append("That is less than the design load: use more ribs, a longer footprint or a stronger board.")
    lines += [f"Warning: {n}." for n in r["notes"]]
    lines.append("This is indicative only, from the board data in settings; load-test the first build of a new "
                 "seat design.")
    return "\n".join(lines)
