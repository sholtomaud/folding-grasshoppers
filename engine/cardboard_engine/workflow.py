"""Workflows: the steps the Design Assistant follows, and the values it uses.

A workflow is a named list of steps (make the model, make the parts, nest on
sheets, estimate cost, export, save) and saved values for tool inputs (board
size, gap, prices). The built-in Standard workflow makes nesting and costing
part of every design. Workflows are plain JSON files, one per workflow::

    ~/Documents/Fold Theory/Workflows/
        Standard.json        only once the designer changes a saved value
        Big board.json       saved by Claude (save_workflow) or by hand
        .settings.json       {"active": "...", "apply_defaults": true}

With *apply defaults* on, a tool input the designer did not give is taken
from the active workflow. With it off, a missing input the workflow covers
is an error that tells Claude to ask the designer first. A saved value of
``null`` means "always ask": with defaults on, the tool's own default is
used and Claude is told to ask.

The workflow file is validated against the real tool definitions, so a
workflow can only name tools and inputs that exist.
"""

from __future__ import annotations

import copy
import json
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

from .brand import documents_dir
from .project import ProjectError, _write_atomic, safe_project_name

WORKFLOW_SCHEMA = "cardboardcad.workflow/1"
STANDARD_NAME = "Standard"
SETTINGS_FILE = ".settings.json"
MAX_FILE_BYTES = 1024 * 1024
MAX_WORKFLOWS = 200
MAX_STEPS = 30
MAX_TEXT = 2000
MAX_QUESTIONS = 20

STANDARD: dict = {
    "schema": WORKFLOW_SCHEMA,
    "name": STANDARD_NAME,
    "description": "Design, make the parts, nest them on sheets and estimate the cost. "
                   "Cut files and saving only when asked.",
    "steps": [
        {"title": "Model", "tools": ["create_shape", "load_model_file", "scale_model"],
         "instructions": "Make or load the model and set its real size.", "optional": False},
        {"title": "Parts", "tools": ["waffle_slice", "contour_stack", "unfold"],
         "instructions": "Choose the method that suits the form and say why.", "optional": False},
        {"title": "Nest on sheets", "tools": ["nest_sheets"],
         "instructions": "Nest the parts on the workflow's board size.", "optional": False},
        {"title": "Estimate cost", "tools": ["estimate_cost"],
         "instructions": "Estimate machine time and cost. If a price is missing, ask for it, then offer to "
                         "remember it with set_workflow_defaults.", "optional": False},
        {"title": "Cut files", "tools": ["export_cut_files"],
         "instructions": "Only when the designer asks for files.", "optional": True},
        {"title": "Save project", "tools": ["save_project"],
         "instructions": "Only when the designer asks to save.", "optional": True},
    ],
    "defaults": {
        "nest_sheets": {"sheet_width_mm": 1200, "sheet_height_mm": 800, "gap_mm": 5, "margin_mm": 10},
        "estimate_cost": {"sheet_price": None, "machine_rate": None},
    },
    "questions": [],
}


class WorkflowError(ValueError):
    """A workflow that cannot be saved or used. The message is for Claude and the designer."""


def workflows_dir(home: str) -> str:
    return os.path.join(documents_dir(home), "Workflows")


def _fmt(v) -> str:
    if v is None:
        return "ask"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return json.dumps(v) if isinstance(v, (list, str, bool)) else str(v)


def _text(raw, what: str, limit: int = MAX_TEXT) -> str:
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise WorkflowError(f"{what} must be text")
    return raw.strip()[:limit]


class WorkflowStore:
    """Reads and writes the workflow files. Small files, read on every use, so edits by hand count at once.

    ``tools`` maps tool names to their definitions; ``check_args(tool, args)`` validates
    inputs the same way the tools do (it raises ValueError).
    """

    def __init__(self, home: str, tools: Dict[str, dict], check_args: Callable[[dict, dict], dict],
                 workflow_tools=()):
        self.home = home
        self.tools = tools
        self.check_args = check_args
        self.workflow_tools = set(workflow_tools)

    @property
    def folder(self) -> str:
        return workflows_dir(self.home)

    # ---- validation ------------------------------------------------------
    def validate(self, raw) -> dict:
        if not isinstance(raw, dict):
            raise WorkflowError("A workflow must be an object")
        try:
            name = safe_project_name(_text(raw.get("name"), "name", 100))
        except ProjectError:
            raise WorkflowError("Give the workflow a name (letters or numbers)") from None
        steps_raw = raw.get("steps")
        if not isinstance(steps_raw, list) or not steps_raw:
            raise WorkflowError("A workflow needs at least one step")
        if len(steps_raw) > MAX_STEPS:
            raise WorkflowError(f"A workflow can have at most {MAX_STEPS} steps")
        steps = []
        for i, s in enumerate(steps_raw, 1):
            if not isinstance(s, dict):
                raise WorkflowError(f"Step {i} must be an object with title, tools and instructions")
            tools = s.get("tools")
            if isinstance(tools, str):
                tools = [tools]
            if not isinstance(tools, list) or not tools or not all(isinstance(t, str) for t in tools):
                raise WorkflowError(f"Step {i} must list the tools it uses")
            for t in tools:
                if t not in self.tools or t in self.workflow_tools:
                    allowed = ", ".join(n for n in self.tools if n not in self.workflow_tools)
                    raise WorkflowError(f"Step {i} names {t!r}, which is not a design tool. Tools: {allowed}")
            optional = s.get("optional", False)
            if not isinstance(optional, bool):
                raise WorkflowError(f"Step {i}: optional must be true or false")
            steps.append({
                "title": _text(s.get("title"), f"Step {i} title", 100) or tools[0],
                "tools": list(dict.fromkeys(tools)),
                "instructions": _text(s.get("instructions"), f"Step {i} instructions"),
                "optional": optional,
            })
        defaults = self._validate_defaults(raw.get("defaults") or {})
        questions = raw.get("questions") or []
        if not isinstance(questions, list) or not all(isinstance(q, str) for q in questions):
            raise WorkflowError("questions must be a list of text")
        return {
            "schema": WORKFLOW_SCHEMA,
            "name": name,
            "description": _text(raw.get("description"), "description"),
            "steps": steps,
            "defaults": defaults,
            "questions": [q.strip()[:500] for q in questions if q.strip()][:MAX_QUESTIONS],
        }

    def _validate_defaults(self, raw) -> Dict[str, Dict[str, Any]]:
        if not isinstance(raw, dict):
            raise WorkflowError("defaults must be an object: {tool name: {input name: value}}")
        out: Dict[str, Dict[str, Any]] = {}
        for tname, inputs in raw.items():
            tool = self.tools.get(tname)
            if tool is None or tname in self.workflow_tools:
                raise WorkflowError(f"defaults names {tname!r}, which is not a design tool")
            if not isinstance(inputs, dict):
                raise WorkflowError(f"defaults for {tname} must be an object of input values")
            props = tool["input_schema"]["properties"]
            checked: Dict[str, Any] = {}
            for key, value in inputs.items():
                if key not in props:
                    raise WorkflowError(f"{tname} has no input {key!r}. Inputs: {', '.join(props)}")
                if value is None:
                    checked[key] = None
                    continue
                loose = {**tool, "input_schema": {**tool["input_schema"], "required": []}}
                try:
                    checked[key] = self.check_args(loose, {key: value})[key]
                except ValueError as exc:
                    raise WorkflowError(f"defaults for {tname}: {exc}") from None
            if checked:
                out[tname] = checked
        return out

    # ---- files -----------------------------------------------------------
    def _path(self, name: str) -> str:
        return os.path.join(self.folder, safe_project_name(name) + ".json")

    def _read_json(self, path: str):
        try:
            if os.path.getsize(path) > MAX_FILE_BYTES:
                return None
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def all(self) -> List[dict]:
        """Every usable workflow: Standard first (a saved copy replaces the built-in one), then by name."""
        found: Dict[str, dict] = {}
        try:
            files = sorted(f for f in os.listdir(self.folder) if f.endswith(".json") and not f.startswith("."))
        except OSError:
            files = []
        for f in files[:MAX_WORKFLOWS]:
            raw = self._read_json(os.path.join(self.folder, f))
            try:
                wf = self.validate(raw)
            except WorkflowError:
                continue
            found.setdefault(wf["name"].lower(), wf)
        std = found.pop(STANDARD_NAME.lower(), None)
        rest = sorted(found.values(), key=lambda w: w["name"].lower())
        return [std or copy.deepcopy(STANDARD)] + rest

    def get(self, name: str) -> Optional[dict]:
        if not isinstance(name, str):
            return None
        for wf in self.all():
            if wf["name"].lower() == name.strip().lower():
                return wf
        return None

    def save(self, raw, make_active: bool = True) -> dict:
        wf = self.validate(raw)
        _write_atomic(self._path(wf["name"]), json.dumps(wf, indent=2, ensure_ascii=False) + "\n")
        if make_active:
            self.set_settings(active=wf["name"])
        return wf

    # ---- settings --------------------------------------------------------
    def settings(self) -> dict:
        raw = self._read_json(os.path.join(self.folder, SETTINGS_FILE))
        raw = raw if isinstance(raw, dict) else {}
        active = raw.get("active") if isinstance(raw.get("active"), str) else STANDARD_NAME
        apply = raw.get("apply_defaults") if isinstance(raw.get("apply_defaults"), bool) else True
        return {"active": active, "apply_defaults": apply}

    def set_settings(self, active: Optional[str] = None, apply_defaults: Optional[bool] = None) -> dict:
        s = self.settings()
        if active is not None:
            wf = self.get(active)
            if wf is None:
                names = ", ".join(w["name"] for w in self.all())
                raise WorkflowError(f"There is no workflow called {active!r}. Workflows: {names}")
            s["active"] = wf["name"]
        if apply_defaults is not None:
            if not isinstance(apply_defaults, bool):
                raise WorkflowError("apply_defaults must be true or false")
            s["apply_defaults"] = apply_defaults
        _write_atomic(os.path.join(self.folder, SETTINGS_FILE), json.dumps(s, indent=2) + "\n")
        return s

    def active(self) -> dict:
        return self.get(self.settings()["active"]) or copy.deepcopy(STANDARD)

    def listing(self) -> dict:
        s = self.settings()
        active = self.active()
        return {
            "active": active["name"],
            "apply_defaults": s["apply_defaults"],
            "folder": self.folder,
            "workflows": [{"name": w["name"], "description": w["description"],
                           "builtin": w["name"] == STANDARD_NAME, "steps": len(w["steps"])}
                          for w in self.all()],
        }

    def set_defaults(self, defaults, workflow: str = "") -> dict:
        """Merge saved values into a workflow (the active one if none is named) and save it."""
        wf = self.get(workflow) if workflow and workflow.strip() else self.active()
        if wf is None:
            names = ", ".join(w["name"] for w in self.all())
            raise WorkflowError(f"There is no workflow called {workflow!r}. Workflows: {names}")
        new = self._validate_defaults(defaults)
        merged = copy.deepcopy(wf["defaults"])
        for tname, inputs in new.items():
            merged.setdefault(tname, {}).update(inputs)
        wf = {**wf, "defaults": merged}
        return self.save(wf, make_active=False)

    # ---- using a workflow ------------------------------------------------
    def fill(self, tool_name: str, args: dict) -> Tuple[dict, Dict[str, Any], List[str]]:
        """Fill missing inputs from the active workflow.

        Returns (args, used, ask): the inputs to run with, the saved values used, and the
        inputs marked "ask" that were left to the tool's own default. With apply defaults
        off, raises WorkflowError when an input the workflow covers was not given.
        """
        if not isinstance(args, dict):
            return args, {}, []
        wf = self.active()
        covered = wf["defaults"].get(tool_name) or {}
        missing = {k: v for k, v in covered.items() if args.get(k) is None}
        if not missing:
            return args, {}, []
        if not self.settings()["apply_defaults"]:
            hints = ", ".join(f"{k} (saved: {_fmt(v)})" for k, v in missing.items())
            raise WorkflowError(
                f"Apply defaults is off, so ask the designer for these inputs before running {tool_name}: "
                f"{hints}. Then call {tool_name} again with the values they give.")
        used = {k: v for k, v in missing.items() if v is not None}
        ask = [k for k, v in missing.items() if v is None]
        return {**args, **used}, used, ask

    def notes(self, tool_name: str, used: Dict[str, Any], ask: List[str]) -> str:
        wf_name = self.active()["name"]
        out = []
        if used:
            out.append(f"Used workflow \"{wf_name}\" values: " + ", ".join(f"{k} {_fmt(v)}" for k, v in used.items())
                       + ".")
        if ask:
            out.append(f"Workflow \"{wf_name}\" has no saved value for {', '.join(ask)}: ask the designer, "
                       "and offer to remember the answer with set_workflow_defaults.")
        return " ".join(out)

    def next_step(self, tool_name: str) -> str:
        """A hint for Claude after a step succeeds: what the workflow does next."""
        wf = self.active()
        steps = wf["steps"]
        at = next((i for i, s in enumerate(steps) if tool_name in s["tools"]), None)
        if at is None:
            return ""
        for j in range(at + 1, len(steps)):
            if not steps[j]["optional"]:
                s = steps[j]
                return (f"Workflow \"{wf['name']}\" next step {j + 1}: {s['title']} "
                        f"({', '.join(s['tools'])}).")
        if not steps[at]["optional"]:
            return f"That was the last required step of workflow \"{wf['name']}\"."
        return ""

    def prompt_section(self) -> str:
        s = self.settings()
        wf = self.active()
        lines = [f"## Workflow: {wf['name']}"]
        if wf["description"]:
            lines.append(wf["description"])
        if s["apply_defaults"]:
            lines.append(
                "Apply defaults is ON: work through the required steps in order without waiting to be asked, "
                "using the saved values below for any input the designer has not given (the engine fills them "
                "in and tells you). Say briefly which values were used. Values marked \"ask\" are unknown: ask "
                "the designer for them.")
        else:
            lines.append(
                "Apply defaults is OFF: before each step, ask the designer for the inputs that step's tools "
                "have saved values for (offer the saved value as a suggestion), unless they already said in "
                "this conversation. Ask everything a step needs in one short message. The engine refuses "
                "those tools until the values are given.")
        lines.append("Steps:")
        for i, st in enumerate(wf["steps"], 1):
            opt = " (optional: only when asked)" if st["optional"] else ""
            text = f" {st['instructions']}" if st["instructions"] else ""
            lines.append(f"{i}. {st['title']} ({', '.join(st['tools'])}){opt}.{text}")
        if wf["defaults"]:
            lines.append("Saved values:")
            for tname, inputs in wf["defaults"].items():
                lines.append(f"- {tname}: " + ", ".join(f"{k} {_fmt(v)}" for k, v in inputs.items()))
        if wf["questions"]:
            lines.append("Ask these at the start of a new design, in one message, unless already answered:")
            lines.extend(f"- {q}" for q in wf["questions"])
        others = [w["name"] for w in self.all() if w["name"] != wf["name"]]
        if others:
            lines.append("Other saved workflows: " + ", ".join(others) + ". Switch with use_workflow when the "
                         "designer asks.")
        lines.append(
            "When the designer describes a way of working they want repeated (for example \"always nest on "
            "2400 x 1200 honeycomb and quote it\"), offer to save it with save_workflow. Remember a value they "
            "give (a sheet price, a board size) with set_workflow_defaults when they agree.")
        return "\n".join(lines)
