"""Project settings: typed values in layers, most specific wins.

Prices, rates, margin, GST and company details are settings, never numbers
Claude types into a tool call. Each setting has a definition (label, type,
unit, allowed range) and each stored value records who set it and when::

    ~/Documents/Fold Theory/Settings/settings.json   default layer (the rate card)
    <active workflow>.json  "settings": {...}         workflow layer
    <project folder>/settings.json                    project layer
    (concepts, later)                                 concept layer

Both settings files have the same shape::

    {"schema": "cardboardcad.settings/1",
     "definitions": [{"key": "glue_per_joint", "label": "Glue per joint", "type": "currency", ...}],
     "values": {"sheet_price": {"value": 38.5, "set_by": "SM", "set_at": "2026-11-27T03:30:00Z"}}}

A value of ``None`` means "not set": a formula that needs it stops with an
error that tells Claude to ask the designer with ``request_inputs``.
"""

from __future__ import annotations

import datetime
import difflib
import json
import math
import os
import re
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Optional

from .brand import documents_dir

SETTINGS_SCHEMA = "cardboardcad.settings/1"
SETTINGS_FILE = "settings.json"
LAYERS = ("default", "workflow", "project", "concept")
SCOPES = {"all": "default", "default": "default", "project": "project", "concept": "concept"}
TYPES = ("number", "integer", "currency", "percent", "text", "boolean")
MAX_FILE_BYTES = 1024 * 1024
MAX_DEFINITIONS = 200
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


class SettingsError(ValueError):
    """A setting that cannot be stored or used. The message is for Claude and the designer."""


@dataclass(frozen=True)
class Definition:
    key: str
    label: str
    type: str
    unit: str = ""
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    default: Any = None  # the built-in rate card value; None: must be asked for
    group: str = "Costs"
    help: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _d(key, label, type_, unit="", minimum=None, maximum=None, default=None, group="Costs", help=""):
    return Definition(key, label, type_, unit, minimum, maximum, default, group, help)


BUILTIN_DEFINITIONS: Dict[str, Definition] = {d.key: d for d in (
    _d("sheet_price", "Sheet price", "currency", "AUD/sheet", 0, help="Price of one sheet of board"),
    _d("machine_rate", "Machine rate", "currency", "AUD/h", 0, help="Cutter cost per hour"),
    _d("labour_rate", "Labour rate", "currency", "AUD/h", 0, help="Workshop labour per hour"),
    _d("margin_pct", "Margin", "percent", "%", 0, 1000, 0, help="Added on top of cost"),
    _d("gst_pct", "GST", "percent", "%", 0, 100, 10, help="Tax added to the quote"),
    _d("cut_speed", "Cut speed", "number", "mm/s", 0.001, None, 20.0, "Machine"),
    _d("score_speed", "Score speed", "number", "mm/s", 0.001, None, 40.0, "Machine"),
    _d("pierce_time", "Pierce time", "number", "s", 0, None, 0.5, "Machine"),
    _d("travel_overhead", "Travel overhead", "number", "fraction", 0, 10, 0.15, "Machine",
       "Extra time for travel moves, as a fraction of cutting time"),
    _d("client_name", "Client", "text", "", default="", group="Job"),
    _d("job_name", "Job", "text", "", default="", group="Job"),
    _d("currency", "Currency", "text", "", default="AUD", group="Company"),
    _d("company_name", "Company name", "text", "", default="Fold Theory", group="Company"),
    _d("company_abn", "ABN", "text", "", default="", group="Company"),
    _d("quote_valid_days", "Quote valid for", "integer", "days", 1, 365, 30, "Company"),
    _d("print_rate", "Print rate", "currency", "AUD/m2", 0, help="Printing per square metre"),
    _d("assembly_hours", "Assembly hours", "number", "h", 0, None, 0, "Quote",
       "Hours for assembly and install, if known; 0 works them out from the parts"),
    _d("assembly_minutes_per_part", "Assembly minutes per part", "number", "min/part", 0, None, 0, "Quote",
       "Minutes to assemble and install each part"),
    _d("pallet_count", "Pallets", "integer", "pallet", 0, None, 0, "Quote", "Pallets to pack and ship"),
    _d("freight_rate", "Freight rate", "currency", "AUD/pallet", 0, help="Packing and freight per pallet"),
    _d("design_hours", "Design hours", "number", "h", 0, None, 0, "Quote", "Design time for the job"),
    _d("design_rate", "Design rate", "currency", "AUD/h", 0, help="Design time per hour"),
    _d("board_ect", "Edge crush strength (ECT)", "number", "kN/m", 0.001, None, None, "Board",
       "From the board supplier's data sheet; used by the indicative structural check"),
    _d("seat_safety_factor", "Safety factor for seats", "number", "", 1, 20, 3, "Board",
       "Seats take impact loads"),
    _d("load_safety_factor", "Safety factor for counters and shelves", "number", "", 1, 20, 2, "Board"),
    _d("max_panel_ratio", "Rib panel limit", "number", "x thickness", 1, None, 50, "Board",
       "Widest unbraced rib panel, as a multiple of the board thickness, before a buckling warning"),
    _d("max_span_ratio", "Top span limit", "number", "x thickness", 1, None, 50, "Board",
       "Longest span of a counter or shelf top between ribs, as a multiple of the board thickness"),
)}


def settings_dir(home: str) -> str:
    return os.path.join(documents_dir(home), "Settings")


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fmt(v) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def validate_value(defn: Definition, raw) -> Any:
    """The value to store, or SettingsError with a message the designer can act on."""
    label = defn.label
    if defn.type == "text":
        if raw is None:
            return ""
        if not isinstance(raw, (str, int, float)) or isinstance(raw, bool):
            raise SettingsError(f"{label} must be text")
        return str(raw).strip()[:500]
    if defn.type == "boolean":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str) and raw.strip().lower() in ("true", "yes", "on", "false", "no", "off"):
            return raw.strip().lower() in ("true", "yes", "on")
        raise SettingsError(f"{label} must be yes or no")
    # numbers
    if isinstance(raw, bool):
        raise SettingsError(f"{label} must be a number")
    value = raw
    if isinstance(raw, str):
        text = raw.strip().replace(",", "")
        text = re.sub(r"^[A-Z]{3}\s*|^\$\s*|\s*%$", "", text)
        if not text:
            raise SettingsError(f"{label} must be a number")
        try:
            value = float(text)
        except ValueError:
            raise SettingsError(f"{label} must be a number (got {raw!r})") from None
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SettingsError(f"{label} must be a number")
    value = float(value)
    if defn.type == "integer":
        if not value.is_integer():
            raise SettingsError(f"{label} must be a whole number")
        value = int(value)
    unit = f" {defn.unit}" if defn.unit else ""
    if defn.minimum is not None and value < defn.minimum:
        raise SettingsError(f"{label} must not be below {_fmt(defn.minimum)}{unit} (got {_fmt(value)})")
    if defn.maximum is not None and value > defn.maximum:
        raise SettingsError(f"{label} must not be above {_fmt(defn.maximum)}{unit} (got {_fmt(value)})")
    return value


def definition_from(raw, *, existing: Optional[Definition] = None) -> Definition:
    """A definition from JSON (a new field Claude or a person adds). Raises SettingsError."""
    if not isinstance(raw, dict):
        raise SettingsError("A setting definition must be an object")
    key = raw.get("key")
    if not isinstance(key, str) or not KEY_RE.match(key):
        raise SettingsError("A setting key must be lower case letters, digits and _ (for example glue_per_joint)")
    if existing is not None:
        return existing
    label = raw.get("label")
    type_ = raw.get("type")
    if not isinstance(label, str) or not label.strip():
        raise SettingsError(f"New setting {key} needs a label (and a type: {', '.join(TYPES)})")
    if type_ not in TYPES:
        raise SettingsError(f"New setting {key} needs a type: one of {', '.join(TYPES)}")
    unit = raw.get("unit") or ""
    if not isinstance(unit, str):
        raise SettingsError(f"{key}: unit must be text")
    if unit:
        from .formulas import FormulaError, parse_unit

        try:
            parse_unit(unit)
        except FormulaError as exc:
            raise SettingsError(f"{key}: {exc}") from None
    if type_ == "percent":
        unit = "%"
    bounds = {}
    for b in ("minimum", "maximum"):
        v = raw.get(b)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)):
            raise SettingsError(f"{key}: {b} must be a number")
        bounds[b] = v
    if type_ == "currency" and bounds["minimum"] is None:
        bounds["minimum"] = 0
    help_ = raw.get("help") or ""
    group = raw.get("group") or "Costs"
    return Definition(key, label.strip()[:100], type_, unit.strip()[:30], bounds["minimum"], bounds["maximum"],
                      None, str(group)[:60], str(help_)[:300])


def _read_json(path: str):
    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            return None
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _empty() -> dict:
    return {"schema": SETTINGS_SCHEMA, "definitions": [], "values": {}}


def _clean_file(raw) -> dict:
    """A settings file's content, keeping only well-formed entries."""
    out = _empty()
    if not isinstance(raw, dict):
        return out
    defs = raw.get("definitions")
    if isinstance(defs, list):
        for d in defs[:MAX_DEFINITIONS]:
            if isinstance(d, dict) and isinstance(d.get("key"), str) and d["key"] not in BUILTIN_DEFINITIONS:
                try:
                    out["definitions"].append(definition_from(d).to_dict())
                except SettingsError:
                    continue
    vals = raw.get("values")
    if isinstance(vals, dict):
        for k, v in vals.items():
            if isinstance(k, str) and isinstance(v, dict) and "value" in v:
                out["values"][k] = {"value": v["value"], "set_by": str(v.get("set_by") or ""),
                                    "set_at": str(v.get("set_at") or "")}
    return out


class SettingsStore:
    """Resolves settings across the layers. Files are small and read on every use.

    ``workflow_values()`` gives the active workflow's settings; ``project_folder()``
    the open project's folder (None before it is saved). Until a project is saved,
    project and concept values are kept in memory and written when it is saved.
    """

    def __init__(self, home: str, workflow_values: Callable[[], Dict[str, Any]],
                 project_folder: Callable[[], Optional[str]]):
        self.home = home
        self._workflow_values = workflow_values
        self._project_folder = project_folder
        self.project_data: dict = _empty()
        self.concept_data: dict = _empty()
        self.on_change: Optional[Callable[[], None]] = None

    # ---- files ---------------------------------------------------------------
    @property
    def defaults_path(self) -> str:
        return os.path.join(settings_dir(self.home), SETTINGS_FILE)

    def _defaults(self) -> dict:
        return _clean_file(_read_json(self.defaults_path))

    def load_project(self, folder: Optional[str]) -> None:
        """Take the project layer from a project folder (opening a project)."""
        self.project_data = _clean_file(_read_json(os.path.join(folder, SETTINGS_FILE))) if folder else _empty()
        self.concept_data = _empty()

    def clear_project(self) -> None:
        self.project_data = _empty()
        self.concept_data = _empty()

    def project_file_text(self) -> str:
        return json.dumps(self.project_data, indent=2, ensure_ascii=False) + "\n"

    def _write(self, path: str, data: dict) -> None:
        from .project import _write_atomic

        _write_atomic(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    # ---- definitions -----------------------------------------------------------
    def definitions(self) -> Dict[str, Definition]:
        out = dict(BUILTIN_DEFINITIONS)
        for data in (self._defaults(), self.project_data, self.concept_data):
            for d in data["definitions"]:
                out.setdefault(d["key"], Definition(**d))
        return out

    def definition(self, key: str) -> Definition:
        defs = self.definitions()
        if not isinstance(key, str) or key not in defs:
            close = difflib.get_close_matches(str(key), list(defs), n=3)
            hint = f" Did you mean {', '.join(close)}?" if close else ""
            raise SettingsError(f"There is no setting called {key!r}.{hint} Settings: {', '.join(defs)}")
        return defs[key]

    # ---- reading ---------------------------------------------------------------
    def _layers(self) -> Dict[str, Dict[str, dict]]:
        wf = {k: {"value": v, "set_by": "workflow", "set_at": ""}
              for k, v in (self._workflow_values() or {}).items() if v is not None}
        return {"default": self._defaults()["values"], "workflow": wf,
                "project": self.project_data["values"], "concept": self.concept_data["values"]}

    def resolve(self, key: str) -> dict:
        defn = self.definition(key)
        layers = self._layers()
        for layer in reversed(LAYERS):
            entry = layers[layer].get(key)
            if entry is not None and entry.get("value") is not None:
                try:
                    value = validate_value(defn, entry["value"])
                except SettingsError:
                    continue  # a hand-edited bad value does not count
                return {"key": key, "value": value, "layer": layer, "set_by": entry.get("set_by", ""),
                        "set_at": entry.get("set_at", ""), **self._describe(defn)}
        return {"key": key, "value": defn.default, "layer": "default" if defn.default is not None else None,
                "set_by": "Fold Theory" if defn.default is not None else "", "set_at": "", **self._describe(defn)}

    @staticmethod
    def _describe(defn: Definition) -> dict:
        return {"label": defn.label, "type": defn.type, "unit": defn.unit, "minimum": defn.minimum,
                "maximum": defn.maximum, "group": defn.group, "help": defn.help}

    def values(self) -> Dict[str, Any]:
        return {k: self.resolve(k)["value"] for k in self.definitions()}

    def listing(self) -> dict:
        return {"layers": list(LAYERS), "defaults_file": self.defaults_path,
                "project_folder": self._project_folder(),
                "settings": [self.resolve(k) for k in self.definitions()]}

    # ---- writing ---------------------------------------------------------------
    def check(self, key: str, raw, definition: Optional[dict] = None) -> tuple:
        """(Definition, value) for a value about to be stored. Raises SettingsError."""
        defs = self.definitions()
        if key in defs:
            defn = defs[key]
        elif definition is not None:
            defn = definition_from({**definition, "key": key})
        else:
            self.definition(key)  # raises with suggestions
            raise AssertionError("unreachable")
        if raw is None or (isinstance(raw, str) and not raw.strip() and defn.type != "text"):
            raise SettingsError(f"{defn.label} needs a value")
        return defn, validate_value(defn, raw)

    def set(self, key: str, raw, *, scope: str = "project", set_by: str = "", definition: Optional[dict] = None,
            notify: bool = True) -> dict:
        self.set_many({key: raw}, scope=scope, set_by=set_by,
                      definitions={key: definition} if definition else None, notify=notify)
        return self.resolve(key)

    def set_many(self, raw_values: Dict[str, Any], *, scope: str = "project", set_by: str = "",
                 definitions: Optional[Dict[str, dict]] = None, notify: bool = True) -> Dict[str, Any]:
        """Validate every value first, then write them all (all or nothing). Raises SettingsError."""
        layer = SCOPES.get(scope)
        if layer is None:
            raise SettingsError("scope must be project or all")
        checked = {}
        errors = {}
        for key, raw in raw_values.items():
            try:
                checked[key] = self.check(key, raw, (definitions or {}).get(key))
            except SettingsError as exc:
                errors[key] = str(exc)
        if errors:
            err = SettingsError("; ".join(errors.values()))
            err.errors = errors  # type: ignore[attr-defined]
            raise err
        stamp = now_iso()
        who = (set_by or "designer").strip()[:80]
        if layer == "default":
            data = self._defaults()
        elif layer == "project":
            data = self.project_data
        else:
            data = self.concept_data
        known = {d["key"] for d in data["definitions"]}
        for key, (defn, value) in checked.items():
            data["values"][key] = {"value": value, "set_by": who, "set_at": stamp}
            if key not in BUILTIN_DEFINITIONS and key not in known:
                data["definitions"].append(defn.to_dict())
                known.add(key)
        if layer == "default":
            self._write(self.defaults_path, data)
        elif layer == "project":
            folder = self._project_folder()
            if folder:
                self._write(os.path.join(folder, SETTINGS_FILE), data)
        if notify and self.on_change is not None:
            self.on_change()
        return {k: v for k, (_, v) in checked.items()}

    def adopt_legacy(self, values: Dict[str, Any], who: str) -> None:
        """Values from an old project's recipe become project settings, unless already set."""
        for key, raw in values.items():
            if raw is None or key in self.project_data["values"]:
                continue
            try:
                defn, value = self.check(key, raw)
            except SettingsError:
                continue
            self.project_data["values"][key] = {"value": value, "set_by": who, "set_at": ""}


def field_for_form(defn: Definition, current: dict) -> dict:
    """A form field the app draws: label, type, unit, range and the value it holds now."""
    return {"key": defn.key, "label": defn.label, "type": defn.type, "unit": defn.unit,
            "minimum": defn.minimum, "maximum": defn.maximum, "help": defn.help,
            "current": current.get("value"), "current_layer": current.get("layer")}


def missing(store: SettingsStore, keys) -> List[Definition]:
    """The definitions of the keys that have no value in any layer."""
    return [store.definition(k) for k in keys if store.resolve(k)["value"] is None]


__all__ = [
    "BUILTIN_DEFINITIONS", "Definition", "LAYERS", "SETTINGS_SCHEMA", "SettingsError", "SettingsStore",
    "definition_from", "field_for_form", "missing", "settings_dir", "validate_value",
]
