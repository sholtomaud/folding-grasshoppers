"""The design brief (#44): what the client asked for, and checks against it.

``brief.json`` (schema ``foldtheory.brief/1``) in the project folder holds
the values; ``brief.md`` is the same, readable, with the latest checks. Only
fields that are set are checked. Every number in a check message is worked
out here, so Claude can repeat it exactly instead of doing the arithmetic.

Checks (status ``pass``, ``warn`` or ``fail``):

- height: the scene's height against the maximum height
- footprint: the scene's width and depth against the stand (turning it 90° is allowed)
- budget: the engine's quote total against the budget
- pallet: the stack of nested sheets on the pallet (and its maximum height)
- sheet: the nested sheet size against the brief's sheet size
"""

from __future__ import annotations

import datetime
import math
from typing import Any, Dict, List, Optional

BRIEF_FILE = "brief.json"
BRIEF_MD = "brief.md"
BRIEF_SCHEMA = "foldtheory.brief/1"
MAX_TEXT = 2000
MAX_ITEMS = 50


class BriefError(ValueError):
    """A brief value that cannot be used. The message names the field."""


def _f(key, label, type_, unit="", group="", help_=""):
    return key, {"key": key, "label": label, "type": type_, "unit": unit, "group": group, "help": help_}


FIELDS: Dict[str, dict] = dict([
    _f("footprint_width_mm", "Stand width", "number", "mm", "Stand"),
    _f("footprint_depth_mm", "Stand depth", "number", "mm", "Stand"),
    _f("max_height_mm", "Maximum height", "number", "mm", "Stand"),
    _f("venue_limits", "Venue limits", "text", "", "Stand", "Rules from the venue: fire rating, no glue, access"),
    _f("items", "Items wanted", "items", "", "Items", "List of {name, quantity, notes}"),
    _f("seats", "Seats", "integer", "", "Loads"),
    _f("load_per_seat_kg", "Load per seat", "number", "kg", "Loads"),
    _f("shelf_load_kg", "Load per shelf or counter", "number", "kg", "Loads"),
    _f("board_grade", "Board grade", "text", "", "Board", "For example double-wall 6 mm or honeycomb 16 mm"),
    _f("sheet_width_mm", "Sheet width", "number", "mm", "Board"),
    _f("sheet_height_mm", "Sheet height", "number", "mm", "Board"),
    _f("pallet_length_mm", "Pallet length", "number", "mm", "Shipping"),
    _f("pallet_width_mm", "Pallet width", "number", "mm", "Shipping"),
    _f("pallet_max_height_mm", "Pallet stack limit", "number", "mm", "Shipping"),
    _f("assembly_time_h", "Assembly time allowed", "number", "h", "Assembly"),
    _f("budget", "Budget", "currency", "", "Budget", "The client's budget, including GST"),
    _f("branding", "Branding", "text", "", "Branding", "Logos, colours, print areas"),
])

CHECK_LABELS = {"height": "Height", "footprint": "Footprint", "budget": "Budget", "pallet": "Pallet",
                "sheet": "Sheet size"}


def mm(v: float) -> str:
    """A length as the designer writes it: 2400, 2412.5."""
    r = round(float(v), 1)
    return str(int(r)) if r == int(r) else f"{r:.1f}"


def clean_values(raw) -> Dict[str, Any]:
    """Check a set of brief values. ``None`` means clear the field. Raises BriefError."""
    if not isinstance(raw, dict):
        raise BriefError("The brief values must be an object of {field: value}")
    out: Dict[str, Any] = {}
    for key, v in raw.items():
        f = FIELDS.get(key)
        if f is None:
            raise BriefError(f"Unknown brief field {key!r}. Fields: {', '.join(FIELDS)}")
        if v is None:
            out[key] = None
            continue
        t = f["type"]
        if t in ("number", "currency", "integer"):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise BriefError(f"{key} must be a number")
            if t == "integer":
                if int(v) != v:
                    raise BriefError(f"{key} must be a whole number")
                v = int(v)
            if v <= 0:
                raise BriefError(f"{key} must be greater than 0")
        elif t == "text":
            if not isinstance(v, str):
                raise BriefError(f"{key} must be text")
            v = v.strip()[:MAX_TEXT]
        elif t == "items":
            v = _clean_items(key, v)
        out[key] = v
    return out


def _clean_items(key: str, v) -> List[dict]:
    if not isinstance(v, list) or len(v) > MAX_ITEMS:
        raise BriefError(f"{key} must be a list of at most {MAX_ITEMS} {{name, quantity, notes}}")
    items = []
    for it in v:
        if not isinstance(it, dict) or not isinstance(it.get("name"), str) or not it["name"].strip():
            raise BriefError(f"Each of {key} needs a name, for example {{\"name\": \"stool\", \"quantity\": 4}}")
        q = it.get("quantity", 1)
        if isinstance(q, bool) or not isinstance(q, (int, float)) or int(q) != q or q < 1:
            raise BriefError(f"The quantity of {it['name']} must be a whole number of at least 1")
        notes = it.get("notes", "")
        if not isinstance(notes, str):
            raise BriefError(f"The notes of {it['name']} must be text")
        items.append({"name": it["name"].strip()[:100], "quantity": int(q), "notes": notes.strip()[:MAX_TEXT]})
    return items


def merge(current: Dict[str, Any], changes: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(current)
    for k, v in changes.items():
        if v is None:
            out.pop(k, None)
        else:
            out[k] = v
    return out


def _check(key: str, status: str, message: str) -> dict:
    return {"key": key, "label": CHECK_LABELS[key], "status": status, "message": message}


def check_brief(values: Dict[str, Any], facts: Dict[str, Any]) -> List[dict]:
    """Checks for the fields that are set. ``facts``: size_mm [w, d, h], quote_total, currency,
    sheet_count, sheet_size [w, h], thickness_mm. Missing facts give a warning, not a failure."""
    out: List[dict] = []
    size = facts.get("size_mm")
    if "max_height_mm" in values:
        limit = values["max_height_mm"]
        if size is None:
            out.append(_check("height", "warn", f"No model yet (limit {mm(limit)} mm)"))
        else:
            h = size[2]
            out.append(_check("height", "fail" if h > limit + 1e-6 else "pass", f"{mm(h)} of {mm(limit)} mm"))
    if "footprint_width_mm" in values and "footprint_depth_mm" in values:
        W, D = values["footprint_width_mm"], values["footprint_depth_mm"]
        if size is None:
            out.append(_check("footprint", "warn", f"No model yet (stand {mm(W)} x {mm(D)} mm)"))
        else:
            w, d = size[0], size[1]
            msg = f"{mm(w)} x {mm(d)} of {mm(W)} x {mm(D)} mm"
            if w <= W + 1e-6 and d <= D + 1e-6:
                out.append(_check("footprint", "pass", msg))
            elif d <= W + 1e-6 and w <= D + 1e-6:
                out.append(_check("footprint", "pass", msg + " (fits turned 90 degrees)"))
            else:
                out.append(_check("footprint", "fail", msg))
    if "budget" in values:
        budget = values["budget"]
        total, cur = facts.get("quote_total"), facts.get("currency") or ""
        unit = f" {cur}" if cur else ""
        if total is None:
            out.append(_check("budget", "warn", f"No quote yet: run estimate_cost (budget {budget:.2f}{unit})"))
        else:
            out.append(_check("budget", "fail" if total > budget + 1e-9 else "pass",
                              f"{total:.2f} of {budget:.2f}{unit}"))
    if "pallet_length_mm" in values and "pallet_width_mm" in values:
        L, Wp = values["pallet_length_mm"], values["pallet_width_mm"]
        n, t, sheet = facts.get("sheet_count"), facts.get("thickness_mm"), facts.get("sheet_size")
        pallet = f"{mm(L)} x {mm(Wp)} mm pallet"
        if not n or not t:
            out.append(_check("pallet", "warn", f"Not nested yet, so the stack on the {pallet} is not known"))
        else:
            stack = n * t
            msg = f"{n} sheets x {mm(t)} mm = {mm(stack)} mm stack on a {pallet}"
            status = "pass"
            limit = values.get("pallet_max_height_mm")
            if limit is not None and stack > limit + 1e-6:
                status, msg = "fail", msg + f"; {mm(stack)} of {mm(limit)} mm allowed"
            elif sheet:
                sw, sh = sheet
                fits = (sw <= L + 1e-6 and sh <= Wp + 1e-6) or (sh <= L + 1e-6 and sw <= Wp + 1e-6)
                if not fits:
                    status, msg = "warn", msg + f"; the {mm(sw)} x {mm(sh)} mm sheets overhang it"
            out.append(_check("pallet", status, msg))
    if "sheet_width_mm" in values and "sheet_height_mm" in values:
        bw, bh = values["sheet_width_mm"], values["sheet_height_mm"]
        sheet = facts.get("sheet_size")
        if not sheet:
            out.append(_check("sheet", "warn", f"Not nested yet (brief: {mm(bw)} x {mm(bh)} mm)"))
        else:
            sw, sh = sheet
            same = {round(sw, 3), round(sh, 3)} == {round(bw, 3), round(bh, 3)}
            out.append(_check("sheet", "pass" if same else "warn",
                              f"nested on {mm(sw)} x {mm(sh)} mm; brief: {mm(bw)} x {mm(bh)} mm"))
    return out


def failures_text(checks: List[dict]) -> str:
    failed = [c for c in checks if c["status"] == "fail"]
    if not failed:
        return ""
    return ("Brief check failed: " + "; ".join(f"{c['label'].lower()} {c['message']}" for c in failed)
            + ". Deal with this (change the design, or ask the designer to change the brief) before you call "
              "the concept done.")


def checks_text(checks: List[dict]) -> str:
    if not checks:
        return "Nothing in the brief can be checked yet (set limits, budget, pallet or sheet size with set_brief)."
    return "\n".join(f"{c['status'].upper()} {c['key']}: {c['message']}" for c in checks)


def file_text(values: Dict[str, Any]) -> str:
    import json

    return json.dumps({"schema": BRIEF_SCHEMA, "values": values,
                       "saved_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
                      indent=2, ensure_ascii=False) + "\n"


def read_file(text: str) -> Dict[str, Any]:
    import json

    try:
        raw = json.loads(text)
    except ValueError:
        raise BriefError(f"{BRIEF_FILE} is not valid JSON") from None
    if not isinstance(raw, dict) or raw.get("schema") != BRIEF_SCHEMA:
        raise BriefError(f"{BRIEF_FILE} is not a {BRIEF_SCHEMA} brief")
    return merge({}, clean_values(raw.get("values") or {}))


def markdown(name: str, values: Dict[str, Any], checks: List[dict], currency: str = "") -> str:
    lines = ["# Brief", ""] + ([f"Project: {name}", ""] if name else [])
    group = None
    for key, f in FIELDS.items():
        if key not in values:
            continue
        if f["group"] != group:
            group = f["group"]
            lines += ["", f"## {group}", ""]
        v = values[key]
        if f["type"] == "items":
            lines.append(f"{f['label']}:")
            lines += [f"- {it['name']} ({it['quantity']})" + (f": {it['notes']}" if it["notes"] else "") for it in v]
            continue
        if f["type"] in ("number", "integer"):
            shown = mm(v) + (f" {f['unit']}" if f["unit"] else "")
        elif f["type"] == "currency":
            shown = f"{v:.2f}" + (f" {currency}" if currency else "")
        else:
            shown = str(v)
        lines.append(f"- {f['label']}: {shown}")
    lines += ["", "## Checks", ""]
    lines += [f"- {c['label']}: {c['status']} ({c['message']})" for c in checks] or ["- Nothing to check yet."]
    return "\n".join(lines) + "\n"


def binding_value(values: Dict[str, Any], key: str, currency: str = "") -> Optional[dict]:
    f = FIELDS.get(key)
    if f is None:
        return None
    unit = currency if f["type"] == "currency" else f["unit"]
    return {"key": key, "label": f["label"], "value": values.get(key), "unit": unit, "type": f["type"]}
