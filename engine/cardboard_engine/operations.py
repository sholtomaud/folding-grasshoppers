"""Part operations (#71): geometry tools Claude defines as data.

A part operation (``foldtheory.operation/1``) changes the flat parts a method
made: a lock tab with its slit, a hand hole, a cable notch. It is a JSON
document built only from the primitives below, which the engine provides and
tests. Nothing in it is ever run as code.

- ``params``: named values with a unit (mm or a count), a ``default`` and
  optional ``min`` / ``max``, all expressions (formulas.py, in plain numbers:
  lengths are in mm) over the board
  (``thickness``, ``clearance``, ``kerf``), the target (``edge_length``,
  ``part_width``, ``part_height``) and the parameters before them.
- ``steps``: each a ``profile``, a ``place`` and a ``combine``.

  Profiles, in a local frame (u, v):

  - ``rectangle`` {width, height} and ``slot`` {length, width}
  - ``circle`` {diameter, segments}
  - ``polyline`` {points: [[u, v]...], closed}
  - ``arc`` {radius, sweep (degrees), segments}: an open line
  - ``barb`` {neck, neck_length, head, head_length}: a neck and an arrow head
  - ``tooth`` {neck, head, depth}: a dovetail

  ``anchor`` is ``base`` (the profile stands on v = 0: the default on edges)
  or ``center`` (the default on parts).

  On an edge (``target: edge``) u runs along the edge from its start and v
  points out of the part. ``place`` takes ``at`` (mm from the start, default
  the midpoint), ``every`` (a pitch in mm) or ``count``, ``margin`` (kept
  clear at both ends), ``offset`` (mm inward; negative is outward) and
  ``side`` (``out``, or ``in`` to mirror the profile into the part). ``on:
  mate`` puts the step on the matching edge of the neighbouring part (the
  same edge number on the other side of the cut), mirrored to meet it.

  On a part (``target: part``) ``place`` takes ``x`` and ``y`` in mm from the
  lower left of the part's bounding box (default its centre).

  ``combine``: ``add`` to the outline (a tab), ``cut`` out of it (a hole,
  slot or notch), ``score`` or ``etch``.

- ``example``: a worked example the engine runs before saving, on a test part
  ``{width, height}`` (and, for edges, a matching part on the other side of its
  bottom edge), with ``board``, ``inputs`` and the expected ``area_mm2``,
  ``cut_loops``, ``cut_length_mm``, ``score_count`` and the same with
  ``mate_`` in front.

Every result is checked: outlines stay closed and do not cross themselves, a
cut must take something away and must not split the part, an added piece
must join the part, and a slot is never narrower than the board once cut.
A failure is an :class:`OperationError` with a reason Claude can act on, and
the parts are not changed.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
from dataclasses import replace
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import pyclipper

from . import formulas as fm
from .components.parts import Part

OPERATION_SCHEMA = "foldtheory.operation/1"
OPERATIONS_SCHEMA = "foldtheory.operations/1"
OPERATIONS_FILE = "operations.json"
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
TARGETS = ("edge", "part")
COMBINES = ("add", "cut", "score", "etch")
SIDES = ("out", "in")
ANCHORS = ("base", "center")
PROFILES: Dict[str, Tuple[str, ...]] = {
    "rectangle": ("width", "height"),
    "slot": ("length", "width"),
    "circle": ("diameter",),
    "polyline": (),
    "arc": ("radius",),
    "barb": ("neck", "neck_length", "head", "head_length"),
    "tooth": ("neck", "head", "depth"),
}
PROFILE_EXTRAS: Dict[str, Tuple[str, ...]] = {
    "circle": ("segments",), "polyline": ("points", "closed"), "arc": ("sweep", "segments"),
}
EDGE_PLACE = ("at", "every", "count", "margin", "offset", "side")
PART_PLACE = ("x", "y")
BOARD = ("thickness", "clearance", "kerf")
TARGET_FIELDS = ("edge_length", "part_width", "part_height")
DEFAULT_BOARD = {"thickness": 3.0, "clearance": 0.2, "kerf": 0.0}
EXPECTS = ("area_mm2", "cut_loops", "cut_length_mm", "score_count")
MAX_PARAMS = 20
MAX_STEPS = 30
MAX_POINTS = 200
MAX_REPEATS = 100
MAX_OPERATIONS = 100
SCALE = 1_000_000.0  # boolean operations work in nanometres
P2 = Tuple[float, float]


class OperationError(ValueError):
    """An operation that cannot be saved or applied. The message is for Claude and the designer."""


# ---- checking the document ----------------------------------------------------------


def _expr(raw, what: str) -> str:
    if isinstance(raw, bool) or raw is None:
        raise OperationError(f"{what} must be a number or an expression")
    if isinstance(raw, (int, float)):
        if not math.isfinite(raw):
            raise OperationError(f"{what} must be a finite number")
        return repr(float(raw)) if isinstance(raw, float) else str(raw)
    if not isinstance(raw, str) or not raw.strip():
        raise OperationError(f"{what} must be a number or an expression")
    try:
        fm.parse(raw)
    except fm.FormulaError as exc:
        raise OperationError(f"{what}: {exc}") from None
    return raw.strip()


def _names(expr: str) -> List[str]:
    return fm.names_in(fm.parse(expr))


def _check_names(expr: str, known: Sequence[str], what: str) -> None:
    unknown = [n for n in _names(expr) if n not in known]
    if unknown:
        raise OperationError(f"{what} uses {', '.join(unknown)}, which is not the board, the target or an earlier "
                             f"parameter. It can use: {', '.join(known)}")


def _unit(raw, what: str) -> str:
    unit = (raw or "").strip() if isinstance(raw, str) or raw is None else raw
    if not isinstance(unit, str):
        raise OperationError(f"{what}: unit must be text such as mm or count")
    try:
        dims, _ = fm.parse_unit(unit)
    except fm.FormulaError as exc:
        raise OperationError(f"{what}: {exc}") from None
    if dims not in (fm.NONE, (1, 0, 0)):
        raise OperationError(f"{what}: unit must be a length (mm) or a count")
    return unit


def _segments(raw, what: str, default: int) -> int:
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, int) or not 8 <= raw <= 256:
        raise OperationError(f"{what}: segments must be a whole number from 8 to 256")
    return raw


def _check_profile(raw, what: str, known: Sequence[str], combine: str) -> dict:
    if not isinstance(raw, dict):
        raise OperationError(f"{what}: profile must be an object with a shape")
    shape = raw.get("shape")
    if shape not in PROFILES:
        raise OperationError(f"{what}: shape must be one of {', '.join(PROFILES)} (got {shape!r})")
    allowed = set(PROFILES[shape]) | set(PROFILE_EXTRAS.get(shape, ())) | {"shape", "anchor"}
    extra = sorted(set(raw) - allowed)
    if extra:
        raise OperationError(f"{what}: a {shape} takes {', '.join(sorted(allowed - {'shape'}))}, not "
                             f"{', '.join(extra)}")
    out: Dict[str, Any] = {"shape": shape}
    for k in PROFILES[shape]:
        if k not in raw:
            raise OperationError(f"{what}: a {shape} needs {', '.join(PROFILES[shape])}")
        out[k] = _expr(raw[k], f"{what} {k}")
        _check_names(out[k], known, f"{what} {k}")
    if raw.get("anchor") is not None:
        if raw["anchor"] not in ANCHORS:
            raise OperationError(f"{what}: anchor must be base or center")
        out["anchor"] = raw["anchor"]
    if shape in ("circle", "arc"):
        out["segments"] = _segments(raw.get("segments"), what, 48 if shape == "circle" else 24)
    if shape == "arc":
        out["sweep"] = _expr(raw.get("sweep", 180), f"{what} sweep")
        _check_names(out["sweep"], known, f"{what} sweep")
        if combine in ("add", "cut"):
            raise OperationError(f"{what}: an arc is an open line, so it can only be scored or etched")
    if shape == "polyline":
        pts = raw.get("points")
        if not isinstance(pts, list) or not 2 <= len(pts) <= MAX_POINTS or not all(
                isinstance(p, list) and len(p) == 2 for p in pts):
            raise OperationError(f"{what}: points must be a list of 2 to {MAX_POINTS} [u, v] pairs")
        out["points"] = []
        for i, (u, v) in enumerate(pts):
            pair = [_expr(u, f"{what} point {i + 1} u"), _expr(v, f"{what} point {i + 1} v")]
            for e in pair:
                _check_names(e, known, f"{what} point {i + 1}")
            out["points"].append(pair)
        closed = raw.get("closed", combine in ("add", "cut"))
        if not isinstance(closed, bool):
            raise OperationError(f"{what}: closed must be true or false")
        if combine in ("add", "cut") and (not closed or len(pts) < 3):
            raise OperationError(f"{what}: an outline to {combine} must be closed, with at least 3 points")
        out["closed"] = closed
    return out


def _check_step(raw, i: int, target: str, known: Sequence[str]) -> dict:
    what = f"step {i}"
    if not isinstance(raw, dict):
        raise OperationError(f"{what} must be an object with profile, place and combine")
    extra = sorted(set(raw) - {"profile", "place", "combine", "on", "description"})
    if extra:
        raise OperationError(f"{what}: unknown {', '.join(extra)}. A step has profile, place, combine and on")
    combine = raw.get("combine")
    if combine not in COMBINES:
        raise OperationError(f"{what}: combine must be one of {', '.join(COMBINES)} (got {combine!r})")
    out: Dict[str, Any] = {"combine": combine, "profile": _check_profile(raw.get("profile"), what, known, combine)}
    on = raw.get("on", "part")
    if on not in ("part", "mate"):
        raise OperationError(f"{what}: on must be part or mate")
    if on == "mate" and target != "edge":
        raise OperationError(f"{what}: on mate needs an edge operation (the matching edge of the next part)")
    out["on"] = on
    place = raw.get("place") or {}
    if not isinstance(place, dict):
        raise OperationError(f"{what}: place must be an object")
    keys = EDGE_PLACE if target == "edge" else PART_PLACE
    extra = sorted(set(place) - set(keys))
    if extra:
        raise OperationError(f"{what}: on {'an edge' if target == 'edge' else 'a part'} place takes "
                             f"{', '.join(keys)}, not {', '.join(extra)}")
    clean: Dict[str, Any] = {}
    for k, v in place.items():
        if k == "side":
            if v not in SIDES:
                raise OperationError(f"{what}: side must be out or in")
            clean[k] = v
            continue
        clean[k] = _expr(v, f"{what} {k}")
        _check_names(clean[k], known, f"{what} {k}")
    if "every" in clean and "count" in clean:
        raise OperationError(f"{what}: give every or count, not both")
    if ("every" in clean or "count" in clean) and "at" in clean:
        raise OperationError(f"{what}: give at for one position, or every / count to repeat it, not both")
    out["place"] = clean
    if raw.get("description"):
        out["description"] = str(raw["description"])[:200]
    return out


def _check_params(raw, known: List[str]) -> Dict[str, dict]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict) or len(raw) > MAX_PARAMS:
        raise OperationError(f"params must be an object of at most {MAX_PARAMS} named parameters")
    out = {}
    for name, spec in raw.items():
        if not isinstance(name, str) or not KEY_RE.match(name) or name in BOARD or name in TARGET_FIELDS:
            raise OperationError(f"Parameter {name!r}: a name is lower case letters, digits and _, and not "
                                 f"{', '.join(BOARD + TARGET_FIELDS)}")
        if not isinstance(spec, dict) or "default" not in spec:
            raise OperationError(f"Parameter {name}: give {{unit, default, min, max}}")
        extra = sorted(set(spec) - {"unit", "default", "min", "max", "description", "label"})
        if extra:
            raise OperationError(f"Parameter {name}: unknown {', '.join(extra)}")
        p = {"unit": _unit(spec.get("unit", "mm"), f"Parameter {name}")}
        for k in ("default", "min", "max"):
            if spec.get(k) is not None:
                p[k] = _expr(spec[k], f"Parameter {name} {k}")
                _check_names(p[k], known, f"Parameter {name} {k}")
        for k in ("label", "description"):
            if spec.get(k):
                p[k] = str(spec[k])[:200]
        out[name] = p
        known.append(name)
    return out


def operation_version(op: dict) -> str:
    canon = json.dumps({"target": op["target"], "params": op["params"], "steps": op["steps"]}, sort_keys=True)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:12]


def validate_operation(raw) -> dict:
    """A clean operation, with its worked example checked. Raises OperationError."""
    if not isinstance(raw, dict):
        raise OperationError("An operation must be an object with key, label, target, params, steps and example")
    if raw.get("schema") not in (None, OPERATION_SCHEMA):
        raise OperationError(f"schema must be {OPERATION_SCHEMA}")
    key = raw.get("key")
    if not isinstance(key, str) or not KEY_RE.match(key):
        raise OperationError("An operation key must be lower case letters, digits and _ (for example skin_lock)")
    label = raw.get("label") or key
    if not isinstance(label, str):
        raise OperationError(f"{key}: label must be text")
    target = raw.get("target")
    if target not in TARGETS:
        raise OperationError(f"{key}: target must be edge or part (got {target!r})")
    known = list(BOARD + TARGET_FIELDS)
    params = _check_params(raw.get("params"), known)
    steps = raw.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise OperationError(f"{key}: an operation needs at least one step (and at most {MAX_STEPS})")
    clean = {"schema": OPERATION_SCHEMA, "key": key, "label": label.strip()[:100],
             "description": str(raw.get("description") or "")[:500], "target": target, "params": params,
             "steps": [_check_step(s, i, target, known) for i, s in enumerate(steps, start=1)],
             "example": raw.get("example")}
    check_example(clean)
    clean["version"] = operation_version(clean)
    for extra in ("set_by", "set_at"):
        if raw.get(extra):
            clean[extra] = str(raw[extra])[:80]
    return clean


# ---- the worked example -----------------------------------------------------------------------


def _rect(x0, y0, x1, y1) -> List[P2]:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def measure(part: Part) -> Dict[str, float]:
    loops = _normal(part.cut)
    return {"area_mm2": round(sum(_area2(p) for p in loops) / 2, 6), "cut_loops": len(part.cut),
            "cut_length_mm": round(sum(_perimeter(p) for p in part.cut), 6), "score_count": len(part.score)}


def check_example(op: dict) -> Dict[str, float]:
    """Run the worked example. Raises OperationError unless every expected number comes out."""
    key = op["key"]
    ex = op.get("example")
    if not isinstance(ex, dict) or not isinstance(ex.get("part"), dict) or not isinstance(ex.get("expect"), dict):
        raise OperationError(f"{key} needs a worked example: {{\"part\": {{\"width\": 200, \"height\": 100}}, "
                             "\"board\": {\"thickness\": 3}, \"inputs\": {}, \"expect\": {\"area_mm2\": ...}}")
    w, h = ex["part"].get("width"), ex["part"].get("height")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and 1 <= v <= 5000 for v in (w, h)):
        raise OperationError(f"{key}: the example part needs a width and height from 1 to 5000 mm")
    board = dict(DEFAULT_BOARD)
    for k, v in (ex.get("board") or {}).items():
        if k not in BOARD or isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 100:
            raise OperationError(f"{key}: the example board takes thickness, clearance and kerf in mm")
        board[k] = float(v)
    expect = ex["expect"]
    wanted = {k: v for k, v in expect.items()}
    allowed = set(EXPECTS) | {"mate_" + k for k in EXPECTS}
    bad = sorted(set(wanted) - allowed)
    if bad or not wanted:
        raise OperationError(f"{key}: the example expects some of {', '.join(sorted(allowed))}")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in wanted.values()):
        raise OperationError(f"{key}: the example's expected values must be numbers")
    a = Part(id="A", cut=[_rect(0, 0, w, h)])
    parts = [a]
    if op["target"] == "edge":
        parts.append(Part(id="B", cut=[_rect(0, -h, w, 0)]))
        targets = [{"name": "the example edge", "main": ("A", (0.0, 0.0), (float(w), 0.0)),
                    "mate": ("B", (float(w), 0.0), (0.0, 0.0))}]
    else:
        targets = [{"name": "the example part", "part": "A"}]
    out = apply(op, parts, targets, board, ex.get("inputs") or {})
    got = {**measure(out[0]), **({("mate_" + k): v for k, v in measure(out[1]).items()} if len(out) > 1 else {})}
    for k, v in wanted.items():
        if k not in got:
            raise OperationError(f"{key}: a part operation's example has no mate part, so it cannot expect {k}")
        if abs(got[k] - v) > 0.01 + 1e-6 * abs(v):
            raise OperationError(f"The worked example for {key} gives {k} = {got[k]:.10g} but expects {v:.10g}; "
                                 "fix the operation or the example")
    return got


# ---- applying ---------------------------------------------------------------------------------


def _q(value: float, unit: str) -> dict:
    # Every length is in mm and every count a plain number, so expressions work in plain numbers:
    # thickness - 1 is a millimetre less than the board.
    return {"value": value, "unit": ""}


def _eval(expr: str, env: Dict[str, dict], unit: str, what: str) -> float:
    try:
        return fm.evaluate(expr, env, "")
    except fm.FormulaError as exc:
        raise OperationError(f"{what}: {exc}") from None


def resolve_params(op: dict, env: Dict[str, dict], inputs: dict, where: str) -> Dict[str, dict]:
    """Each parameter's value (given, or its default), checked against min and max."""
    if not isinstance(inputs, dict):
        raise OperationError("inputs must be an object {parameter: value}")
    unknown = sorted(set(inputs) - set(op["params"]))
    if unknown:
        have = ", ".join(op["params"]) or "none"
        raise OperationError(f"{op['key']} has no parameter {', '.join(unknown)}. Its parameters: {have}")
    env = dict(env)
    for name, p in op["params"].items():
        unit = p["unit"]
        if name in inputs:
            v = inputs[name]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise OperationError(f"{name} must be a number")
            value = float(v)
        else:
            value = _eval(p["default"], env, unit, f"{op['key']} {name}")
        for bound, cmp in (("min", lambda a, b: a < b - 1e-9), ("max", lambda a, b: a > b + 1e-9)):
            if bound in p:
                limit = _eval(p[bound], env, unit, f"{op['key']} {name} {bound}")
                if cmp(value, limit):
                    word = "below its min" if bound == "min" else "above its max"
                    raise OperationError(f"{name} is {value:g}{(' ' + unit) if unit else ''} on {where}, {word} of "
                                         f"{limit:g}{(' ' + unit) if unit else ''} ({p[bound]})")
        env[name] = _q(value, unit)
    return env


def _dims(profile: dict, env, what: str) -> Dict[str, float]:
    out = {}
    for k in PROFILES[profile["shape"]]:
        v = _eval(profile[k], env, "mm", f"{what} {k}")
        if v <= 0:
            raise OperationError(f"{what}: {k} comes out at {v:g} mm; it must be more than 0")
        out[k] = v
    return out


def profile_points(profile: dict, env, what: str, anchor: str) -> Tuple[List[P2], bool]:
    """The profile in its local frame (u, v), and whether it is closed."""
    shape = profile["shape"]
    anchor = profile.get("anchor", anchor)
    d = _dims(profile, env, what)
    if shape in ("rectangle", "slot"):
        w, h = (d["width"], d["height"]) if shape == "rectangle" else (d["length"], d["width"])
        pts = _rect(-w / 2, 0, w / 2, h)
        top = h
    elif shape == "circle":
        r, n = d["diameter"] / 2, profile["segments"]
        pts = [(r * math.cos(2 * math.pi * k / n), r + r * math.sin(2 * math.pi * k / n)) for k in range(n)]
        top = 2 * r
    elif shape == "barb":
        n, l, hw, hl = d["neck"] / 2, d["neck_length"], d["head"] / 2, d["head_length"]
        if hw <= n:
            raise OperationError(f"{what}: the barb head ({2 * hw:g} mm) must be wider than its neck ({2 * n:g} mm)")
        pts = [(-n, 0), (n, 0), (n, l), (hw, l), (n, l + hl), (-n, l + hl), (-hw, l), (-n, l)]
        top = l + hl
    elif shape == "tooth":
        n, hw, dp = d["neck"] / 2, d["head"] / 2, d["depth"]
        pts = [(-n, 0), (n, 0), (hw, dp), (-hw, dp)]
        top = dp
    elif shape == "arc":
        r, n = d["radius"], profile["segments"]
        sweep = _eval(profile["sweep"], env, "", f"{what} sweep")
        if not 0 < sweep <= 360:
            raise OperationError(f"{what}: sweep comes out at {sweep:g} degrees; it must be from 0 to 360")
        a0 = math.radians(90 - sweep / 2)
        pts = [(r * math.cos(a0 + math.radians(sweep) * k / n), r * math.sin(a0 + math.radians(sweep) * k / n))
               for k in range(n + 1)]
        return pts, False
    else:  # polyline
        pts = [(_eval(u, env, "mm", f"{what} point {i + 1}"), _eval(v, env, "mm", f"{what} point {i + 1}"))
               for i, (u, v) in enumerate(profile["points"])]
        return pts, profile["closed"]
    if anchor == "center":
        pts = [(u, v - top / 2) for u, v in pts]
    return pts, True


def _positions(place: dict, env, length: float, what: str) -> List[float]:
    margin = _eval(place["margin"], env, "mm", f"{what} margin") if "margin" in place else 0.0
    span = length - 2 * margin
    if "count" in place:
        n = _eval(place["count"], env, "", f"{what} count")
        if n != int(n) or not 1 <= n <= MAX_REPEATS:
            raise OperationError(f"{what}: count comes out at {n:g}; it must be a whole number from 1 to "
                                 f"{MAX_REPEATS}")
        if span <= 0:
            raise OperationError(f"{what}: the margins leave no room on an edge {length:g} mm long")
        return [margin + (i + 0.5) * span / int(n) for i in range(int(n))]
    if "every" in place:
        pitch = _eval(place["every"], env, "mm", f"{what} every")
        if pitch <= 0:
            raise OperationError(f"{what}: every comes out at {pitch:g} mm; it must be more than 0")
        n = max(1, math.floor(span / pitch + 1e-9)) if span > 0 else 1
        if n > MAX_REPEATS:
            raise OperationError(f"{what}: every {pitch:g} mm repeats {n} times; at most {MAX_REPEATS}")
        return [length / 2 + (i - (n - 1) / 2) * pitch for i in range(n)]
    return [_eval(place["at"], env, "mm", f"{what} at") if "at" in place else length / 2]


def _bbox(part: Part) -> Tuple[float, float, float, float]:
    xs = [x for loop in part.cut for x, _ in loop]
    ys = [y for loop in part.cut for _, y in loop]
    return min(xs), min(ys), max(xs), max(ys)


def _target_env(board: Dict[str, float], part: Part, edge_length: float) -> Dict[str, dict]:
    x0, y0, x1, y1 = _bbox(part)
    env = {k: _q(float(board.get(k, DEFAULT_BOARD[k])), "mm") for k in BOARD}
    env.update(edge_length=_q(edge_length, "mm"), part_width=_q(x1 - x0, "mm"), part_height=_q(y1 - y0, "mm"))
    return env


def _edge_frame(pa: P2, pb: P2):
    l = math.dist(pa, pb)
    ux, uy = (pb[0] - pa[0]) / l, (pb[1] - pa[1]) / l
    nx, ny = uy, -ux  # out of a counter-clockwise part: to the right of pa -> pb
    return lambda u, v: (pa[0] + u * ux + v * nx, pa[1] + u * uy + v * ny)


def apply(op: dict, parts: List[Part], targets: List[dict], board: Dict[str, float], inputs: dict) -> List[Part]:
    """The parts with the operation applied at every target. Raises OperationError; ``parts`` is not changed.

    A target is ``{"name", "main": (part id, pa, pb), "mate": (part id, oa, ob) or None}`` for an edge
    operation (pa -> pb in the part's counter-clockwise winding) or ``{"name", "part": part id}``.
    """
    by_id = {p.id: p for p in parts}
    for t in targets:
        where = t["name"]
        if op["target"] == "edge":
            pid, pa, pb = t["main"]
            length = math.dist(pa, pb)
            if length < 1e-6:
                raise OperationError(f"{where} has no length")
            env = resolve_params(op, _target_env(board, by_id[pid], length), inputs, where)
        else:
            pid = t["part"]
            env = resolve_params(op, _target_env(board, by_id[pid], 0.0), inputs, where)
        for i, step in enumerate(op["steps"], start=1):
            what = f"{op['key']} step {i} ({step['combine']} {step['profile']['shape']})"
            shape, closed = profile_points(step["profile"], env, what,
                                           "base" if op["target"] == "edge" else "center")
            if step["combine"] == "cut" and step["profile"]["shape"] == "slot":
                d = _dims(step["profile"], env, what)
                kerf = env["kerf"]["value"]
                cut_w, thick = min(d["length"], d["width"]) + kerf, env["thickness"]["value"]
                if cut_w < thick - 1e-9:
                    raise OperationError(f"{what}: the slot is {cut_w:g} mm wide once cut, narrower than the board "
                                         f"({thick:g} mm), so the board would not go in. Make it at least "
                                         "thickness - kerf as drawn")
            place = step["place"]
            if op["target"] == "edge":
                if step["on"] == "mate":
                    if t.get("mate") is None:
                        raise OperationError(f"{what} goes on the matching edge of the neighbouring part, and "
                                             f"{where} has none: apply it to an unfold edge by number")
                    on_id, a, b = t["mate"]
                else:
                    on_id, a, b = pid, pa, pb
                to_world = _edge_frame(a, b)
                mirror = step["on"] == "mate"
                offset = _eval(place["offset"], env, "mm", f"{what} offset") if "offset" in place else 0.0
                flip = -1.0 if place.get("side") == "in" else 1.0
                polys = []
                for u0 in _positions(place, env, length, what):
                    uc = length - u0 if mirror else u0
                    polys.append([to_world(uc - pu if mirror else uc + pu, flip * pv - offset) for pu, pv in shape])
            else:
                on_id = pid
                x0, y0, x1, y1 = _bbox(by_id[pid])
                envp = dict(env)
                ox = x0 + (_eval(place["x"], envp, "mm", f"{what} x") if "x" in place else (x1 - x0) / 2)
                oy = y0 + (_eval(place["y"], envp, "mm", f"{what} y") if "y" in place else (y1 - y0) / 2)
                polys = [[(ox + pu, oy + pv) for pu, pv in shape]]
            by_id[on_id] = _combine(by_id[on_id], step["combine"], polys, closed, what)
    return [by_id[p.id] for p in parts]


# ---- geometry ---------------------------------------------------------------------------------


def _area2(p: Sequence[P2]) -> float:
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(p, list(p[1:]) + list(p[:1])))


def _perimeter(p: Sequence[P2]) -> float:
    return sum(math.dist(a, b) for a, b in zip(p, list(p[1:]) + list(p[:1])))


def _ints(p: Sequence[P2]):
    return [(int(round(x * SCALE)), int(round(y * SCALE))) for x, y in p]


def _floats(p) -> List[P2]:
    return [(x / SCALE, y / SCALE) for x, y in p]


def _boolean(loops, clip, kind) -> List[List[P2]]:
    pc = pyclipper.Pyclipper()
    for p in loops:
        if len(p) >= 3:
            pc.AddPath(_ints(p), pyclipper.PT_SUBJECT, True)
    for p in clip:
        q = _ints(p)
        if not pyclipper.Orientation(q):
            q.reverse()
        pc.AddPath(q, pyclipper.PT_CLIP, True)
    try:
        out = pc.Execute(kind, pyclipper.PFT_EVENODD, pyclipper.PFT_NONZERO)
    except pyclipper.ClipperException:
        out = []
    loops = [_floats(p) for p in out if len(p) >= 3]
    return sorted(loops, key=lambda p: -abs(_area2(p)))


def _normal(loops) -> List[List[P2]]:
    """The loops as the region they enclose (even-odd): outer loops counter-clockwise, holes clockwise."""
    return _boolean(loops, [], pyclipper.CT_UNION) if loops else []


def _crosses(loop: List[P2]) -> bool:
    n = len(loop)
    if n > 3000:
        return False

    def orient(a, b, c):
        v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return 0 if abs(v) < 1e-12 else (1 if v > 0 else -1)

    segs = [(loop[i], loop[(i + 1) % n]) for i in range(n)]
    for i in range(n):
        a, b = segs[i]
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            c, d = segs[j]
            if max(a[0], b[0]) < min(c[0], d[0]) or max(c[0], d[0]) < min(a[0], b[0]) or \
                    max(a[1], b[1]) < min(c[1], d[1]) or max(c[1], d[1]) < min(a[1], b[1]):
                continue
            o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
            if o1 * o2 < 0 and o3 * o4 < 0:
                return True
    return False


def _combine(part: Part, combine: str, polys: List[List[P2]], closed: bool, what: str) -> Part:
    if combine in ("score", "etch"):
        lines = [list(p) + ([p[0]] if closed else []) for p in polys]
        return replace(part, **{combine: list(getattr(part, combine)) + lines})
    before = _normal(part.cut)
    area0 = sum(_area2(p) for p in before) / 2
    outer0 = sum(1 for p in before if _area2(p) > 0)
    kind = pyclipper.CT_UNION if combine == "add" else pyclipper.CT_DIFFERENCE
    after = _boolean(part.cut, polys, kind)
    area1 = sum(_area2(p) for p in after) / 2
    outer1 = sum(1 for p in after if _area2(p) > 0)
    if not after:
        raise OperationError(f"{what} cuts away all of {part.id}")
    if combine == "add":
        if area1 <= area0 + 1e-6:
            raise OperationError(f"{what} adds nothing to {part.id}: the profile lies inside it")
        if outer1 > outer0:
            raise OperationError(f"{what} makes a piece that does not touch {part.id}; give it an offset that "
                                 "joins it to the part")
    else:
        if area1 >= area0 - 1e-6:
            raise OperationError(f"{what} misses {part.id}: the profile is outside the part")
        if outer1 > outer0:
            raise OperationError(f"{what} cuts {part.id} into pieces; make it smaller or move it")
    for p in after:
        if _crosses(p):
            raise OperationError(f"{what} gives {part.id} an outline that crosses itself")
    return replace(part, cut=after)


# ---- built-in operations ----------------------------------------------------------------------


def _builtin(raw: dict) -> dict:
    return validate_operation(raw)


BUILTIN_OPERATIONS: List[dict] = [_builtin(o) for o in (
    {
        "key": "lock_tab",
        "label": "Lock tab",
        "description": "A flap with a barbed head on one side of a cut edge and the slit that takes it in the "
                       "neighbouring panel, sized from the board. No glue.",
        "target": "edge",
        "params": {
            "flap": {"unit": "mm", "default": "3 * thickness", "min": "2 * thickness",
                     "description": "How far the flap reaches before the neck"},
            "neck": {"unit": "mm", "default": "min(max(0.3 * edge_length, 3 * thickness), 10 * thickness)",
                     "min": "2 * thickness", "max": "edge_length / 2"},
            "barb": {"unit": "mm", "default": "thickness / 2", "min": "0.5",
                     "description": "How far each barb stands proud of the slit"},
        },
        "steps": [
            {"combine": "add", "profile": {"shape": "rectangle", "width": "neck + 2 * barb + 2 * thickness",
                                           "height": "flap"}},
            {"combine": "add", "profile": {"shape": "barb", "neck": "neck + kerf",
                                           "neck_length": "thickness + clearance",
                                           "head": "neck + clearance + 2 * barb + kerf", "head_length": "thickness"},
             "place": {"offset": "-flap"}},
            {"combine": "score", "profile": {"shape": "polyline", "points": [
                ["-(neck + 2 * barb + 2 * thickness) / 2", 0], ["(neck + 2 * barb + 2 * thickness) / 2", 0]]}},
            {"combine": "cut", "on": "mate",
             "profile": {"shape": "slot", "length": "neck + clearance - kerf", "width": "thickness + clearance - kerf"},
             "place": {"side": "in", "offset": "flap + thickness"}},
        ],
        # 200 mm edge, 3 mm board: neck 30, flap 9 x 39 (351); barb 30 x 3.2 + a 33.2 -> 30 head 3 deep
        # (96 + 94.8); slit 30.2 x 3.2 (96.64).
        "example": {"part": {"width": 200, "height": 100}, "board": {"thickness": 3, "clearance": 0.2, "kerf": 0},
                    "expect": {"area_mm2": 20541.8, "cut_loops": 1, "score_count": 1,
                               "mate_area_mm2": 19903.36, "mate_cut_loops": 2}},
    },
    {
        "key": "rib_notch",
        "label": "Rib notch flap",
        "description": "A flap on a skin edge with a notch the width of a rib that drops over the rib end, so the "
                       "waffle holds the skin. Scored to fold.",
        "target": "edge",
        "params": {
            "flap": {"unit": "mm", "default": "4 * thickness", "min": "2 * thickness",
                     "description": "How far the flap reaches"},
            "width": {"unit": "mm", "default": "10 * thickness", "min": "3 * thickness", "max": "edge_length",
                      "description": "Flap width along the edge"},
            "notch_depth": {"unit": "mm", "default": "2 * thickness", "min": "thickness", "max": "flap - thickness"},
            "at": {"unit": "mm", "default": "edge_length / 2", "min": "width / 2", "max": "edge_length - width / 2",
                   "description": "Where the rib meets the edge, from its start"},
        },
        "steps": [
            {"combine": "add", "profile": {"shape": "rectangle", "width": "width", "height": "flap"},
             "place": {"at": "at"}},
            {"combine": "cut", "profile": {"shape": "slot", "length": "thickness + clearance - kerf",
                                           "width": "notch_depth + 1"},
             "place": {"at": "at", "offset": "notch_depth - flap"}},
            {"combine": "score", "profile": {"shape": "polyline", "points": [["-width / 2", 0], ["width / 2", 0]]},
             "place": {"at": "at"}},
        ],
        # 3 mm board: flap 12 x 30 (360) less a 3.2 x 6 notch (19.2).
        "example": {"part": {"width": 200, "height": 100}, "board": {"thickness": 3, "clearance": 0.2, "kerf": 0},
                    "expect": {"area_mm2": 20340.8, "cut_loops": 1, "score_count": 1}},
    },
    {
        "key": "slot_joint",
        "label": "Slot joint",
        "description": "A slot of board thickness plus clearance cut in from an edge, so two panels can cross "
                       "(each gets a slot half its depth).",
        "target": "edge",
        "params": {
            "depth": {"unit": "mm", "default": "edge_length / 2", "min": "thickness", "max": "edge_length"},
            "at": {"unit": "mm", "default": "edge_length / 2", "min": "thickness", "max": "edge_length - thickness",
                   "description": "Where the other panel crosses, from the edge's start"},
        },
        "steps": [
            {"combine": "cut", "profile": {"shape": "slot", "length": "thickness + clearance - kerf",
                                           "width": "depth + 1"},
             "place": {"at": "at", "side": "in", "offset": "-1"}},
        ],
        "example": {"part": {"width": 200, "height": 100}, "inputs": {"depth": 50},
                    "expect": {"area_mm2": 19840, "cut_loops": 1}},
    },
    {
        "key": "hand_hole",
        "label": "Hand hole",
        "description": "A round hole through a part, for a hand or a cable.",
        "target": "part",
        "params": {"diameter": {"unit": "mm", "default": "30", "min": "4 * thickness", "max": "300"},
                   "x": {"unit": "mm", "default": "part_width / 2"},
                   "y": {"unit": "mm", "default": "part_height / 2"}},
        "steps": [{"combine": "cut", "profile": {"shape": "circle", "diameter": "diameter", "segments": 64},
                   "place": {"x": "x", "y": "y"}}],
        "example": {"part": {"width": 200, "height": 100},
                    "expect": {"cut_loops": 2, "area_mm2": round(20000 - 32 * 15 ** 2 * math.sin(math.pi / 32), 6)}},
    },
)]


# ---- the store --------------------------------------------------------------------------------


def _read_json(path: str):
    try:
        if os.path.getsize(path) > 1024 * 1024:
            return None
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def file_text(operations: List[dict]) -> str:
    return json.dumps({"schema": OPERATIONS_SCHEMA, "operations": operations}, indent=2, ensure_ascii=False) + "\n"


class OperationStore:
    """The operations Claude can apply: built in, then Fold Theory's saved ones, then the project's (by key)."""

    def __init__(self, home: str, project_folder: Callable[[], Optional[str]]):
        from .settings import settings_dir

        self.defaults_path = os.path.join(settings_dir(home), OPERATIONS_FILE)
        self._project_folder = project_folder
        self.project_operations: List[dict] = []
        self.rejected: List[str] = []

    def _file_operations(self, data) -> List[dict]:
        out = []
        if isinstance(data, dict) and isinstance(data.get("operations"), list):
            for raw in data["operations"][:MAX_OPERATIONS]:
                try:
                    out.append(validate_operation(raw))
                except OperationError as exc:
                    self.rejected.append(f"{raw.get('key') if isinstance(raw, dict) else raw}: {exc}")
        return out

    def load_project(self, folder: Optional[str]) -> None:
        self.project_operations = (self._file_operations(_read_json(os.path.join(folder, OPERATIONS_FILE)))
                                   if folder else [])

    def clear_project(self) -> None:
        self.project_operations = []

    def all(self) -> List[dict]:
        merged: Dict[str, dict] = {o["key"]: dict(o, source="builtin") for o in BUILTIN_OPERATIONS}
        for o in self._file_operations(_read_json(self.defaults_path)):
            merged[o["key"]] = dict(o, source="default")
        for o in self.project_operations:
            merged[o["key"]] = dict(o, source="project")
        return list(merged.values())

    def get(self, key) -> dict:
        found = {o["key"]: o for o in self.all()}
        if key not in found:
            raise OperationError(f"There is no operation {key!r}. Operations: {', '.join(found)}. Add one with "
                                 "add_operation")
        return found[key]

    def project_file_text(self) -> str:
        return file_text(self.project_operations)

    def save_operation(self, raw, *, scope: str = "project", set_by: str = "") -> dict:
        from .project import _write_atomic
        from .settings import now_iso

        if scope not in ("project", "all"):
            raise OperationError("scope must be project or all")
        op = validate_operation({**raw, "set_by": set_by or "designer", "set_at": now_iso()}
                                if isinstance(raw, dict) else raw)
        if any(o["key"] == op["key"] for o in BUILTIN_OPERATIONS):
            raise OperationError(f"{op['key']} is a built-in operation; give yours another key")
        if scope == "all":
            data = _read_json(self.defaults_path)
            current = [x for x in (data.get("operations", []) if isinstance(data, dict) else [])
                       if isinstance(x, dict) and x.get("key") != op["key"]]
            if len(current) >= MAX_OPERATIONS:
                raise OperationError(f"There are already {MAX_OPERATIONS} saved operations")
            _write_atomic(self.defaults_path, file_text(current + [op]))
        else:
            self.project_operations = [x for x in self.project_operations if x["key"] != op["key"]] + [op]
            folder = self._project_folder()
            if folder:
                _write_atomic(os.path.join(folder, OPERATIONS_FILE), self.project_file_text())
        return op


def definition(op: dict) -> dict:
    """What an applied operation keeps in the recipe, so the project rebuilds without the store."""
    return {k: copy.deepcopy(op[k]) for k in ("schema", "key", "label", "description", "target", "params", "steps",
                                              "example", "version") if k in op}
