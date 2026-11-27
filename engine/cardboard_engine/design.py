"""The Design Assistant's side of the engine: one design, changed by tools.

Claude runs in the app (the API key never leaves the app). When Claude calls
a tool, the app posts it to ``/api/design/tool`` and this module runs it on
the one design the engine holds: a model, the parts made from it, and where
nesting put them. ``state()`` is what the preview pane draws.

Tool results are short plain text for Claude. Bad input is returned as an
error result (``is_error``) that Claude can read and correct, never raised.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .components.contour import ContourError, ContourInput, contour_stack
from .components.cut_estimate import CutEstimateError, CutEstimateInput, estimate
from .components.export import ExportError, export_files, to_svg
from .components.geometry import GeometryError, Mesh
from .components.mesh_io import MeshFileError, load_mesh_file, parse_obj, scale_mesh, to_obj
from .components.nest import NestError, NestInput, nest
from .components.parts import PartsDoc, PartsError
from .components.primitives import SHAPES, ShapeError, make_shape
from .components.triangulate import extrude
from .components.unfold import UnfoldError, UnfoldInput, unfold
from .components.waffle import WaffleError, WaffleInput, waffle_slice
from . import project as proj
from . import rhino_sync
from .rhino_sync import Override
from .project import ProjectError
from .workflow import WorkflowError, WorkflowStore

INPUT_ERRORS = (
    GeometryError, MeshFileError, ShapeError, PartsError, WaffleError, ContourError,
    NestError, UnfoldError, ExportError, CutEstimateError, ProjectError, WorkflowError,
)
# Tools that read or change workflows (workflow.py), not the design.
WORKFLOW_TOOLS = {"list_workflows", "save_workflow", "use_workflow", "set_workflow_defaults"}
# Tools that do not change the design: a saved project stays saved.
READ_ONLY_TOOLS = {"model_info", "export_cut_files", "save_project"} | WORKFLOW_TOOLS
MODEL_TOOLS = {"create_shape", "load_model_file", "scale_model"}
METHOD_TOOLS = {"waffle_slice", "contour_stack", "unfold"}
SHEET_GAP_MM = 100.0  # between sheets laid side by side for Rhino
MAX_PREVIEW_TRIANGLES = 400_000
MAX_STEPS = 200

SYSTEM_PROMPT = """You are the Design Assistant in CardboardCAD, a Mac app made for Fold Theory, a studio that builds furniture, displays and sculptures from flat cardboard sheets cut on a CNC or laser cutter.

You turn what the designer asks for into calls to the tools you have. The tools all act on one shared design:
1. A 3D model: make one with create_shape, or load the designer's own model with load_model_file (an .stl or .obj exported from Rhino). Use scale_model to set its real size.
2. Parts: turn the model into flat cardboard parts with one method:
   - waffle_slice: an interlocking grid of slotted ribs (X ribs and Y ribs). Light, fast to build, good for big organic forms, stools, lamps.
   - contour_stack: stacked layers glued on top of each other. Solid and heavy, very faithful to the form; uses a lot of board.
   - unfold: folds the model's flat faces into nets with glue tabs. Only for faceted (low-poly) models; it makes hollow shells.
   A new method replaces the previous parts.
3. Sheets: nest_sheets lays the parts onto as few sheets of board as it can.
4. Then estimate_cost (machine time and money) and export_cut_files (DXF or SVG cut files, one per sheet).
The active workflow (below) says which of these steps to run, in what order, and with which saved values.
5. save_project saves everything (model, parts, cut files, estimate, a preview picture) as a project folder the designer can reopen. To present several concepts to a client, save each one under its own name, for example "Acme Expo - Concept A", then "Acme Expo - Concept B".

Everything is in millimetres. Z is up and models sit on the ground. Typical board: single-wall corrugated 3 mm, double-wall 6 mm, honeycomb board 10 to 20 mm. Common sheet sizes: 1200 x 800, 1000 x 700, 2400 x 1200 mm. When the designer gives no value, use the tool defaults and say which you used.

The app shows a live preview next to this chat: the 3D model (and the assembled ribs, layers or folded nets) and the flat cut sheets. It updates after every tool call, so describe results briefly instead of listing every part. Point the designer to the preview when it helps ("see the Sheets tab").

Keep replies short and plain. If a request is ambiguous in a way that changes the result (for example no size at all for a new object), ask one short question, or make a sensible choice and say so. If a tool returns an error, read it, fix the input and try again, or explain the problem. Only call export_cut_files or save_project when the designer asks for files or to save; they write into their Documents folder. For estimate_cost, ask for the sheet price and machine rate if you do not know them, rather than reporting a cost of 0. When a question would block you (no board size, no price) and the workflow does not answer it, ask it plainly and wait.

The designer also works in Rhino, linked live to this app. When they reshape a flat part there, it becomes a Rhino hand edit (an override) that the app keeps through later steps for as long as it still fits the generated part. Treat hand edits as the designer's decisions: build on them and never undo them. Before a step that would change an overridden part (re-slicing with other settings, scaling the model), say which hand edits it affects and ask first. A message may start with notes on what the designer just changed in Rhino. model_info lists the hand edits; clear_rhino_override removes one, only when the designer asks.

Format replies as short paragraphs; use "- " bullet lists only for short lists."""


def _num(desc: str, default=None, minimum=None, maximum=None, integer=False) -> dict:
    d: Dict[str, Any] = {"type": "integer" if integer else "number", "description": desc}
    if default is not None:
        d["default"] = default
    if minimum is not None:
        d["minimum"] = minimum
    if maximum is not None:
        d["maximum"] = maximum
    return d


def _tool(name: str, description: str, properties: dict, required=()) -> dict:
    schema: Dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = list(required)
    return {"name": name, "description": description, "input_schema": schema}


TOOLS: List[dict] = [
    _tool(
        "create_shape",
        "Start a new design from a simple solid: box, cylinder, cone, sphere or pyramid. It sits on the ground, "
        "centred. Width is along X, depth along Y, height along Z, all in mm. Replaces the current model and parts.",
        {
            "shape": {"type": "string", "enum": list(SHAPES), "description": "Which solid"},
            "width_mm": _num("Size along X in mm (the diameter for round shapes)", 300),
            "depth_mm": _num("Size along Y in mm", 300),
            "height_mm": _num("Size along Z in mm", 300),
            "segments": _num("How many flat sides round shapes get (3-256). Few sides suit unfold.", 32, 3, 256, True),
        },
        required=["shape"],
    ),
    _tool(
        "load_model_file",
        "Load the designer's own model from an .stl or .obj file in their home folder (for example one exported "
        "from Rhino with File > Export Selected). Units are taken as mm. Replaces the current model and parts.",
        {"path": {"type": "string", "description": "Absolute path, or one starting with ~/"}},
        required=["path"],
    ),
    _tool(
        "scale_model",
        "Scale the current model uniformly, about the centre of its base. Give exactly one of: factor, or the "
        "target height_mm, width_mm or depth_mm. Clears parts made at the old size.",
        {
            "factor": _num("Multiply every size by this"),
            "height_mm": _num("Scale so the height (Z) becomes this"),
            "width_mm": _num("Scale so the width (X) becomes this"),
            "depth_mm": _num("Scale so the depth (Y) becomes this"),
        },
    ),
    _tool(
        "model_info",
        "Describe the current design: model size, face count, which method made the parts, and the nesting.",
        {},
    ),
    _tool(
        "waffle_slice",
        "Slice the model into an interlocking grid of flat ribs: X ribs (planes across X) slotted from the top and "
        "Y ribs slotted from the bottom, each slot half the height of the crossing. Replaces previous parts.",
        {
            "x_ribs": _num("Number of ribs across X", 5, 0, 200, True),
            "y_ribs": _num("Number of ribs across Y", 5, 0, 200, True),
            "thickness_mm": _num("Board thickness in mm (the slot width before clearance)", 3.0),
            "clearance_mm": _num("Extra slot width so the ribs slide together, mm", 0.2),
            "kerf_mm": _num("Cutter width in mm; outlines grow by half of it", 0.0),
        },
    ),
    _tool(
        "contour_stack",
        "Slice the model into stacked layers of board, one per thickness, each etched with where its neighbours "
        "sit so they glue up in line. Replaces previous parts.",
        {
            "thickness_mm": _num("Board thickness in mm: one layer per band this thick", 3.0),
            "axis": {"type": "string", "enum": ["x", "y", "z"], "default": "z",
                     "description": "Stacking direction; z stacks horizontal layers"},
            "etch": {"type": "boolean", "default": True, "description": "Etch alignment lines"},
            "kerf_mm": _num("Cutter width in mm", 0.0),
        },
    ),
    _tool(
        "unfold",
        "Unfold the model's flat faces into nets with fold (score) lines, glue tabs and matching edge numbers. "
        "Best for faceted models such as boxes, pyramids, low-segment shapes. Replaces previous parts.",
        {
            "tab_width_mm": _num("Glue tab width in mm; 0 for no tabs", 8.0),
            "tab_angle_deg": _num("Slope of the tab sides in degrees (0-90)", 45.0),
            "flat_angle_deg": _num("Folds flatter than this many degrees are not scored", 1.0),
        },
    ),
    _tool(
        "nest_sheets",
        "Lay the current parts onto as few sheets of board as possible (true shape). Run after a method.",
        {
            "sheet_width_mm": _num("Sheet width in mm", 1200.0),
            "sheet_height_mm": _num("Sheet height in mm", 800.0),
            "gap_mm": _num("Minimum space between parts, mm", 5.0),
            "margin_mm": _num("Minimum space from the sheet edge, mm", 10.0),
            "rotations": {"type": "array", "items": {"type": "number"}, "default": [0, 90],
                          "description": "Allowed rotations in degrees; [0] keeps the grain direction"},
        },
    ),
    _tool(
        "estimate_cost",
        "Estimate cutting time and cost of the current parts. Cut length, score length, pierces and sheet count "
        "are measured from the design (nest first for a real sheet count); give machine speeds and prices.",
        {
            "cut_speed": _num("Cutting speed, mm/s", 20.0),
            "score_speed": _num("Scoring and etching speed, mm/s", 40.0),
            "pierce_time": _num("Seconds per pierce", 0.5),
            "travel_overhead": _num("Extra time for travel moves, as a fraction", 0.15),
            "sheet_price": _num("Price of one sheet", 0.0),
            "machine_rate": _num("Machine cost per hour", 0.0),
        },
    ),
    _tool(
        "export_cut_files",
        "Write the cut files, one per sheet, with layers CUT, SCORE, ETCH and LABEL. Only when the designer asks.",
        {
            "format": {"type": "string", "enum": ["dxf", "svg"], "default": "dxf", "description": "File format"},
            "name": {"type": "string", "default": "cardboardcad", "description": "File name stem"},
            "folder": {"type": "string", "default": "",
                       "description": "Folder inside the home folder; empty means ~/Documents/CardboardCAD"},
        },
    ),
    _tool(
        "save_project",
        "Save the whole design as a project folder: the model, parts, nested sheets, DXF and SVG cut files, the "
        "cost estimate and a preview picture. It can be reopened later from File > Open Project. Saving again "
        "under the same name updates it. Only when the designer asks to save.",
        {
            "name": {"type": "string", "default": "",
                     "description": "Project name, e.g. \"Acme Expo - Concept A\". Empty keeps the current project's name"},
            "folder": {"type": "string", "default": "",
                       "description": "Project folder inside the home folder; empty means "
                                      "~/Documents/CardboardCAD/Projects/<name>"},
        },
    ),
]

_STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Short name of the step, e.g. \"Nest on sheets\""},
        "tools": {"type": "array", "items": {"type": "string"},
                  "description": "The design tools this step uses, e.g. [\"nest_sheets\"]"},
        "instructions": {"type": "string", "description": "How to do the step, in the designer's words"},
        "optional": {"type": "boolean", "description": "true: only when the designer asks (e.g. export)"},
    },
    "required": ["title", "tools"],
}

TOOLS += [
    _tool(
        "list_workflows",
        "List the saved workflows (the step-by-step ways of working the assistant follows), which one is "
        "active, and whether apply defaults is on.",
        {},
    ),
    _tool(
        "save_workflow",
        "Save a workflow from this conversation so the assistant follows it in future designs: its steps in "
        "order (each naming the design tools it uses), saved values for tool inputs (board size, gap, prices) "
        "and questions to ask at the start. Saving under an existing name replaces that workflow. Only after "
        "the designer agrees to save it.",
        {
            "name": {"type": "string", "description": "Workflow name, e.g. \"Honeycomb stools\""},
            "description": {"type": "string", "default": "", "description": "One sentence on when to use it"},
            "steps": {"type": "array", "items": _STEP_SCHEMA, "description": "The steps, in order"},
            "defaults": {"type": "object", "default": {},
                         "description": "Saved input values per tool: {tool name: {input name: value}}, e.g. "
                                        "{\"nest_sheets\": {\"sheet_width_mm\": 2400, \"sheet_height_mm\": 1200}}. "
                                        "Use null for a value to always ask the designer for."},
            "questions": {"type": "array", "items": {"type": "string"}, "default": [],
                          "description": "Questions to ask at the start of a new design, e.g. \"Who is the client?\""},
            "make_active": {"type": "boolean", "default": True, "description": "Use it from now on"},
        },
        required=["name", "steps"],
    ),
    _tool(
        "use_workflow",
        "Switch to another saved workflow (see list_workflows). Only when the designer asks.",
        {"name": {"type": "string", "description": "The workflow's name"}},
        required=["name"],
    ),
    _tool(
        "set_workflow_defaults",
        "Remember input values in a workflow, merged into what it already saves, e.g. the designer's sheet "
        "price and machine rate: {\"estimate_cost\": {\"sheet_price\": 12.5, \"machine_rate\": 60}}. "
        "Only after the designer agrees.",
        {
            "defaults": {"type": "object", "description": "{tool name: {input name: value}}"},
            "workflow": {"type": "string", "default": "", "description": "Workflow name; empty means the active one"},
        },
        required=["defaults"],
    ),
]
TOOLS.append(_tool(
    "clear_rhino_override",
    "Remove the designer's Rhino hand edit (override) of one flat part, so the part goes back to the shape the "
    "method makes. Only when the designer asks; model_info lists the parts with hand edits.",
    {"part": {"type": "string", "description": "The part id, e.g. \"X3\""}},
    required=["part"],
))
_TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}


class ToolInputError(ValueError):
    pass


def _check_args(tool: dict, args) -> Dict[str, Any]:
    """Validate ``args`` against the tool's schema and fill in defaults."""
    if not isinstance(args, dict):
        raise ToolInputError("Tool input must be an object")
    schema = tool["input_schema"]
    props = schema["properties"]
    unknown = sorted(set(args) - set(props))
    if unknown:
        raise ToolInputError(f"Unknown input(s): {', '.join(unknown)}. Allowed: {', '.join(props) or 'none'}")
    for req in schema.get("required", []):
        if req not in args:
            raise ToolInputError(f"{req} is required")
    out = {}
    for name, spec in props.items():
        if name not in args or args[name] is None:
            if "default" in spec:
                out[name] = spec["default"]
            continue
        v = args[name]
        t = spec["type"]
        if t in ("number", "integer"):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise ToolInputError(f"{name} must be a number")
            if t == "integer":
                if int(v) != v:
                    raise ToolInputError(f"{name} must be a whole number")
                v = int(v)
            if "minimum" in spec and v < spec["minimum"]:
                raise ToolInputError(f"{name} must be at least {spec['minimum']}")
            if "maximum" in spec and v > spec["maximum"]:
                raise ToolInputError(f"{name} must be at most {spec['maximum']}")
        elif t == "string":
            if not isinstance(v, str):
                raise ToolInputError(f"{name} must be text")
            if "enum" in spec and v.strip().lower() not in spec["enum"]:
                raise ToolInputError(f"{name} must be one of: {', '.join(spec['enum'])}")
        elif t == "boolean":
            if not isinstance(v, bool):
                raise ToolInputError(f"{name} must be true or false")
        elif t == "array":
            item = (spec.get("items") or {}).get("type", "number")
            if item == "string":
                if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                    raise ToolInputError(f"{name} must be a list of text")
            elif item == "object":
                if not isinstance(v, list) or not all(isinstance(x, dict) for x in v):
                    raise ToolInputError(f"{name} must be a list of objects")
            elif not isinstance(v, list) or not all(
                isinstance(x, (int, float)) and not isinstance(x, bool) for x in v
            ):
                raise ToolInputError(f"{name} must be a list of numbers")
        elif t == "object":
            if not isinstance(v, dict):
                raise ToolInputError(f"{name} must be an object")
        out[name] = v
    return out


@dataclass
class ToolResult:
    text: str
    is_error: bool = False
    # The system prompt changed (a workflow tool ran): the app fetches it again.
    setup_changed: bool = False


def _mm(v: float) -> str:
    return f"{v:.0f}" if abs(v - round(v)) < 0.05 else f"{v:.1f}"


def _length(poly, closed: bool) -> float:
    pts = list(poly)
    if closed and pts and tuple(pts[0]) != tuple(pts[-1]):
        pts.append(pts[0])
    return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


class DesignSession:
    """The one design the engine holds. Thread-safe: preview polls run beside tool calls."""

    def __init__(self, home: str):
        self.home = home
        self._lock = threading.RLock()
        self.version = 0
        self._state_cache: Optional[dict] = None
        self.project: Optional[dict] = None  # {name, folder, saved_version}
        self.workflows = WorkflowStore(home, _TOOLS_BY_NAME, _check_args, WORKFLOW_TOOLS)
        # Rhino hand edits of flat parts, by part id. Kept through re-runs (see rhino_sync.py).
        self.overrides: Dict[str, Override] = {}
        self.notes: List[str] = []    # Rhino edits not yet told to the Design Assistant
        self.notices: List[str] = []  # recent things the app should show (conflicts, stale edits)
        self._clear_all()
        self.steps: List[dict] = []

    # ---- state -----------------------------------------------------------
    def _clear_all(self) -> None:
        self.model: Optional[Mesh] = None
        self.model_name: Optional[str] = None
        self.recipe: Dict[str, Any] = {}
        self._clear_parts()

    def _clear_parts(self) -> None:
        self.method: Optional[str] = None
        self.method_summary: Optional[str] = None
        self.parts: Optional[PartsDoc] = None
        self.parts3d: Dict[str, list] = {}
        self.generated: Dict[str, Any] = {}  # the parts as the method made them, before hand edits
        self.board_thickness: float = 0.0
        self.nested: Optional[dict] = None
        self.last_estimate: Optional[dict] = None
        self.recipe = {}

    def _bump(self) -> None:
        self.version += 1
        self._state_cache = None

    def reset(self) -> None:
        with self._lock:
            self._clear_all()
            self.overrides, self.notes, self.notices = {}, [], []
            self.steps = []
            self.project = None
            self._bump()

    # ---- tools -----------------------------------------------------------
    def run_tool(self, name: str, args) -> ToolResult:
        tool = _TOOLS_BY_NAME.get(name)
        if tool is None:
            return ToolResult(f"There is no tool called {name!r}. Tools: {', '.join(_TOOLS_BY_NAME)}", True)
        with self._lock:
            was_saved = self._is_saved()
            try:
                used, ask = {}, []
                if name not in WORKFLOW_TOOLS:
                    args, used, ask = self.workflows.fill(name, args)
                checked = _check_args(tool, args)
                text = getattr(self, "_t_" + name)(**checked)
                if name not in WORKFLOW_TOOLS:
                    extra = " ".join(x for x in (self.workflows.notes(name, used, ask),
                                                 self.workflows.next_step(name)) if x)
                    if extra:
                        text += "\n" + extra
                result = ToolResult(text, setup_changed=name in WORKFLOW_TOOLS - {"list_workflows"})
                self._record(name, checked)
            except (ToolInputError, *INPUT_ERRORS) as exc:
                result = ToolResult(str(exc), True)
            self._log_step(name, not result.is_error, result.text)
            self._bump()
            if self.project is not None and (was_saved or name == "save_project") and (
                    result.is_error or name in READ_ONLY_TOOLS):
                self.project["saved_version"] = self.version
            return result

    def system_prompt(self) -> str:
        """The system prompt for Claude: the fixed part and the active workflow."""
        return SYSTEM_PROMPT + "\n\n" + self.workflows.prompt_section()

    # ---- workflow tools ----------------------------------------------------
    def _t_list_workflows(self):
        info = self.workflows.listing()
        lines = [f"Active workflow: {info['active']}. Apply defaults is {'on' if info['apply_defaults'] else 'off'} "
                 "(the designer changes that in the app). Saved in " + info["folder"]]
        for w in info["workflows"]:
            tag = " (built in)" if w["builtin"] else ""
            desc = f": {w['description']}" if w["description"] else ""
            lines.append(f"- {w['name']}{tag}, {w['steps']} steps{desc}")
        return "\n".join(lines)

    def _t_save_workflow(self, name, steps, description="", defaults=None, questions=None, make_active=True):
        wf = self.workflows.save({"name": name, "description": description, "steps": steps,
                                  "defaults": defaults or {}, "questions": questions or []},
                                 make_active=make_active)
        state = "It is now the active workflow." if make_active else "The active workflow is unchanged."
        return (f"Saved workflow \"{wf['name']}\" with {len(wf['steps'])} steps to "
                f"{self.workflows._path(wf['name'])}. {state}")

    def _t_use_workflow(self, name):
        s = self.workflows.set_settings(active=name)
        wf = self.workflows.active()
        return f"Now using workflow \"{s['active']}\": " + ", ".join(st["title"] for st in wf["steps"]) + "."

    def _t_set_workflow_defaults(self, defaults, workflow=""):
        wf = self.workflows.set_defaults(defaults, workflow)
        saved = "; ".join(f"{t}: " + ", ".join(f"{k} {v if v is not None else 'ask'}" for k, v in ins.items())
                          for t, ins in wf["defaults"].items())
        return f"Workflow \"{wf['name']}\" now saves: {saved}."

    def _log_step(self, name: str, ok: bool, text: str) -> None:
        self.steps.append({"tool": name, "ok": ok, "summary": text.split("\n")[0][:200]})
        del self.steps[:-MAX_STEPS]

    def _record(self, name: str, checked: dict) -> None:
        """Remember the inputs of the steps that made the design, so a saved project can be rebuilt."""
        if name in MODEL_TOOLS:
            self.recipe = {}
        elif name in METHOD_TOOLS:
            self.recipe = {"method": {"tool": name, "input": dict(checked)}}
        elif name == "nest_sheets":
            self.recipe = {"method": self.recipe.get("method"), "nest": dict(checked)}
        elif name == "estimate_cost":
            self.recipe["estimate"] = dict(checked)

    def _is_saved(self) -> bool:
        return self.project is not None and self.project["saved_version"] == self.version

    def _need_model(self) -> Mesh:
        if self.model is None:
            raise ToolInputError("There is no model yet. Create one with create_shape or load one with load_model_file.")
        return self.model

    def _need_parts(self) -> PartsDoc:
        if self.parts is None:
            raise ToolInputError("There are no parts yet. Run waffle_slice, contour_stack or unfold first.")
        return self.parts

    def _describe_model(self) -> str:
        m = self.model
        (lx, ly, lz), (hx, hy, hz) = m.bbox()
        return (f"{self.model_name}: {_mm(hx - lx)} x {_mm(hy - ly)} x {_mm(hz - lz)} mm (W x D x H), "
                f"{len(m.faces)} faces")

    def _set_model(self, mesh: Mesh, name: str, keep_overrides: bool = False) -> None:
        self._clear_all()
        if not keep_overrides:
            self.overrides = {}
        self.model, self.model_name = mesh, name

    def _t_create_shape(self, shape, width_mm=300, depth_mm=300, height_mm=300, segments=32):
        mesh = make_shape(shape, width=width_mm, depth=depth_mm, height=height_mm, segments=segments)
        self._set_model(mesh, shape.strip().lower())
        return "Created " + self._describe_model()

    def _t_load_model_file(self, path):
        mesh = load_mesh_file(path, home=self.home)
        import os

        self._set_model(mesh, os.path.basename(path.rstrip("/")))
        text = "Loaded " + self._describe_model()
        (lx, ly, lz), (hx, hy, hz) = mesh.bbox()
        if max(hx - lx, hy - ly, hz - lz) < 20:
            text += ". It is very small; it may be in cm, m or inches: ask, then scale_model."
        return text

    def _t_scale_model(self, factor=None, height_mm=None, width_mm=None, depth_mm=None):
        mesh = self._need_model()
        given = {k: v for k, v in dict(factor=factor, height_mm=height_mm, width_mm=width_mm, depth_mm=depth_mm).items()
                 if v is not None}
        if len(given) != 1:
            raise ToolInputError("Give exactly one of factor, height_mm, width_mm or depth_mm")
        (key, value), = given.items()
        (lx, ly, lz), (hx, hy, hz) = mesh.bbox()
        current = {"factor": 1.0, "width_mm": hx - lx, "depth_mm": hy - ly, "height_mm": hz - lz}[key]
        if current <= 0:
            raise ToolInputError(f"The model has no {key.split('_')[0]} to scale")
        if value <= 0:
            raise ToolInputError(f"{key} must be greater than 0")
        f = value if key == "factor" else value / current
        name = self.model_name
        had_parts = self.parts is not None
        self._set_model(scale_mesh(mesh, f), name, keep_overrides=True)
        text = f"Scaled by {f:.4g}. Now " + self._describe_model()
        if had_parts:
            text += ". The old parts were cleared; run a method again."
        return text

    def _t_model_info(self):
        if self.model is None:
            return "No model yet."
        lines = [self._describe_model()]
        if self.parts is not None:
            lines.append(f"Parts: {self.method_summary}")
        if self.nested is not None:
            n = self.nested
            lines.append(f"Nested on {n['sheet_count']} sheet(s) of {_mm(n['sheet_width'])} x {_mm(n['sheet_height'])} mm")
        if self.overrides:
            lines.append("Rhino hand edits (keep them; ask before changing these parts): " + "; ".join(
                f"{o.part} ({o.summary}, {'in use' if o.status == 'applied' else o.status})"
                for o in self.overrides.values()))
        return "\n".join(lines)

    def _parts_text(self, parts) -> str:
        ids = [p.id for p in parts]
        shown = ", ".join(ids[:12]) + (", ..." if len(ids) > 12 else "")
        sizes = [p.bbox() for p in parts]
        big = max(sizes, key=lambda b: (b[1][0] - b[0][0]) * (b[1][1] - b[0][1]))
        return (f"{len(parts)} parts ({shown}). Largest part {_mm(big[1][0] - big[0][0])} x "
                f"{_mm(big[1][1] - big[0][1])} mm. Not nested yet.")

    def _t_waffle_slice(self, x_ribs=5, y_ribs=5, thickness_mm=3.0, clearance_mm=0.2, kerf_mm=0.0):
        r = waffle_slice(self._need_model(), WaffleInput(nx=x_ribs, ny=y_ribs, thickness=thickness_mm,
                                                       clearance=clearance_mm, kerf=kerf_mm))
        self._clear_parts()
        self.method, self.parts, self.parts3d = "waffle", PartsDoc(parts=r.parts), r.ribs3d
        self.board_thickness = thickness_mm
        self.method_summary = f"waffle, {x_ribs} X ribs and {y_ribs} Y ribs, {_mm(thickness_mm)} mm board"
        return f"Waffle: {self._parts_text(r.parts)}" + self._after_method()

    def _t_contour_stack(self, thickness_mm=3.0, axis="z", etch=True, kerf_mm=0.0):
        r = contour_stack(self._need_model(), ContourInput(thickness=thickness_mm, kerf=kerf_mm, etch=etch, axis=axis))
        self._clear_parts()
        self.method, self.parts, self.parts3d = "contour", PartsDoc(parts=r.parts), r.layers3d
        self.board_thickness = thickness_mm
        self.method_summary = f"contour stack, {len(r.parts)} layers of {_mm(thickness_mm)} mm board along {axis}"
        return f"Contour stack: {self._parts_text(r.parts)}" + self._after_method()

    def _t_unfold(self, tab_width_mm=8.0, tab_angle_deg=45.0, flat_angle_deg=1.0):
        r = unfold(self._need_model(), UnfoldInput(tab_width=tab_width_mm, tab_angle=tab_angle_deg,
                                                  flat_angle=flat_angle_deg))
        self._clear_parts()
        self.method, self.parts = "unfold", PartsDoc(parts=r.parts)
        self.method_summary = f"unfold, {len(r.parts)} net(s) from {r.face_count} faces, {r.tab_count} glue tabs"
        return f"Unfold: {len(r.parts)} net(s), {r.tab_count} glue tabs. " + self._parts_text(r.parts) + \
            self._after_method()

    def _after_method(self) -> str:
        """Apply the Rhino hand edits that still fit to the parts a method just made."""
        before = {k: o.status for k, o in self.overrides.items()}
        parts, self.generated, note = rhino_sync.apply_overrides(self.parts.parts, self.overrides)
        self.parts = PartsDoc(parts=parts)
        for k, o in self.overrides.items():
            if o.status != "applied" and before.get(k) != o.status:
                self._notice(f"The Rhino hand edit of {k} no longer fits ({o.status}); the generated {k} is used.")
        return (" " + note) if note else ""

    def _t_clear_rhino_override(self, part):
        if part not in self.overrides:
            have = ", ".join(self.overrides) or "none"
            raise ToolInputError(f"{part} has no Rhino hand edit. Parts with one: {have}")
        del self.overrides[part]
        ok, err = self._run_recipe(dict(self.recipe))
        text = f"Cleared the Rhino hand edit of {part}; it is back to the shape the method makes."
        return text if ok else text + " " + err

    def _run_recipe(self, recipe: dict):
        """Run the stored method, nesting and estimate again. Returns (ok, why it stopped)."""
        method = recipe.get("method")
        if not method:
            return True, ""
        steps = [(method["tool"], method.get("input", {}))]
        for key, tname in (("nest", "nest_sheets"), ("estimate", "estimate_cost")):
            if recipe.get(key) is not None:
                steps.append((tname, recipe[key]))
        for tname, args in steps:
            try:
                checked = _check_args(_TOOLS_BY_NAME[tname], args)
                getattr(self, "_t_" + tname)(**checked)
                self._record(tname, checked)
            except (ToolInputError, *INPUT_ERRORS) as exc:
                return False, f"The {tname} step could not run again: {exc}"
        return True, ""

    def _t_nest_sheets(self, sheet_width_mm=1200.0, sheet_height_mm=800.0, gap_mm=5.0, margin_mm=10.0,
                       rotations=(0, 90)):
        doc = self._need_parts()
        inp = NestInput(sheet_width=sheet_width_mm, sheet_height=sheet_height_mm, gap=gap_mm, margin=margin_mm,
                        rotations=tuple(float(r) for r in rotations) or (0.0,))
        r = nest(PartsDoc(parts=[p for p in doc.parts]), inp)
        self.parts = r.doc
        self.last_estimate = None
        self.nested = {"sheet_count": r.sheet_count, "utilisation": list(r.utilisation),
                       "sheet_width": sheet_width_mm, "sheet_height": sheet_height_mm}
        use = ", ".join(f"{u * 100:.0f}%" for u in r.utilisation)
        return (f"Nested {len(r.doc.parts)} parts on {r.sheet_count} sheet(s) of {_mm(sheet_width_mm)} x "
                f"{_mm(sheet_height_mm)} mm. Board used per sheet: {use}.")

    def _t_estimate_cost(self, **machine):
        doc = self._need_parts()
        cut = sum(_length(loop, True) for p in doc.parts for loop in p.cut)
        score = sum(_length(line, False) for p in doc.parts for line in (*p.score, *p.etch))
        pierces = sum(len(p.cut) + len(p.score) + len(p.etch) for p in doc.parts)
        sheets = self.nested["sheet_count"] if self.nested else 1
        r = estimate(CutEstimateInput(cut_length=cut, score_length=score, pierce_count=pierces,
                                      sheet_count=sheets, **machine))
        self.last_estimate = {"cut_length_mm": cut, "score_length_mm": score, "pierce_count": pierces,
                              "sheet_count": sheets, **r.to_dict()}
        text = (f"Cut {cut / 1000:.2f} m, score/etch {score / 1000:.2f} m, {pierces} pierces, {sheets} sheet(s). "
                f"Machine time {r.time_min:.1f} min. Material {r.material_cost:.2f}, machine {r.machine_cost:.2f}, "
                f"Total {r.total_cost:.2f}.")
        if not self.nested:
            text += " (Not nested yet, so 1 sheet was assumed.)"
        if not machine.get("sheet_price") and not machine.get("machine_rate"):
            text += (" No sheet price or machine rate was given, so the costs are 0: ask the designer "
                     "for their sheet price and hourly machine rate.")
        return text

    def _t_export_cut_files(self, format="dxf", name="cardboardcad", folder=""):
        doc = self._need_parts()
        out = export_files(doc, fmt=format, name=name, folder=folder, write=True, home=self.home)
        text = f"Wrote {len(out.paths)} file(s):\n" + "\n".join(out.paths)
        if not self.nested:
            text += "\n(The parts are not nested on sheets yet, so they are laid out in one row.)"
        return text

    def _t_save_project(self, name="", folder=""):
        if not (name or "").strip() and not (folder or "").strip() and self.project is not None:
            folder = self.project["folder"]
        out = self.save_project(folder=folder, name=name)
        return f"Saved project \"{out['name']}\" ({len(out['files'])} files) to {out['folder']}"

    # ---- projects --------------------------------------------------------
    def save_project(self, folder: str = "", name: str = "", chat=None) -> dict:
        """Write the design as a project folder (see ``project.py``). Raises ProjectError."""
        with self._lock:
            if self.model is None:
                raise ProjectError("There is nothing to save yet: make or load a model first")
            if not isinstance(name, str) or not isinstance(folder, str):
                raise ProjectError("name and folder must be text")
            directory, clean = proj.resolve_save_dir(folder, name, self.home)
            previous = proj.check_writable(directory)
            files = self._project_files(clean)
            if chat is not None:
                files[proj.CHAT] = json.dumps(chat, ensure_ascii=False, indent=1) + "\n"
            manifest_files = sorted(set(files) | {proj.MANIFEST} | (
                {proj.CHAT} if os.path.isfile(os.path.join(directory, proj.CHAT)) else set()))
            files[proj.MANIFEST] = json.dumps(self._manifest(clean, manifest_files), indent=2) + "\n"
            proj.write_files(directory, files, previous)
            self.project = {"name": clean, "folder": directory, "saved_version": self.version}
            return {"name": clean, "folder": directory, "files": manifest_files, "version": self.version}

    def _project_files(self, name: str) -> Dict[str, str]:
        from .components.export import safe_file_stem, to_dxf

        files = {proj.MODEL_FILE: to_obj(self.model, self.model_name or "")}
        if self.parts is not None:
            files["parts/parts.json"] = self.parts.to_json() + "\n"
            stem = re.sub(r"-{2,}", "-", safe_file_stem(name))
            for sheet in self.parts.layout():
                files[f"cut-files/{stem}-sheet{sheet.index + 1}.dxf"] = to_dxf(sheet)
                files[f"cut-files/{stem}-sheet{sheet.index + 1}.svg"] = to_svg(sheet)
        if self.last_estimate is not None:
            files["estimate/estimate.json"] = json.dumps(self.last_estimate, indent=2) + "\n"
            files["estimate/estimate.csv"] = proj.estimate_csv(self.last_estimate)
        # The model, not the ribs: interlocking ribs cannot be drawn back to front.
        # The app adds preview/preview.png, a snapshot of its 3D preview.
        files["preview/model.svg"] = proj.model_svg(self.model.triangles())
        return files

    def _summary(self) -> dict:
        (lx, ly, lz), (hx, hy, hz) = self.model.bbox()
        est = self.last_estimate or {}
        return {
            "size_mm": [round(hx - lx, 2), round(hy - ly, 2), round(hz - lz, 2)],
            "faces": len(self.model.faces),
            "method": self.method,
            "method_summary": self.method_summary,
            "part_count": len(self.parts.parts) if self.parts is not None else 0,
            "sheet_count": self.nested["sheet_count"] if self.nested else (1 if self.parts is not None else 0),
            "sheet_size": [self.nested["sheet_width"], self.nested["sheet_height"]] if self.nested else None,
            "time_min": est.get("time_min"),
            "total_cost": est.get("total_cost"),
        }

    def _manifest(self, name: str, files: List[str]) -> dict:
        import datetime

        from . import __version__

        return {
            "schema": proj.PROJECT_SCHEMA,
            "name": name,
            "app": "CardboardCAD",
            "engine_version": __version__,
            "saved_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "units": "mm",
            "model": {"file": proj.MODEL_FILE, "name": self.model_name},
            "recipe": self.recipe,
            "workflow": {"name": self.workflows.active()["name"],
                         "apply_defaults": self.workflows.settings()["apply_defaults"]},
            "summary": self._summary(),
            "rhino_overrides": [o.to_dict() for o in self.overrides.values()],
            "files": files,
        }

    def open_project(self, path: str) -> dict:
        """Load a project folder and rebuild its design. On error the current design is kept."""
        if not isinstance(path, str):
            raise ProjectError("The project folder must be text")
        directory = proj.resolve_open_dir(path, self.home)
        m = proj.read_manifest(directory)
        name = m.get("name") if isinstance(m.get("name"), str) and m.get("name") else os.path.basename(directory)
        model_info = m.get("model") if isinstance(m.get("model"), dict) else {}
        rel = proj._safe_rel(model_info.get("file", proj.MODEL_FILE))
        if rel is None:
            raise ProjectError(f"{proj.MANIFEST} names a model file outside the project")
        try:
            mesh = parse_obj(proj._read_text(os.path.join(directory, rel), proj.MAX_MODEL_BYTES, rel))
        except MeshFileError as exc:
            raise ProjectError(f"{rel}: {exc}") from None
        model_name = model_info.get("name") if isinstance(model_info.get("name"), str) else "model"
        scratch = DesignSession(home=self.home)
        scratch._set_model(mesh, model_name)
        raw_overrides = m.get("rhino_overrides") or []
        if not isinstance(raw_overrides, list):
            raise ProjectError(f"{proj.MANIFEST}: rhino_overrides must be a list")
        try:
            scratch.overrides = {o.part: o for o in map(Override.from_dict, raw_overrides)}
        except PartsError as exc:
            raise ProjectError(f"{proj.MANIFEST}: {exc}") from None
        recipe = m.get("recipe") or {}
        if not isinstance(recipe, dict):
            raise ProjectError(f"{proj.MANIFEST}: recipe must be an object")
        method = recipe.get("method")
        steps = []
        if method is not None:
            if not isinstance(method, dict) or method.get("tool") not in METHOD_TOOLS:
                raise ProjectError(f"{proj.MANIFEST}: the recipe method must be one of {', '.join(sorted(METHOD_TOOLS))}")
            steps.append((method["tool"], method.get("input", {})))
            for key, tname in (("nest", "nest_sheets"), ("estimate", "estimate_cost")):
                if recipe.get(key) is not None:
                    steps.append((tname, recipe[key]))
        for tname, args in steps:
            try:
                checked = _check_args(_TOOLS_BY_NAME[tname], args)
                getattr(scratch, "_t_" + tname)(**checked)
                scratch._record(tname, checked)
            except (ToolInputError, *INPUT_ERRORS) as exc:
                raise ProjectError(f"{proj.MANIFEST}: the {tname} step could not run again: {exc}") from None
        chat = proj.read_chat(directory)
        with self._lock:
            for attr in ("model", "model_name", "method", "method_summary", "parts", "parts3d",
                         "board_thickness", "nested", "last_estimate", "recipe", "overrides", "generated"):
                setattr(self, attr, getattr(scratch, attr))
            self.notes, self.notices = [], []
            self.steps = []
            self._log_step("open_project", True, f"Opened project {name} from {directory}")
            self._bump()
            self.project = {"name": name, "folder": directory, "saved_version": self.version}
            return {"name": name, "folder": directory, "chat": chat, "version": self.version,
                    "summary": m.get("summary")}

    def push_model(self, mesh: Mesh, name: str) -> int:
        """A model sent from Rhino (Hops SendToCardboardCAD) replaces the current one."""
        with self._lock:
            self._set_model(mesh, name or "rhino model")
            self._log_step("send_from_rhino", True, "Received " + self._describe_model() + " from Rhino")
            self._bump()
            return self.version

    # ---- two-way Rhino (rhino_sync.py, assets/rhino_fold_theory.py) ----------
    def _note(self, text: str) -> None:
        self.notes.append(text)
        del self.notes[:-rhino_sync.MAX_NOTES]

    def _notice(self, text: str) -> None:
        self.notices.append(text)
        del self.notices[:-rhino_sync.MAX_NOTICES]

    def take_notes(self) -> List[str]:
        """Rhino edits the Design Assistant has not been told about yet (each is handed out once)."""
        with self._lock:
            out, self.notes = self.notes, []
            return out

    def _model_object(self, root: str) -> dict:
        return {"id": "model", "kind": "model", "layer": root + "::Model", "name": self.model_name,
                "mesh": {"vertices": [[round(c, 6) for c in v] for v in self.model.vertices],
                         "faces": [list(f) for f in self.model.faces]}}

    def _model_revision(self) -> Optional[str]:
        if self.model is None:
            return None
        return rhino_sync.revision({k: v for k, v in self._model_object("").items() if k != "layer"})

    def scene(self, since: Optional[int] = None) -> dict:
        """The design as tagged objects for the Rhino script. Unchanged since ``since``: just the version."""
        with self._lock:
            if since is not None and since == self.version:
                return {"version": self.version, "changed": False}
            root = rhino_sync.layer_root(self.project["name"] if self.project else None)
            objs: List[dict] = []
            if self.model is not None:
                objs.append(self._model_object(root))
            for pid, loops in self.parts3d.items():
                objs.append({"id": f"assembly:{pid}", "kind": "assembly", "part": pid, "layer": root + "::Assembly",
                             "loops": [[[round(c, 6) for c in p] for p in loop] for loop in loops]})
            if self.parts is not None:
                local = {p.id: p for p in self.parts.parts}
                x = 0.0

                def drawn(polys):
                    return [[[round(p[0] + x, 6) + 0.0, round(p[1], 6) + 0.0] for p in poly] for poly in polys]

                for sheet in self.parts.layout():
                    if sheet.size is not None:
                        w, h = sheet.size
                    else:
                        w = max((p.bbox()[1][0] for p in sheet.parts), default=0.0)
                        h = max((p.bbox()[1][1] for p in sheet.parts), default=0.0)
                    objs.append({"id": f"sheet:{sheet.index}", "kind": "sheet", "index": sheet.index,
                                 "layer": root + "::Sheets",
                                 "outline": [[x, 0.0], [x + w, 0.0], [x + w, h], [x, h]]})
                    for laid in sheet.parts:
                        own = local[laid.id]
                        lx, ly = laid.label_point()
                        ov = self.overrides.get(laid.id)
                        objs.append({
                            "id": f"part:{laid.id}", "kind": "part", "part": laid.id, "label": laid.label or laid.id,
                            "layer": root + "::Parts", "sheet": sheet.index,
                            "frame": rhino_sync.frame_for(own, laid, x, sheet.index),
                            "shape": rhino_sync.shape_of(own),
                            "cut": drawn(laid.cut), "score": drawn(laid.score), "etch": drawn(laid.etch),
                            "label_at": [round(lx + x, 6), round(ly, 6)],
                            "override": ov.status if ov is not None else None,
                        })
                    x += w + SHEET_GAP_MM
            for o in objs:
                o["revision"] = rhino_sync.revision({k: v for k, v in o.items() if k != "layer"})
                o["version"] = self.version
            return {
                "version": self.version, "changed": True, "units": "mm", "root": root,
                "project": self.project["name"] if self.project else None,
                "objects": objs,
                "overrides": [o.info() for o in self.overrides.values()],
                "notices": list(self.notices),
            }

    def rhino_model(self, mesh: Mesh, name: Optional[str] = None, base_revision: Optional[str] = None) -> dict:
        """The model was edited in Rhino: replace it, keep the hand edits, and run the recipe again."""
        with self._lock:
            conflict = (base_revision is not None and self.model is not None
                        and base_revision != self._model_revision())
            recipe = dict(self.recipe)
            self._set_model(mesh, (name or "").strip()[:80] or self.model_name or "rhino model", keep_overrides=True)
            ok, err = self._run_recipe(recipe)
            text = "Model edited in Rhino: now " + self._describe_model() + "."
            if recipe.get("method"):
                ran = [recipe["method"]["tool"]] + [t for k, t in (("nest", "nest_sheets"),
                                                                   ("estimate", "estimate_cost")) if recipe.get(k)]
                text += (" Ran " + ", ".join(ran) + " again.") if ok else " " + err
            if conflict:
                text += " It replaced a newer model in the app."
            self._note("In Rhino, the designer edited the model. " + text)
            self._notice(text)
            self._log_step("rhino_model_edit", ok, text)
            self._bump()
            return {"ok": ok, "version": self.version, "conflict": conflict, "message": text,
                    "revisions": {"model": self._model_revision()}}

    def rhino_part(self, pid, frame, cut, score=None, etch=None, base_shape=None) -> dict:
        """A flat part was edited in Rhino (sheet coordinates). It becomes a hand edit. Raises PartsError."""
        with self._lock:
            if self.parts is None:
                raise PartsError("There are no parts yet, so there is nothing to edit")
            if not isinstance(pid, str) or pid not in self.generated:
                raise PartsError(f"There is no part {pid!r} in the design")
            if base_shape is not None and not isinstance(base_shape, str):
                raise PartsError("base_shape must be text")
            edited = rhino_sync.part_from_edit(pid, rhino_sync.check_frame(frame), cut, score, etch)
            generated = self.generated[pid]
            current = next(p for p in self.parts.parts if p.id == pid)
            conflict = base_shape is not None and base_shape != rhino_sync.shape_of(current)
            ok, err = True, ""
            if rhino_sync.same_shape(edited, current) and not conflict:
                status, text = "unchanged", f"{pid} is unchanged."
                return {"ok": True, "status": status, "conflict": False, "message": text, "version": self.version}
            if rhino_sync.same_shape(edited, generated):
                self.overrides.pop(pid, None)
                ok, err = self._run_recipe(dict(self.recipe))
                status, text = "cleared", f"{pid} is back to the shape the method makes; its hand edit was removed."
                self._note(f"In Rhino, the designer put {pid} back to its generated shape (no hand edit now).")
            elif rhino_sync.same_shape_moved(edited, current):
                status = "moved"
                text = (f"{pid} was only moved. Nesting places the parts on the sheets, so the move is not kept; "
                        "reshape a part to make a hand edit, or ask the assistant to nest again.")
            else:
                summary = rhino_sync.describe_edit(generated, edited)
                self.overrides[pid] = Override(part=pid, cut=edited.cut, score=edited.score, etch=edited.etch,
                                               base_shape=rhino_sync.shape_of(generated), summary=summary)
                ok, err = self._run_recipe(dict(self.recipe))
                status = self.overrides[pid].status
                text = f"{pid} edited in Rhino ({summary}). It is now a hand edit (override)."
                self._note(f"In Rhino, the designer edited part {pid} ({summary}). It is now a hand edit "
                           "(override): keep it, and ask before changing this part.")
            if conflict:
                text += f" It replaced a newer {pid} made in the app."
            if not ok:
                text += " " + err
            if status != "moved":
                self._notice(text)
            self._log_step("rhino_part_edit", ok, text)
            self._bump()
            return {"ok": ok, "status": status, "conflict": conflict, "message": text, "version": self.version}

    # ---- live sync (Rhino) -------------------------------------------------
    def live(self, since: Optional[int] = None) -> dict:
        """The design as plain geometry for Grasshopper. Unchanged since ``since``: just the version."""
        with self._lock:
            if since is not None and since == self.version:
                return {"version": self.version, "changed": False}
            model = None
            if self.model is not None:
                model = {"name": self.model_name,
                         "vertices": [list(v) for v in self.model.vertices],
                         "faces": [list(f) for f in self.model.faces]}
            parts3d = [{"id": pid, "loops": [[list(p) for p in loop] for loop in loops]}
                       for pid, loops in self.parts3d.items()]
            sheets = []
            x = 0.0
            if self.parts is not None:
                for sheet in self.parts.layout():
                    if sheet.size is not None:
                        w, h = sheet.size
                    else:
                        w = max((p.bbox()[1][0] for p in sheet.parts), default=0.0)
                        h = max((p.bbox()[1][1] for p in sheet.parts), default=0.0)
                    sheets.append({
                        "index": sheet.index,
                        "origin": [x, 0],
                        "size": [w, h],
                        "parts": [{"id": p.id, "label": p.label or p.id,
                                   "cut": [[list(q) for q in poly] for poly in p.cut],
                                   "score": [[list(q) for q in poly] for poly in p.score],
                                   "etch": [[list(q) for q in poly] for poly in p.etch]}
                                  for p in sheet.parts],
                    })
                    x += w + SHEET_GAP_MM
            return {
                "version": self.version,
                "changed": True,
                "project": self.project["name"] if self.project else None,
                "summary": self._live_summary(),
                "model": model,
                "method": self.method,
                "thickness_mm": self.board_thickness,
                "parts3d": parts3d,
                "sheets": sheets,
                "parts": self.parts.to_json() if self.parts is not None else None,
            }

    def _live_summary(self) -> str:
        if self.model is None:
            return "No design in CardboardCAD yet. Make one in the app's Design Assistant."
        text = self._t_model_info()
        if self.last_estimate:
            text += f"\nEstimate: {self.last_estimate['time_min']:.1f} min, total {self.last_estimate['total_cost']:.2f}"
        if self.project:
            text = f"Project: {self.project['name']}\n" + text
        return text

    # ---- preview ---------------------------------------------------------
    def state(self) -> dict:
        with self._lock:
            if self._state_cache is None:
                self._state_cache = self._build_state()
            out = dict(self._state_cache)
            out["rhino"] = {"overrides": [o.info() for o in self.overrides.values()],
                            "notices": list(self.notices)}
            out["project"] = None if self.project is None else {
                "name": self.project["name"],
                "folder": self.project["folder"],
                "unsaved_changes": not self._is_saved(),
            }
            return out

    def _build_state(self) -> dict:
        model = None
        objects: List[dict] = []
        sheets: List[dict] = []
        if self.model is not None:
            (lx, ly, lz), (hx, hy, hz) = self.model.bbox()
            model = {
                "name": self.model_name,
                "faces": len(self.model.faces),
                "size_mm": [round(hx - lx, 2), round(hy - ly, 2), round(hz - lz, 2)],
                "bbox": [[lx, ly, lz], [hx, hy, hz]],
            }
            kind = "assembly" if self.method == "unfold" else "model"
            objects.append(self._mesh_object("model", kind, self.model_name, self.model))
            for pid, loops in self.parts3d.items():
                try:
                    tris = extrude(loops, self.board_thickness)
                except ValueError:
                    continue
                objects.append(_triangles_object(pid, tris))
        if self.parts is not None:
            util = self.nested["utilisation"] if self.nested else None
            for sheet in self.parts.layout():
                sheets.append({
                    "index": sheet.index,
                    "parts": len(sheet.parts),
                    "utilisation": util[sheet.index] if util and sheet.index < len(util) else None,
                    "svg": to_svg(sheet),
                })
        return {
            "version": self.version,
            "model": model,
            "method": self.method,
            "method_summary": self.method_summary,
            "nested": self.nested is not None if self.parts is not None else False,
            "sheet_size": [self.nested["sheet_width"], self.nested["sheet_height"]] if self.nested else None,
            "sheets": sheets,
            "estimate": self.last_estimate,
            "steps": list(self.steps),
            "scene": {"objects": objects},
        }

    def _mesh_object(self, oid: str, kind: str, label: str, mesh: Mesh) -> dict:
        obj = {"id": oid, "kind": kind, "label": label, "vertices": [], "indices": []}
        if sum(2 if len(f) == 4 else 1 for f in mesh.faces) > MAX_PREVIEW_TRIANGLES:
            obj["too_large"] = True
            return obj
        obj["vertices"] = [round(c, 3) for p in mesh.vertices for c in p]
        idx = obj["indices"]
        for f in mesh.faces:
            idx += (f[0], f[1], f[2])
            if len(f) == 4:
                idx += (f[0], f[2], f[3])
        return obj


def _triangles_object(pid: str, tris) -> dict:
    verts = [round(c, 3) for t in tris for p in t for c in p]
    return {"id": pid, "kind": "part", "label": pid, "vertices": verts, "indices": list(range(len(tris) * 3))}
