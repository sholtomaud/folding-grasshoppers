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
from typing import Any, Dict, List, Optional, Tuple

from . import brand
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
from .diagnostics import LEVELS as DIAG_LEVELS, SOURCES as DIAG_SOURCES, DiagnosticsLog, format_entries
from .formulas import FormulaError, FormulaStore, build_quote, needed_fields, quote_csv
from . import rhino_sync
from . import scene as sc
from . import brief as bf
from . import structure as st
from .rhino_sync import Override
from .project import ProjectError
from .settings import BUILTIN_DEFINITIONS, SettingsError, SettingsStore, definition_from, field_for_form
from .views import ACTIONS, BLOCK_TYPES, ViewError, ViewStore, static_catalog
from .workflow import WorkflowError, WorkflowStore

VALIDATION_ERRORS = (SettingsError, FormulaError, ViewError, WorkflowError)
INPUT_ERRORS = (
    GeometryError, MeshFileError, ShapeError, PartsError, WaffleError, ContourError,
    NestError, UnfoldError, ExportError, CutEstimateError, ProjectError, sc.SceneError, bf.BriefError, st.StructureError,
    *VALIDATION_ERRORS,
)
# Prices and rates are settings, never tool inputs (Claude never types the numbers).
SETTING_ONLY_INPUTS = {"estimate_cost": ("sheet_price", "machine_rate", "labour_rate", "margin_pct", "gst_pct"),
                       "check_structure": ("board_ect", "seat_safety_factor", "load_safety_factor", "safety_factor",
                                           "max_panel_ratio", "max_span_ratio")}
ESTIMATE_SETTINGS = ("cut_speed", "score_speed", "pierce_time", "travel_overhead", "sheet_price", "machine_rate")
MAX_FORMS = 20
MAX_FORM_FIELDS = 12
# Tools that read or change workflows (workflow.py), not the design.
WORKFLOW_TOOLS = {"list_workflows", "save_workflow", "use_workflow", "set_workflow_defaults"}
# Tools for settings, formulas, views and diagnostics: they do not change the design.
SETUP_TOOLS = {"request_inputs", "get_settings", "add_formula", "list_formulas", "list_views", "save_view",
               "read_diagnostics"}
# Tools that do not change the design: a saved project stays saved.
READ_ONLY_TOOLS = ({"model_info", "export_cut_files", "save_project", "list_objects", "select_object",
                    "check_brief", "check_structure"} | WORKFLOW_TOOLS | SETUP_TOOLS)
# The brief (brief.py): not workflow steps, and failing checks are not reported back on them.
BRIEF_TOOLS = {"set_brief", "check_brief", "check_structure"}
# Scene (#43): tools that take an optional object name, and the scene tools themselves.
OBJECT_TOOLS = ("create_shape", "load_model_file", "scale_model", "model_info", "waffle_slice", "contour_stack",
                "unfold", "clear_rhino_override", "check_structure")
MODEL_TOOLS = {"create_shape", "load_model_file", "scale_model"}
METHOD_TOOLS = {"waffle_slice", "contour_stack", "unfold"}
SHEET_GAP_MM = 100.0  # between sheets laid side by side for Rhino
MAX_PREVIEW_TRIANGLES = 400_000
MAX_STEPS = 200

SYSTEM_PROMPT = """You are the Design Assistant in Fold Theory, the Mac app of the Fold Theory studio, which builds furniture, displays and sculptures from flat cardboard sheets cut on a CNC or laser cutter.

You turn what the designer asks for into calls to the tools you have. The tools all act on one shared design:
1. A 3D model: make one with create_shape, or load the designer's own model with load_model_file (an .stl or .obj exported from Rhino). Use scale_model to set its real size.
2. Parts: turn the model into flat cardboard parts with one method:
   - waffle_slice: an interlocking grid of slotted ribs (X ribs and Y ribs). Light, fast to build, good for big organic forms, stools, lamps.
   - contour_stack: stacked layers glued on top of each other. Solid and heavy, very faithful to the form; uses a lot of board.
   - unfold: folds the model's flat faces into nets with glue tabs. Only for faceted (low-poly) models; it makes hollow shells.
   A new method replaces the previous parts.
3. Sheets: nest_sheets lays the parts onto as few sheets of board as it can.
   The brief (set_brief) holds what the client asked for: stand footprint and maximum height, venue limits, the items wanted, loads and seats, board and sheet size, pallet, assembly time, budget and branding. Fill it in from the conversation as soon as the designer gives these. The engine checks the design against it after every step (check_brief lists the checks); a tool result that says "Brief check failed" must be dealt with (change the design, or ask the designer to change the brief) before you call a concept done.
   For seats, counters and shelves made as waffles, check_structure gives an indicative strength check from the board data in settings: "about N x the design load", with warnings for wide unbraced rib panels and long top spans. Use it to choose rib count and spacing, repeat its numbers exactly, and say it is indicative only: the first build of a new seat design should be load-tested. If the board's ECT is not set, ask the designer for the supplier's figure; never guess it.
   A design can be a scene of several named objects (a counter, four stools, a back wall). Pass object="counter" to create_shape, load_model_file, scale_model, model_info or a method to work on that object (a new name adds it); add_object, copy_object (for identical stools), move_object, remove_object, select_object and list_objects manage them. Each object has its own model, position and method, and its parts get its prefix (C-X1, S1-X1). nest_sheets, estimate_cost, export_cut_files and save_project always cover the whole scene.
4. Then estimate_cost (machine time and money) and export_cut_files (DXF or SVG cut files, one per sheet).
The active workflow (below) says which of these steps to run, in what order, and with which saved values.
5. save_project saves everything (model, parts, cut files, estimate, a preview picture) as a project folder the designer can reopen. To present several concepts to a client, save each one under its own name, for example "Acme Expo - Concept A", then "Acme Expo - Concept B".

Everything is in millimetres. Z is up and models sit on the ground. Typical board: single-wall corrugated 3 mm, double-wall 6 mm, honeycomb board 10 to 20 mm. Common sheet sizes: 1200 x 800, 1000 x 700, 2400 x 1200 mm. When the designer gives no value, use the tool defaults and say which you used.

The app shows a live preview next to this chat: the 3D model (and the assembled ribs, layers or folded nets) and the flat cut sheets. It updates after every tool call, so describe results briefly instead of listing every part. Point the designer to the preview when it helps ("see the Sheets tab").

Keep replies short and plain. If a request is ambiguous in a way that changes the result (for example no size at all for a new object), ask one short question, or make a sensible choice and say so. If a tool returns an error, read it, fix the input and try again, or explain the problem. Only call export_cut_files or save_project when the designer asks for files or to save; they write into their Documents folder. When a question would block you (no board size) and the workflow does not answer it, ask it plainly and wait.

Numbers: you never calculate. Never do arithmetic yourself: no totals, sums, conversions, rough estimates or "about" figures. Every number that matters (sheets, machine time, costs, margin, GST, the quote total) is worked out by the engine from the settings and its formulas, and the app shows the quote in the Project tab. Repeat a number only exactly as a tool gave it, or point the designer to the Project tab; the app marks any amount or quantity in your reply that no tool gave as unverified.
Prices, rates, margin and GST are settings, never tool inputs. When a tool says a setting is missing, call request_inputs with those keys, then stop and wait: the designer fills in a form in the chat and their answer arrives as their next message. You are told which values were set, not the numbers. Never guess a price. get_settings shows what is set and where each value comes from (Fold Theory defaults, the workflow, this project).
When a quote needs a new kind of line (glue per joint, install crew hours, freight), add it with add_formula: an expression over settings and measurements, with a worked example the engine checks. Ask for any new value with request_inputs first (it can define a new field). Formulas use + - * / ^, min, max, round, ceil, floor, abs, if, sum and count, and units are checked.
The app's tabs are JSON views (list_views shows the block types and bindings). When the designer wants a new tab, table or form, build it with save_view; the engine rejects a view that uses an unknown block, binding or action and says why.
To find out why something went wrong (Grasshopper, Rhino, a form, a view, a tool), call read_diagnostics and answer from what it shows.

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
        "Work out cutting time, cost and the quote for the current parts. Cut length, score length, pierces "
        "and sheet count are measured from the design (nest first for a real sheet count). Prices, rates, "
        "margin and GST come from the settings, never from you: if one is missing this says so, and you ask "
        "with request_inputs. Machine speeds come from the settings too unless the designer gives one here.",
        {
            "cut_speed": _num("Cutting speed, mm/s, only if the designer gave one for this job"),
            "score_speed": _num("Scoring and etching speed, mm/s, only if the designer gave one"),
            "pierce_time": _num("Seconds per pierce, only if the designer gave one"),
            "travel_overhead": _num("Extra time for travel moves as a fraction, only if the designer gave one"),
        },
    ),
    _tool(
        "export_cut_files",
        "Write the cut files, one per sheet, with layers CUT, SCORE, ETCH and LABEL. Only when the designer asks.",
        {
            "format": {"type": "string", "enum": ["dxf", "svg"], "default": "dxf", "description": "File format"},
            "name": {"type": "string", "default": "cardboardcad", "description": "File name stem"},
            "folder": {"type": "string", "default": "",
                       "description": "Folder inside the home folder; empty means ~/Documents/Fold Theory"},
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
                                      "~/Documents/Fold Theory/Projects/<name>"},
        },
    ),
]

_FIELD_SCHEMA = {
    "type": "object",
    "properties": {
        "key": {"type": "string", "description": "Setting key, e.g. sheet_price; a new key defines a new setting"},
        "label": {"type": "string", "description": "Only for a new setting: its label, e.g. \"Glue per joint\""},
        "type": {"type": "string", "enum": ["number", "integer", "currency", "percent", "text", "boolean"],
                 "description": "Only for a new setting"},
        "unit": {"type": "string", "description": "Only for a new setting: e.g. AUD, AUD/h, mm, min"},
        "minimum": {"type": "number"},
        "maximum": {"type": "number"},
        "help": {"type": "string", "description": "One line shown under the field"},
    },
    "required": ["key"],
}

TOOLS += [
    _tool(
        "request_inputs",
        "Ask the designer for setting values with a small form in the chat, for example the sheet price and "
        "machine rate. The form has typed fields with units and checks, and the designer chooses this project "
        "or all projects. After calling it, stop and wait: their answer arrives as their next message, and "
        "you are told which settings were set (never the numbers).",
        {
            "question": {"type": "string", "default": "", "description": "The question at the top of the form"},
            "fields": {"type": "array", "items": _FIELD_SCHEMA, "description": "The settings to ask for"},
            "scope": {"type": "string", "enum": ["project", "all"], "default": "project",
                      "description": "Which choice the form starts on"},
        },
        required=["fields"],
    ),
    _tool(
        "get_settings",
        "List the settings (prices, rates, margin, GST, machine speeds, company and job details), each with its "
        "value and the layer it comes from: Fold Theory defaults, the workflow, this project.",
        {"group": {"type": "string", "default": "", "description": "Only this group, e.g. Costs, Machine, Job"}},
    ),
    _tool(
        "add_formula",
        "Add or change a formula (a quote line or other worked-out number) over settings, measurements and "
        "other formulas, e.g. install_hours = ceil(parts / 40). Give a worked example: input values and the "
        "expected result, which the engine checks before saving. Measurements: sheets, parts, cut_length, "
        "score_length, pierces. Only after the designer agrees.",
        {
            "key": {"type": "string", "description": "Lower-case key, e.g. install_hours"},
            "label": {"type": "string", "description": "What the quote line says, e.g. \"Install crew hours\""},
            "expr": {"type": "string", "description": "The expression, e.g. ceil(parts / 40)"},
            "unit": {"type": "string", "description": "Unit of the result, e.g. AUD, h, min, mm2"},
            "example": {"type": "object",
                        "description": "{\"inputs\": {name: number in that field's unit}, \"expected\": number}"},
            "role": {"type": "string", "enum": ["line", "total", "subtotal", "working"], "default": "line",
                     "description": "line: a quote line; working: an intermediate number"},
            "description": {"type": "string", "default": ""},
            "scope": {"type": "string", "enum": ["project", "all"], "default": "project"},
        },
        required=["key", "label", "expr", "unit", "example"],
    ),
    _tool(
        "list_formulas",
        "List the formulas the quote is made from, with their expressions, units and versions.",
        {},
    ),
    _tool(
        "list_views",
        "List the app's JSON views (tabs), the block types a view can use, the data a block can bind to, and "
        "the registered actions buttons can run.",
        {},
    ),
    _tool(
        "save_view",
        "Save a JSON view (a tab in the app). It is checked first: unknown block types, keys, bindings or "
        "actions are rejected with the reason. Saving replaces a view with the same id, and the designer can "
        "undo it in one click. Only when the designer asks for a new or changed screen.",
        {
            "view": {"type": "object",
                     "description": "{\"schema\": \"cardboardcad.view/1\", \"id\": \"costs\", \"title\": \"Costs\", "
                                    "\"blocks\": [...]} (see list_views)"},
            "scope": {"type": "string", "enum": ["project", "all"], "default": "all",
                      "description": "all: every project; project: only this saved project"},
        },
        required=["view"],
    ),
    _tool(
        "read_diagnostics",
        "Read the diagnostics log: what Grasshopper (LiveDesign, Hops), the Rhino plugin, the engine's tools and "
        "the checks on forms, views and formulas reported. Use it to find out why something failed.",
        {
            "source": {"type": "string", "enum": list(DIAG_SOURCES), "description": "Only this source"},
            "level": {"type": "string", "enum": list(DIAG_LEVELS), "default": "info",
                      "description": "This level and more severe"},
            "limit": _num("How many of the newest entries", 30, 1, 200, True),
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
_OBJ = {"type": "string", "description": "Scene object to act on, e.g. \"counter\" or \"stool 1\". "
        "Omit for the active object. With create_shape or load_model_file a new name adds the object."}
_POS = {"x_mm": _num("Position along X in mm", 0), "y_mm": _num("Position along Y in mm", 0),
        "z_mm": _num("Position along Z in mm", 0)}
TOOLS += [
    _tool(
        "add_object",
        "Add a named object to the scene (a counter, a stool, a back wall) and make it the active one. It starts "
        "with no model: then create_shape or load_model_file. If the design already has a model, that becomes the "
        "first object. Part labels get the object's prefix, e.g. C-X1.",
        {"name": {"type": "string", "description": "Object name, e.g. \"counter\""},
         "prefix": {"type": "string", "default": "", "description": "Label prefix, 1-6 letters/digits (default "
                                                                   "from the name: counter -> C, stool 1 -> S1)"},
         **_POS},
        required=["name"],
    ),
    _tool(
        "copy_object",
        "Copy an object with its model and method (for example stool 1 to stool 2) and place the copy. The copy "
        "gets its own prefix, so its parts are cut separately.",
        {"name": {"type": "string", "description": "The object to copy"},
         "new_name": {"type": "string", "description": "Name of the copy"},
         "prefix": {"type": "string", "default": "", "description": "Label prefix of the copy (default from its name)"},
         **{k: dict(v, default=None) for k, v in _POS.items()}},
        required=["name", "new_name"],
    ),
    _tool(
        "move_object",
        "Place an object in the scene (its position, in mm). Moving does not change its parts.",
        {"name": {"type": "string", "description": "The object"}, **_POS},
        required=["name"],
    ),
    _tool(
        "remove_object",
        "Remove an object and its parts from the scene. Nesting and the estimate are worked out again without it.",
        {"name": {"type": "string", "description": "The object"}},
        required=["name"],
    ),
    _tool(
        "select_object",
        "Make an object the active one, so tools without an object name act on it.",
        {"name": {"type": "string", "description": "The object"}},
        required=["name"],
    ),
    _tool("list_objects", "List the scene's objects: name, prefix, position, method and part count.", {}),
]
TOOLS += [
    _tool(
        "set_brief",
        "Fill in or change the design brief: what the client asked for. Only the fields given change; null "
        "clears one. Fields: " + ", ".join(f"{k} ({f['label']}{', ' + f['unit'] if f['unit'] else ''})"
                                            for k, f in bf.FIELDS.items())
        + ". items is a list of {name, quantity, notes}. The engine checks the design against the brief.",
        {"values": {"type": "object", "description": "{field: value}"}},
        required=["values"],
    ),
    _tool("check_brief", "Check the design against the brief: height, footprint, budget, pallet and sheet size, "
                         "each pass, warn or fail with the engine's numbers.", {}),
    _tool(
        "check_structure",
        "Indicative structural check of a waffle object (not an engineering sign-off): the vertical capacity of "
        "its ribs from the board's edge crush strength (ECT setting, from the supplier) and the rib edge on the "
        "floor, divided by a safety factor (settings: 3 for seats), as 'about N x the design load'; with warnings "
        "for wide unbraced rib panels and for counter or shelf tops that span too far. Use it to choose rib "
        "count and spacing for a load. The load defaults to the brief (load_per_seat_kg or shelf_load_kg).",
        {"use": {"type": "string", "enum": list(st.USES), "description": "What the object is for"},
         "load_kg": {"type": "number", "minimum": 0.001, "description": "Design load in kg (default from the "
                                                                        "brief)"}},
        required=["use"],
    ),
]
for _t in TOOLS:
    if _t["name"] in OBJECT_TOOLS:
        _t["input_schema"]["properties"]["object"] = dict(_OBJ)
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
    # request_inputs: the form the app shows in the chat.
    form: Optional[dict] = None


def _mm(v: float) -> str:
    return f"{v:.0f}" if abs(v - round(v)) < 0.05 else f"{v:.1f}"


def _length(poly, closed: bool) -> float:
    pts = list(poly)
    if closed and pts and tuple(pts[0]) != tuple(pts[-1]):
        pts.append(pts[0])
    return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


class DesignSession:
    """The one design the engine holds. Thread-safe: preview polls run beside tool calls."""

    def __init__(self, home: str, diagnostics: Optional[DiagnosticsLog] = None):
        self.home = home
        self._lock = threading.RLock()
        self.version = 0
        self._state_cache: Optional[dict] = None
        self.project: Optional[dict] = None  # {name, folder, saved_version}
        self.diagnostics = diagnostics if diagnostics is not None else DiagnosticsLog(None)
        self.workflows = WorkflowStore(home, _TOOLS_BY_NAME, _check_args, WORKFLOW_TOOLS,
                                       check_setting=lambda k, v: self.settings.check(k, v)[1])
        self.settings = SettingsStore(home, lambda: self.workflows.active().get("settings", {}),
                                      lambda: self.project["folder"] if self.project else None)
        self.settings.on_change = self._settings_changed
        self.formulas = FormulaStore(home, lambda: self.project["folder"] if self.project else None,
                                     self.field_units)
        self.views = ViewStore(home, lambda: self.project["folder"] if self.project else None,
                               self.view_catalog, self.binding_data, self._view_rejected)
        self.forms: Dict[str, dict] = {}
        self._form: Optional[dict] = None
        self.last_quote: Optional[dict] = None
        # Rhino hand edits of flat parts, by part id. Kept through re-runs (see rhino_sync.py).
        self.overrides: Dict[str, Override] = {}
        self.notes: List[str] = []    # Rhino edits not yet told to the Design Assistant
        self.notices: List[str] = []  # recent things the app should show (conflicts, stale edits)
        # Scene (scene.py): every object's name, prefix, position and method. The active object's state
        # is in the attributes above ("state": None); the others keep theirs in their entry.
        self.scene_objects: List[dict] = []
        self.active: Optional[str] = None
        self.brief: Dict[str, Any] = {}  # brief.py: what the client asked for
        self.last_structure: Optional[dict] = None  # structure.py: the latest check_structure result
        self.scene_doc: Optional[PartsDoc] = None  # the scene's parts once nested
        self._clear_all()
        self.steps: List[dict] = []
        # Where this Mac keeps per-Mac settings (projects location): the app's ENGINE_DATA_DIR.
        # self.project also keeps "files", a fingerprint of what was saved or opened.
        self.data_dir: Optional[str] = None

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
        self.last_quote = None
        self.scene_doc = None
        self.recipe = {}

    def _bump(self) -> None:
        self.version += 1
        self._state_cache = None

    def reset(self) -> None:
        with self._lock:
            self._clear_all()
            self.overrides, self.notes, self.notices = {}, [], []
            self.scene_objects, self.active = [], None
            self.brief = {}
            self.last_structure = None
            self.steps = []
            self.project = None
            self.settings.clear_project()
            self.formulas.clear_project()
            self.forms = {}
            self._bump()

    # ---- tools -----------------------------------------------------------
    def run_tool(self, name: str, args) -> ToolResult:
        tool = _TOOLS_BY_NAME.get(name)
        if tool is None:
            return ToolResult(f"There is no tool called {name!r}. Tools: {', '.join(_TOOLS_BY_NAME)}", True)
        with self._lock:
            was_saved = self._is_saved()
            self._form = None
            try:
                used, ask = {}, []
                priced = [k for k in SETTING_ONLY_INPUTS.get(name, ()) if isinstance(args, dict) and k in args]
                if priced:
                    raise ToolInputError(
                        f"{', '.join(priced)} cannot be given to {name}: prices and rates are settings, and "
                        "the engine reads them from the project settings. If one is missing, ask the designer "
                        "with request_inputs and wait for their answer.")
                if name in OBJECT_TOOLS and isinstance(args, dict) and args.get("object") is not None:
                    args = dict(args)
                    self._use_object(args.pop("object"), create=name in MODEL_TOOLS)
                if name not in WORKFLOW_TOOLS and name not in SETUP_TOOLS and name not in BRIEF_TOOLS:
                    args, used, ask = self.workflows.fill(name, args)
                if name in OBJECT_TOOLS and isinstance(args, dict):
                    args = {k: v for k, v in args.items() if k != "object"}
                checked = _check_args(tool, args)
                text = getattr(self, "_t_" + name)(**checked)
                if self.brief and name not in READ_ONLY_TOOLS and name not in BRIEF_TOOLS:
                    failed = bf.failures_text(self.brief_checks())
                    if failed:
                        text += "\n" + failed
                if name not in WORKFLOW_TOOLS and name not in SETUP_TOOLS and name not in BRIEF_TOOLS:
                    extra = " ".join(x for x in (self.workflows.notes(name, used, ask),
                                                 self.workflows.next_step(name)) if x)
                    if extra:
                        text += "\n" + extra
                result = ToolResult(text, setup_changed=name in WORKFLOW_TOOLS - {"list_workflows"},
                                    form=self._form)
                self._record(name, checked)
            except (ToolInputError, *INPUT_ERRORS) as exc:
                result = ToolResult(str(exc), True)
                self._diagnose(name, exc)
            self._log_step(name, not result.is_error, result.text)
            self._bump()
            if self.project is not None and (was_saved or name == "save_project") and (
                    result.is_error or name in READ_ONLY_TOOLS):
                self.project["saved_version"] = self.version
            return result

    def system_prompt(self) -> str:
        """The system prompt for Claude: the fixed part and the active workflow."""
        return SYSTEM_PROMPT + "\n\n" + self.workflows.prompt_section()

    def _diagnose(self, tool_name: str, exc: Exception) -> None:
        source = "validation" if isinstance(exc, VALIDATION_ERRORS) else "engine"
        level = "warning" if isinstance(exc, SettingsError) and "missing" in str(exc).lower() else "error"
        try:
            self.diagnostics.write(source, level, f"{tool_name} failed: {exc}", design_version=self.version,
                                   details={"tool": tool_name})
        except (OSError, ValueError):
            pass

    def _view_rejected(self, view_id: str, reason: str) -> None:
        try:
            self.diagnostics.write("validation", "error", f"View {view_id} rejected: {reason}",
                                   design_version=self.version, details={"view": view_id})
        except (OSError, ValueError):
            pass

    # ---- settings, forms and the quote ---------------------------------------
    def field_units(self) -> Dict[str, str]:
        """Every name a formula can use (settings and measurements) and its unit."""
        units = {"sheets": "sheet", "parts": "count", "cut_length": "mm", "score_length": "mm",
                 "pierces": "count", "sheet_width": "mm", "sheet_height": "mm", "parts.cut_length": "mm",
                 "print_area": "m2"}
        for key, d in self.settings.definitions().items():
            if d.type in ("number", "integer", "currency", "percent"):
                units[key] = "%" if d.type == "percent" else d.unit
        return units

    def _measure(self, doc: PartsDoc) -> Dict[str, Any]:
        per_part = [sum(_length(loop, True) for loop in p.cut) for p in doc.parts]
        score = sum(_length(line, False) for p in doc.parts for line in (*p.score, *p.etch))
        pierces = sum(len(p.cut) + len(p.score) + len(p.etch) for p in doc.parts)
        sheets = self.nested["sheet_count"] if self.nested else 1
        out = {"sheets": sheets, "parts": len(doc.parts), "cut_length": sum(per_part), "score_length": score,
               "pierces": pierces, "parts.cut_length": per_part, "print_area": 0.0}  # print areas: Phase 3
        if self.nested:
            out["sheet_width"] = self.nested["sheet_width"]
            out["sheet_height"] = self.nested["sheet_height"]
        return out

    def _missing_settings_error(self, keys) -> SettingsError:
        defs = [self.settings.definition(k) for k in keys]
        names = ", ".join(f"{d.key} ({d.label}, {d.unit})" if d.unit else f"{d.key} ({d.label})" for d in defs)
        fields = ", ".join(f'{{"key": "{d.key}"}}' for d in defs)
        return SettingsError(
            f"Missing settings: {names}. Do not guess them: ask the designer with "
            f"request_inputs(fields=[{fields}]) and wait for their answer, then run this again.")

    def _quote(self, measured: Dict[str, Any], overrides: Dict[str, Any]) -> dict:
        formulas = self.formulas.all()
        units = self.field_units()
        need = needed_fields(formulas)
        fields: Dict[str, dict] = {}
        used_settings: Dict[str, dict] = {}
        missing = []
        for name in need:
            if name in measured:
                fields[name] = {"value": measured[name], "unit": units.get(name, "")}
            elif name in units:
                if overrides.get(name) is not None:
                    fields[name] = {"value": overrides[name], "unit": units[name]}
                    used_settings[name] = {"value": overrides[name], "layer": "this estimate", "set_by": "",
                                           "set_at": ""}
                    continue
                r = self.settings.resolve(name)
                if r["value"] is None:
                    missing.append(name)
                    continue
                fields[name] = {"value": r["value"], "unit": units[name]}
                used_settings[name] = {k: r[k] for k in ("value", "layer", "set_by", "set_at")}
        currency = self.settings.resolve("currency")["value"] or "AUD"
        missing, unused = self._needed_missing(formulas, fields, missing, units, currency)
        if missing:
            raise self._missing_settings_error(missing)
        for name in unused:
            fields[name] = {"value": 0, "unit": units[name]}
        q = build_quote(formulas, fields, currency)
        q["settings"] = used_settings
        q["unused_settings"] = unused
        q["subtotal"] = next((l["value"] for l in q["lines"] if l["key"] == "subtotal"), None)
        import datetime as _dt
        q["made_at"] = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        q["design_version"] = self.version
        return q

    @staticmethod
    def _needed_missing(formulas, fields, missing, units, currency):
        """(needed, unused) of the missing settings. A rate is only needed when it changes a line:
        with no design hours, the design rate does not matter and is not asked for."""
        if not missing:
            return [], []

        def run(values):
            trial = dict(fields, **{k: {"value": v, "unit": units[k]} for k, v in values.items()})
            return [l["value"] for l in build_quote(formulas, trial, currency)["lines"]]

        zero = {k: 0 for k in missing}
        try:
            base = run(zero)
        except FormulaError:
            return list(missing), []
        needed = []
        for k in missing:
            try:
                if run(dict(zero, **{k: 1})) != base:
                    needed.append(k)
            except FormulaError:
                needed.append(k)
        return needed, [k for k in missing if k not in needed]

    def _settings_changed(self) -> None:
        """A setting changed: work the estimate and quote out again, so nothing shown is stale."""
        with self._lock:
            if self.last_estimate is not None and self._sheet_doc() is not None:
                try:
                    self._t_estimate_cost(**(self.recipe.get("estimate") or {}))
                except (ToolInputError, *INPUT_ERRORS):
                    self.last_estimate = None
                    self.last_quote = None
            self._bump()

    def set_setting(self, key: str, value, scope: str = "project", set_by: str = "") -> dict:
        with self._lock:
            try:
                return self.settings.set(key, value, scope=scope, set_by=set_by)
            except SettingsError as exc:
                self.diagnostics.write("validation", "error", f"Setting {key} rejected: {exc}",
                                       design_version=self.version, details={"key": str(key)[:60]})
                raise

    def submit_form(self, form_id: str, values, scope: str = "project", set_by: str = "") -> dict:
        """The designer's answer to a request_inputs form. All or nothing; Claude never sees the numbers."""
        with self._lock:
            form = self.forms.get(form_id) if isinstance(form_id, str) else None
            if form is None:
                return {"ok": False, "errors": {"_form": "This form is no longer open; ask again."},
                        "message": "That form is no longer open."}
            if not isinstance(values, dict):
                return {"ok": False, "errors": {"_form": "values must be an object"}, "message": "Bad form answer"}
            keys = [f["key"] for f in form["fields"]]
            raw = {k: values.get(k) for k in keys}
            try:
                self.settings.set_many(raw, scope=scope, set_by=set_by, definitions=form["definitions"])
            except SettingsError as exc:
                errors = getattr(exc, "errors", None) or {"_form": str(exc)}
                self.diagnostics.write("validation", "warning",
                                       "Form answer rejected: " + "; ".join(f"{k}: {v}" for k, v in errors.items()),
                                       design_version=self.version, details={"form": form_id})
                return {"ok": False, "errors": errors, "message": str(exc)}
            del self.forms[form_id]
            where = "for all projects (Fold Theory defaults)" if scope == "all" else "for this project"
            return {"ok": True, "errors": {}, "keys": keys,
                    "message": f"I filled in the form: {', '.join(keys)} set {where}. Carry on with what you were "
                               "doing (run the step that needed them again)."}

    def _t_request_inputs(self, fields, question="", scope="project"):
        if not fields:
            raise ToolInputError("Give at least one field to ask for")
        if len(fields) > MAX_FORM_FIELDS:
            raise ToolInputError(f"Ask for at most {MAX_FORM_FIELDS} values in one form")
        defs = self.settings.definitions()
        out_fields, new_defs = [], {}
        for raw in fields:
            key = raw.get("key")
            if key in defs:
                d = defs[key]
            else:
                d = definition_from(raw)
                new_defs[d.key] = d.to_dict()
            if any(f["key"] == d.key for f in out_fields):
                continue
            f = field_for_form(d, self.settings.resolve(d.key) if d.key in defs else {})
            if isinstance(raw.get("help"), str) and raw["help"].strip():
                f["help"] = raw["help"].strip()[:300]
            out_fields.append(f)
        import uuid

        form = {"id": uuid.uuid4().hex[:12], "question": (question or "").strip()[:500], "fields": out_fields,
                "scopes": ["project", "all"], "scope": scope if scope in ("project", "all") else "project"}
        self.forms[form["id"]] = {"fields": out_fields, "definitions": new_defs}
        while len(self.forms) > MAX_FORMS:
            del self.forms[next(iter(self.forms))]
        self._form = form
        labels = ", ".join(f"{f['label']} ({f['unit']})" if f["unit"] else f["label"] for f in out_fields)
        return (f"A form asking for {labels} is shown in the chat. Stop here and wait for the designer to fill "
                "it in: their answer arrives as their next message. Do not guess or suggest values.")

    def _t_get_settings(self, group=""):
        rows = self.settings.listing()["settings"]
        if group and group.strip():
            rows = [r for r in rows if r["group"].lower() == group.strip().lower()]
        lines = []
        for r in rows:
            unit = f" {r['unit']}" if r["unit"] else ""
            if r["value"] is None or r["value"] == "":
                lines.append(f"- {r['key']} ({r['label']}): not set" + ("; ask with request_inputs"
                                                                        if r["type"] != "text" else ""))
                continue
            who = f", {r['set_by']}" if r["set_by"] else ""
            lines.append(f"- {r['key']} ({r['label']}): {r['value']}{unit} [{r['layer']}{who}]")
        return "Settings (most specific layer wins: default < workflow < project < concept):\n" + "\n".join(lines)

    def _t_add_formula(self, key, label, expr, unit, example, role="line", description="", scope="project"):
        f = self.formulas.save_formula({"key": key, "label": label, "expr": expr, "unit": unit, "example": example,
                                        "role": role, "description": description}, scope=scope, set_by="Claude")
        where = "for all projects" if scope == "all" else "for this project"
        text = (f"Saved formula {f['key']} = {f['expr']} ({f['unit'] or 'plain number'}), version {f['version']}, "
                f"{where}. Its worked example checks out.")
        if self.last_estimate is not None:
            self._settings_changed()
            text += " The quote was worked out again; see the Project tab."
        return text

    def _t_list_formulas(self):
        lines = [f"- {f['key']} ({f['label']}) = {f['expr']} [{f['unit'] or 'number'}] {f['role']}, "
                 f"v{f['version']}, {f['source']}" for f in self.formulas.all()]
        return "Formulas, in quote order:\n" + "\n".join(lines)

    def _t_list_views(self):
        views = ", ".join(f"{v['id']} ({v['title']}, {v['source']})" for v in self.views.listing())
        blocks = ", ".join(BLOCK_TYPES)
        cat = self.view_catalog()
        binds = "; ".join(f"{k}" + (f" [{', '.join(v['columns'])}]" if v["kind"] == "collection" else "")
                          for k, v in sorted(cat.items()) if not k.startswith("settings."))
        return (f"Views: {views}.\nBlock types: {blocks}. Sections and tabs hold blocks; field, total and image "
                f"bind one value; table, settings and checklist bind a list; button runs an action.\n"
                f"Bindings: {binds}; settings.<key> for any setting (editable in a field).\n"
                f"Actions: {', '.join(ACTIONS)}.\nThe project view is a good example: get it from the app's "
                "Project tab, or build on {\"schema\": \"cardboardcad.view/1\", \"id\", \"title\", \"blocks\"}.")

    def _t_save_view(self, view, scope="all"):
        out = self.views.save(view, scope=scope, set_by="Claude")
        return (f"Saved view {out['id']} ({out['title']}). The app shows it as a tab; the designer can undo it "
                "in one click.")

    def _t_read_diagnostics(self, source=None, level="info", limit=30):
        return format_entries(self.diagnostics.read(source=source, level=level, limit=limit))

    # ---- what views can show ---------------------------------------------------
    def view_catalog(self) -> Dict[str, dict]:
        return static_catalog(self.settings.definitions().keys())

    def binding_data(self, binding: str):
        with self._lock:
            est = self.last_estimate or {}
            q = self.last_quote or {}
            if binding.startswith("settings."):
                r = self.settings.resolve(binding.split(".", 1)[1])
                return {k: r[k] for k in ("key", "label", "value", "unit", "layer", "set_by", "set_at", "type")}
            if binding == "settings":
                return self.settings.listing()["settings"]
            if binding == "formulas":
                return [{k: f.get(k) for k in ("key", "label", "expr", "unit", "version", "source", "role")}
                        for f in self.formulas.all()]
            if binding == "quote.lines":
                return [l for l in q.get("lines", []) if l.get("role") != "working"]
            if binding.startswith("quote."):
                return q.get(binding.split(".", 1)[1])
            if binding.startswith("estimate."):
                return est.get(binding.split(".", 1)[1])
            if binding == "project.name":
                return self.project["name"] if self.project else None
            if binding == "project.folder":
                return self.project["folder"] if self.project else None
            if binding == "project.status":
                if self.project is None:
                    return "Not saved yet"
                return "Saved" if self._is_saved() else "Unsaved changes"
            if binding.startswith("design."):
                what = binding.split(".", 1)[1]
                if self.model is None:
                    return None
                if what == "size":
                    (lx, ly, lz), (hx, hy, hz) = self.model.bbox()
                    return f"{_mm(hx - lx)} x {_mm(hy - ly)} x {_mm(hz - lz)} mm"
                if what == "method":
                    return self.method_summary or "No parts yet"
                if what == "parts":
                    doc = self._sheet_doc()
                    return len(doc.parts) if doc is not None else 0
                if what == "sheets":
                    return self.nested["sheet_count"] if self.nested else None
                return self.version
            if binding.startswith("workflow."):
                wf = self.workflows.active()
                done = {s["tool"] for s in self.steps if s["ok"]}
                steps = [{"title": s["title"], "tools": ", ".join(s["tools"]), "optional": s["optional"],
                          "done": any(t in done for t in s["tools"])} for s in wf["steps"]]
                if binding == "workflow.name":
                    return wf["name"]
                if binding == "workflow.status":
                    nxt = next((s["title"] for s in steps if not s["done"] and not s["optional"]), None)
                    return nxt or "All required steps done"
                return steps
            if binding == "parts":
                doc = self._sheet_doc()
                return [{"id": p.id, "label": p.label or p.id} for p in (doc.parts if doc else [])]
            if binding == "sheets":
                util = (self.nested or {}).get("utilisation") or []
                return [{"index": i + 1, "parts": n, "utilisation": util[i] if i < len(util) else None}
                        for i, n in enumerate(len(s.parts) for s in self._sheet_doc().layout())] \
                    if self._sheet_doc() else []
            if binding == "brief.checks":
                return self.brief_checks()
            if binding == "brief.items":
                return list(self.brief.get("items") or [])
            if binding.startswith("brief."):
                return bf.binding_value(self.brief, binding.split(".", 1)[1],
                                        self.brief_facts().get("currency", ""))
            if binding == "diagnostics":
                return [{k: e.get(k) for k in ("time", "source", "level", "message")}
                        for e in self.diagnostics.read(limit=50)]
            if binding == "preview.model":
                return proj.model_svg(self.model.triangles()) if self.model is not None else None
            return None

    def numbers(self) -> List[str]:
        """Numbers the engine has shown (estimate, quote, settings), as text: what Claude may repeat."""
        out: List[str] = []

        def add(v):
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                return
            for t in (f"{v:.2f}", f"{v:.1f}", f"{v:g}", str(int(round(v)))):
                if t not in out:
                    out.append(t)

        with self._lock:
            for v in (self.last_estimate or {}).values():
                add(v)
            for line in (self.last_quote or {}).get("lines", []):
                add(line["value"])
            for r in self.settings.listing()["settings"]:
                add(r["value"])
        return out

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
            self.recipe["estimate"] = {k: v for k, v in checked.items() if v is not None}

    def _is_saved(self) -> bool:
        return self.project is not None and self.project["saved_version"] == self.version

    def _need_model(self) -> Mesh:
        if self.model is None:
            raise ToolInputError("There is no model yet. Create one with create_shape or load one with load_model_file.")
        return self.model

    def _need_parts(self) -> PartsDoc:
        """The parts to nest, cost and cut: the whole scene's, or the one model's."""
        doc = self._sheet_doc()
        if doc is None:
            if self.scene_objects:
                raise ToolInputError("No object in the scene has parts yet. Run waffle_slice, contour_stack or "
                                     "unfold on an object first.")
            raise ToolInputError("There are no parts yet. Run waffle_slice, contour_stack or unfold first.")
        return doc

    # ---- scene (scene.py) -------------------------------------------------
    def _sheet_doc(self) -> Optional[PartsDoc]:
        """The parts on the sheets: the nested scene, the scene's parts put together, or the one model's parts."""
        if not self.scene_objects:
            return self.parts
        if self.scene_doc is not None:
            return self.scene_doc
        return sc.combine((e["prefix"], self._object_state(e)["parts"]) for e in self.scene_objects)

    def _entry(self, name, required: bool = True) -> Optional[dict]:
        key = sc.clean_name(name).lower()
        for e in self.scene_objects:
            if e["name"].lower() == key:
                return e
        if required:
            have = ", ".join(e["name"] for e in self.scene_objects) or "none yet"
            raise sc.SceneError(f"Unknown object {name!r}. Objects: {have}")
        return None

    def _object_state(self, e: dict) -> dict:
        if e["name"] == self.active:
            return {a: getattr(self, a) for a in sc.OBJECT_ATTRS}
        return e["state"]

    def _object_method(self, e: dict):
        return self.recipe.get("method") if e["name"] == self.active else e.get("method")

    def _activate(self, name: str) -> None:
        """Make ``name`` the active object: its state moves into the session's attributes."""
        if name == self.active:
            return
        if self.active is not None:
            cur = self._entry(self.active)
            cur["state"] = self._object_state(cur)
            cur["method"] = self.recipe.get("method")
        target = self._entry(name)
        for a, v in (target["state"] or sc.blank_state()).items():
            setattr(self, a, v)
        target["state"] = None
        self.recipe = {"method": target.get("method"),
                       **{k: v for k, v in self.recipe.items() if k in ("nest", "estimate")}}
        self.active = target["name"]

    def _scene_changed(self) -> Optional[dict]:
        """The scene's parts changed: the nest and estimate no longer hold. Returns the steps to run again."""
        again = {k: self.recipe.pop(k) for k in ("nest", "estimate") if k in self.recipe}
        self.scene_doc, self.nested, self.last_estimate, self.last_quote = None, None, None, None
        return again

    def _rerun_scene(self, again: dict) -> str:
        for key, tname in (("nest", "nest_sheets"), ("estimate", "estimate_cost")):
            if key not in again or self._sheet_doc() is None:
                continue
            try:
                checked = _check_args(_TOOLS_BY_NAME[tname], again[key])
                getattr(self, "_t_" + tname)(**checked)
                self._record(tname, checked)
            except (ToolInputError, *INPUT_ERRORS) as exc:
                return f" The {tname} step could not run again: {exc}"
        ran = [t for k, t in (("nest", "nest_sheets"), ("estimate", "estimate_cost")) if k in again]
        return (" Ran " + " and ".join(ran) + " again for the whole scene.") if ran and self._sheet_doc() else ""

    def _new_entry(self, name: str, prefix: str = "", position=(0.0, 0.0, 0.0), state=None, method=None) -> dict:
        name = sc.clean_name(name)
        if self._entry(name, required=False) is not None:
            raise sc.SceneError(f"There is already an object called {name!r}")
        if len(self.scene_objects) >= sc.MAX_OBJECTS:
            raise sc.SceneError(f"A scene can hold at most {sc.MAX_OBJECTS} objects")
        taken = [e["prefix"] for e in self.scene_objects]
        prefix = sc.check_prefix(prefix, taken) if (prefix or "").strip() else sc.make_prefix(name, taken)
        e = {"name": name, "prefix": prefix, "position": [float(c) for c in position],
             "state": state if state is not None else sc.blank_state(), "method": method}
        return e

    def _start_scene(self) -> Optional[str]:
        """The first object: the design so far, if there is one. Returns its name."""
        if self.scene_objects or self.model is None:
            return None
        e = self._new_entry(self.model_name or "object 1")
        e["state"], e["method"] = None, self.recipe.get("method")
        self.scene_objects.append(e)
        self.active = e["name"]
        self._scene_changed()
        return e["name"]

    def _add_object(self, name, prefix="", position=(0.0, 0.0, 0.0)) -> dict:
        self._start_scene()
        e = self._new_entry(name, prefix, position)
        self.scene_objects.append(e)
        self._activate(e["name"])
        return e

    def _use_object(self, name, create: bool) -> None:
        """Tools given object=...: act on that object (a model tool adds it when it is new)."""
        e = self._entry(name, required=not create) if self.scene_objects else None
        if e is None:
            if not create:
                raise sc.SceneError(f"Unknown object {name!r}. Objects: none yet (add one with add_object)")
            e = self._add_object(name)
        self._activate(e["name"])

    def _describe_object(self, e: dict) -> str:
        st = self._object_state(e)
        x, y, z = e["position"]
        where = f" at ({_mm(x)}, {_mm(y)}, {_mm(z)}) mm" if (x or y or z) else ""
        what = st["model_name"] or "no model yet"
        parts = f", {len(st['parts'].parts)} parts ({st['method_summary']})" if st["parts"] is not None else ""
        return f"{e['name']} ({e['prefix']}): {what}{where}{parts}" + (" [active]" if e["name"] == self.active else "")

    def _t_add_object(self, name, prefix="", x_mm=0.0, y_mm=0.0, z_mm=0.0):
        first = self._start_scene()
        e = self._add_object(name, prefix, (x_mm, y_mm, z_mm))
        text = f"Added {e['name']} (labels {e['prefix']}-...). It has no model yet: use create_shape or load_model_file."
        if first:
            text = f"The design so far is now the object {first}. " + text
        return text

    def _t_copy_object(self, name, new_name, prefix="", x_mm=None, y_mm=None, z_mm=None):
        self._start_scene()
        src = self._entry(name)
        pos = [c if c is not None else src["position"][i] for i, c in enumerate((x_mm, y_mm, z_mm))]
        e = self._new_entry(new_name, prefix, pos, state=sc.copy_state(self._object_state(src)),
                            method=self._object_method(src))
        self.scene_objects.append(e)
        again = self._scene_changed()
        return f"Copied {src['name']} to {e['name']} (labels {e['prefix']}-...)." + self._rerun_scene(again)

    def _t_move_object(self, name, x_mm=0.0, y_mm=0.0, z_mm=0.0):
        self._start_scene()
        e = self._entry(name)
        e["position"] = sc.check_position(x_mm, y_mm, z_mm)
        return f"Moved {e['name']} to ({_mm(x_mm)}, {_mm(y_mm)}, {_mm(z_mm)}) mm. Its parts are unchanged."

    def _t_remove_object(self, name):
        e = self._entry(name)
        idx = self.scene_objects.index(e)
        was_active = e["name"] == self.active
        self.scene_objects.remove(e)
        if was_active:
            self.active = None
            for a, v in sc.blank_state().items():
                setattr(self, a, v)
            if self.scene_objects:
                self._activate(self.scene_objects[max(0, idx - 1)]["name"])
            else:
                self.recipe = {k: v for k, v in self.recipe.items() if k != "method"}
        again = self._scene_changed()
        text = f"Removed {e['name']} and its parts."
        return text + self._rerun_scene(again)

    def _t_select_object(self, name):
        self._activate(self._entry(name)["name"])
        return "Active object: " + self._describe_object(self._entry(self.active))

    # ---- brief (brief.py) --------------------------------------------------
    def brief_facts(self) -> Dict[str, Any]:
        """What the brief is checked against, all worked out by the engine."""
        facts: Dict[str, Any] = {}
        meshes = [m for _, m in self._placed_meshes()]
        if meshes:
            boxes = [m.bbox() for m in meshes]
            lo = [min(b[0][i] for b in boxes) for i in range(3)]
            hi = [max(b[1][i] for b in boxes) for i in range(3)]
            facts["size_mm"] = [round(hi[i] - lo[i], 3) for i in range(3)]
        q = self.last_quote or {}
        facts["quote_total"] = q.get("total")
        facts["currency"] = q.get("currency") or self.settings.resolve("currency")["value"] or "AUD"
        if self.nested:
            facts["sheet_count"] = self.nested["sheet_count"]
            facts["sheet_size"] = [self.nested["sheet_width"], self.nested["sheet_height"]]
        states = [self._object_state(e) for e in self.scene_objects] or [
            {"parts": self.parts, "board_thickness": self.board_thickness}]
        thick = [st["board_thickness"] for st in states if st["parts"] is not None and st["board_thickness"]]
        if thick:
            facts["thickness_mm"] = max(thick)
        return facts

    def brief_checks(self) -> List[dict]:
        with self._lock:
            return bf.check_brief(self.brief, self.brief_facts()) if self.brief else []

    def set_brief(self, values) -> dict:
        """Change brief fields (None clears one). Raises BriefError and changes nothing on a bad value."""
        with self._lock:
            self.brief = bf.merge(self.brief, bf.clean_values(values))
            self._bump()
            return self.brief_listing()

    def brief_listing(self) -> dict:
        return {"values": dict(self.brief), "checks": self.brief_checks(),
                "fields": [dict(f) for f in bf.FIELDS.values()]}

    def _t_set_brief(self, values):
        self.brief = bf.merge(self.brief, bf.clean_values(values))
        set_keys = ", ".join(k for k in values if values[k] is not None) or "none"
        cleared = ", ".join(k for k in values if values[k] is None)
        text = f"Brief updated (set: {set_keys}" + (f"; cleared: {cleared}" if cleared else "") + ")."
        return text + "\n" + bf.checks_text(self.brief_checks())

    def _t_check_brief(self):
        if not self.brief:
            return "There is no brief yet. Fill it in with set_brief."
        return bf.checks_text(self.brief_checks())

    def _t_check_structure(self, use, load_kg=None):
        if self.model is None:
            raise ToolInputError("There is no model yet. Create one with create_shape or load one with load_model_file.")
        if self.method != "waffle" or not self.parts3d:
            raise ToolInputError("Only waffle designs can be checked for strength yet; slice the model with "
                                 "waffle_slice first.")
        if load_kg is None:
            field = "load_per_seat_kg" if use == "seat" else "shelf_load_kg"
            load_kg = self.brief.get(field)
            if load_kg is None:
                raise ToolInputError(f"Give load_kg, or set {field} in the brief with set_brief.")
        ect = self.settings.resolve("board_ect")["value"]
        if ect is None:
            raise ToolInputError("The board's edge crush strength (setting board_ect, kN/m) is not set. Ask the "
                                 "designer for it from the board supplier's data sheet with request_inputs, and "
                                 "wait for the answer. Never guess it.")
        sf_key = "seat_safety_factor" if use == "seat" else "load_safety_factor"
        floor_z = self.model.bbox()[0][2]
        r = st.check_waffle(self.parts3d, floor_z, self.board_thickness, use=use, load_kg=load_kg, ect_kn_m=ect,
                            safety_factor=self.settings.resolve(sf_key)["value"],
                            max_panel_ratio=self.settings.resolve("max_panel_ratio")["value"],
                            max_span_ratio=self.settings.resolve("max_span_ratio")["value"])
        name = self.active or ""
        self.last_structure = dict(r, object=name or None)
        return st.text(r, name)

    def _t_list_objects(self):
        if not self.scene_objects:
            return "The design is one model, not a scene. add_object starts a scene."
        return "\n".join(self._describe_object(e) for e in self.scene_objects)

    def _placed_meshes(self) -> List[Tuple[dict, Mesh]]:
        """Each object's model where it stands in the scene (or the one model)."""
        if not self.scene_objects:
            return [({}, self.model)] if self.model is not None else []
        out = []
        for e in self.scene_objects:
            m = self._object_state(e)["model"]
            if m is not None:
                out.append((e, sc.moved(m, e["position"])))
        return out

    def _objects_info(self) -> List[dict]:
        out = []
        for e in self.scene_objects:
            st = self._object_state(e)
            size = None
            if st["model"] is not None:
                (lx, ly, lz), (hx, hy, hz) = st["model"].bbox()
                size = [round(hx - lx, 2), round(hy - ly, 2), round(hz - lz, 2)]
            out.append({"name": e["name"], "prefix": e["prefix"], "position": list(e["position"]),
                        "model": st["model_name"], "size_mm": size, "method": st["method"],
                        "method_summary": st["method_summary"],
                        "part_count": len(st["parts"].parts) if st["parts"] is not None else 0,
                        "active": e["name"] == self.active})
        return out

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
        scene_lines = []
        if self.scene_objects:
            scene_lines = [f"Scene of {len(self.scene_objects)} object(s), active: {self.active}:"] + [
                "- " + self._describe_object(e) for e in self.scene_objects]
            if self.nested is not None:
                n = self.nested
                scene_lines.append(f"The scene is nested on {n['sheet_count']} sheet(s) of {_mm(n['sheet_width'])} x "
                                   f"{_mm(n['sheet_height'])} mm")
        if self.model is None:
            return "\n".join(scene_lines + ["No model yet" + (f" in {self.active}." if self.active else ".")])
        lines = scene_lines + [self._describe_model()]
        if self.parts is not None:
            lines.append(f"Parts: {self.method_summary}")
        if self.nested is not None and not self.scene_objects:
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
        if self.scene_objects:
            self.scene_doc = r.doc
        else:
            self.parts = r.doc
        self.last_estimate = None
        self.nested = {"sheet_count": r.sheet_count, "utilisation": list(r.utilisation),
                       "sheet_width": sheet_width_mm, "sheet_height": sheet_height_mm}
        use = ", ".join(f"{u * 100:.0f}%" for u in r.utilisation)
        return (f"Nested {len(r.doc.parts)} parts on {r.sheet_count} sheet(s) of {_mm(sheet_width_mm)} x "
                f"{_mm(sheet_height_mm)} mm. Board used per sheet: {use}.")

    def _t_estimate_cost(self, **given):
        """Measure the parts, take prices and rates from the settings, and work out the estimate and quote."""
        doc = self._need_parts()
        m = self._measure(doc)
        overrides = {k: v for k, v in given.items() if v is not None}
        values, missing = {}, []
        for key in ESTIMATE_SETTINGS:
            v = overrides.get(key)
            if v is None:
                v = self.settings.resolve(key)["value"]
            if v is None:
                missing.append(key)
            values[key] = v
        if missing:
            raise self._missing_settings_error(missing)
        r = estimate(CutEstimateInput(cut_length=m["cut_length"], score_length=m["score_length"],
                                      pierce_count=m["pierces"], sheet_count=m["sheets"], **values))
        quote = self._quote(m, overrides)
        self.last_estimate = {"cut_length_mm": m["cut_length"], "score_length_mm": m["score_length"],
                              "pierce_count": m["pierces"], "sheet_count": m["sheets"], **r.to_dict()}
        self.last_quote = quote
        cur = quote["currency"]
        text = (f"Cut {m['cut_length'] / 1000:.2f} m, score/etch {m['score_length'] / 1000:.2f} m, "
                f"{m['pierces']} pierces, {m['sheets']} sheet(s). Machine time {r.time_min:.1f} min. "
                f"Material {r.material_cost:.2f}, machine {r.machine_cost:.2f}, Total {r.total_cost:.2f}.")
        lines = ", ".join(f"{l['label']} {l['value']:.2f}" for l in quote["lines"] if l["role"] == "line")
        if quote["total"] is not None:
            text += f" Quote: {lines}; quote total {quote['total']:.2f} {cur} (lines in the Project tab)."
        if not self.nested:
            text += " (Not nested yet, so 1 sheet was assumed.)"
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
    def save_project(self, folder: str = "", name: str = "", chat=None, overwrite: bool = False) -> dict:
        """Write the design as a project folder (see ``project.py``). Raises ProjectError.

        Saving into the open project's folder after its files changed on disk (another Mac,
        a sync) raises ProjectChangedError, unless ``overwrite``."""
        with self._lock:
            if not self._placed_meshes():
                raise ProjectError("There is nothing to save yet: make or load a model first")
            if not isinstance(name, str) or not isinstance(folder, str):
                raise ProjectError("name and folder must be text")
            directory, clean = proj.resolve_save_dir(folder, name, self.home, self.locations()["projects"])
            if not overwrite and self.project is not None and self.project["folder"] == directory:
                known = dict(self.project.get("files") or {})
                if chat is None:
                    known.pop(proj.CHAT, None)  # left as it is on disk
                found = proj.outside_changes(directory, known)
                if found["changed"] or found["missing"]:
                    raise proj.ProjectChangedError(found["changed"] + found["missing"])
            previous = proj.check_writable(directory)
            files = self._project_files(clean)
            if chat is not None:
                files[proj.CHAT] = json.dumps(chat, ensure_ascii=False, indent=1) + "\n"
            manifest_files = sorted(set(files) | {proj.MANIFEST} | (
                {proj.CHAT} if os.path.isfile(os.path.join(directory, proj.CHAT)) else set()))
            files[proj.MANIFEST] = json.dumps(self._manifest(clean, manifest_files), indent=2) + "\n"
            proj.write_files(directory, files, previous)
            self.project = {"name": clean, "folder": directory, "saved_version": self.version,
                            "files": proj.fingerprint(directory, manifest_files)}
            return {"name": clean, "folder": directory, "files": manifest_files, "version": self.version}

    def _project_files(self, name: str) -> Dict[str, str]:
        from .components.export import safe_file_stem, to_dxf

        if self.scene_objects:
            files = {}
            for e in self.scene_objects:
                st = self._object_state(e)
                if st["model"] is not None:
                    files[f"model/{e['prefix']}.obj"] = to_obj(st["model"], st["model_name"] or "")
        else:
            files = {proj.MODEL_FILE: to_obj(self.model, self.model_name or "")}
        doc = self._sheet_doc()
        if doc is not None:
            files["parts/parts.json"] = doc.to_json() + "\n"
            stem = re.sub(r"-{2,}", "-", safe_file_stem(name))
            for sheet in doc.layout():
                files[f"cut-files/{stem}-sheet{sheet.index + 1}.dxf"] = to_dxf(sheet)
                files[f"cut-files/{stem}-sheet{sheet.index + 1}.svg"] = to_svg(sheet)
        if self.last_estimate is not None:
            files["estimate/estimate.json"] = json.dumps(self.last_estimate, indent=2) + "\n"
            files["estimate/estimate.csv"] = proj.estimate_csv(self.last_estimate)
        if self.last_quote is not None:
            files["estimate/quote.json"] = json.dumps(self.last_quote, indent=2) + "\n"
            files["estimate/quote.csv"] = quote_csv(self.last_quote)
        if self.settings.project_data["values"] or self.settings.project_data["definitions"]:
            files["settings.json"] = self.settings.project_file_text()
        if self.formulas.project_formulas:
            files["formulas.json"] = self.formulas.project_file_text()
        if self.brief:
            files[bf.BRIEF_FILE] = bf.file_text(self.brief)
            files[bf.BRIEF_MD] = bf.markdown(name, self.brief, self.brief_checks(),
                                             self.brief_facts().get("currency", ""))
        # The model, not the ribs: interlocking ribs cannot be drawn back to front.
        # The app adds preview/preview.png, a snapshot of its 3D preview.
        files["preview/model.svg"] = proj.model_svg([t for _, m in self._placed_meshes() for t in m.triangles()])
        return files

    def _summary(self) -> dict:
        meshes = [m for _, m in self._placed_meshes()]
        boxes = [m.bbox() for m in meshes]
        lx, ly, lz = (min(b[0][i] for b in boxes) for i in range(3))
        hx, hy, hz = (max(b[1][i] for b in boxes) for i in range(3))
        est = self.last_estimate or {}
        doc = self._sheet_doc()
        return {
            "size_mm": [round(hx - lx, 2), round(hy - ly, 2), round(hz - lz, 2)],
            "faces": sum(len(m.faces) for m in meshes),
            "method": "scene" if self.scene_objects else self.method,
            "method_summary": (f"scene of {len(self.scene_objects)} objects" if self.scene_objects
                               else self.method_summary),
            "part_count": len(doc.parts) if doc is not None else 0,
            "sheet_count": self.nested["sheet_count"] if self.nested else (1 if doc is not None else 0),
            "sheet_size": [self.nested["sheet_width"], self.nested["sheet_height"]] if self.nested else None,
            "time_min": est.get("time_min"),
            "total_cost": est.get("total_cost"),
            "quote_total": (self.last_quote or {}).get("total"),
        }

    def _manifest(self, name: str, files: List[str]) -> dict:
        import datetime

        from . import __version__

        return {
            "schema": proj.PROJECT_SCHEMA,
            "name": name,
            "app": brand.APP_NAME,
            "engine_version": __version__,
            "saved_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "units": "mm",
            "model": ({"file": proj.MODEL_FILE, "name": self.model_name} if not self.scene_objects
                      else self._scene_manifest()["objects"][0]["model"]),
            "recipe": (self.recipe if not self.scene_objects
                       else {k: v for k, v in self.recipe.items() if k in ("nest", "estimate")}),
            **({"scene": self._scene_manifest()} if self.scene_objects else {}),
            "workflow": {"name": self.workflows.active()["name"],
                         "apply_defaults": self.workflows.settings()["apply_defaults"]},
            "summary": self._summary(),
            "rhino_overrides": ([o.to_dict() for o in self.overrides.values()] if not self.scene_objects else []),
            "files": files,
        }

    def _scene_manifest(self) -> dict:
        objs = []
        for e in self.scene_objects:
            st = self._object_state(e)
            objs.append({
                "name": e["name"], "prefix": e["prefix"], "position": list(e["position"]),
                "model": ({"file": f"model/{e['prefix']}.obj", "name": st["model_name"]}
                          if st["model"] is not None else None),
                "method": self._object_method(e),
                "rhino_overrides": [o.to_dict() for o in st["overrides"].values()],
            })
        return {"active": self.active, "objects": objs}

    def open_project(self, path: str) -> dict:
        """Load a project folder and rebuild its design. On error the current design is kept."""
        if not isinstance(path, str):
            raise ProjectError("The project folder must be text")
        directory = proj.resolve_open_dir(path, self.home)
        m = proj.read_manifest(directory)
        name = m.get("name") if isinstance(m.get("name"), str) and m.get("name") else os.path.basename(directory)
        scratch = DesignSession(home=self.home)
        scratch.settings.load_project(directory)
        scratch.formulas.load_project(directory)
        brief_path = os.path.join(directory, bf.BRIEF_FILE)
        if os.path.isfile(brief_path):
            try:
                scratch.brief = bf.read_file(proj._read_text(brief_path, proj.MAX_MANIFEST_BYTES, bf.BRIEF_FILE))
            except bf.BriefError as exc:
                raise ProjectError(str(exc)) from None
        recipe = m.get("recipe") or {}
        if not isinstance(recipe, dict):
            raise ProjectError(f"{proj.MANIFEST}: recipe must be an object")
        scene_info = m.get("scene")
        steps = []
        if scene_info is not None:
            rel = self._open_scene(scratch, directory, scene_info)
            method = {} if scene_info.get("objects") else None
        else:
            model_info = m.get("model") if isinstance(m.get("model"), dict) else {}
            rel = self._open_model(scratch, directory, model_info, m.get("rhino_overrides"))
            method = recipe.get("method")
            if method is not None:
                self._check_method(method)
                steps.append((method["tool"], method.get("input", {})))
        if method is not None:
            for key, tname in (("nest", "nest_sheets"), ("estimate", "estimate_cost")):
                if recipe.get(key) is not None:
                    args = recipe[key]
                    if tname == "estimate_cost" and isinstance(args, dict):
                        # Saved before prices became settings: they become this project's settings.
                        legacy = {k: args[k] for k in SETTING_ONLY_INPUTS["estimate_cost"] if k in args}
                        scratch.settings.adopt_legacy(legacy, "earlier version of the project")
                        args = {k: v for k, v in args.items() if k not in legacy}
                    steps.append((tname, args))
        for tname, args in steps:
            try:
                checked = _check_args(_TOOLS_BY_NAME[tname], args)
                getattr(scratch, "_t_" + tname)(**checked)
                scratch._record(tname, checked)
            except SettingsError:
                if tname != "estimate_cost":
                    raise
                # No prices yet: open the design without an estimate; Claude asks for them.
            except (ToolInputError, *INPUT_ERRORS) as exc:
                raise ProjectError(f"{proj.MANIFEST}: the {tname} step could not run again: {exc}") from None
        chat = proj.read_chat(directory)
        with self._lock:
            for attr in ("model", "model_name", "method", "method_summary", "parts", "parts3d",
                         "board_thickness", "nested", "last_estimate", "last_quote", "recipe", "overrides",
                         "generated", "scene_objects", "active", "scene_doc", "brief"):
                setattr(self, attr, getattr(scratch, attr))
            self.notes, self.notices = [], []
            self.settings.project_data = scratch.settings.project_data
            self.settings.concept_data = scratch.settings.concept_data
            self.formulas.project_formulas = scratch.formulas.project_formulas
            self.steps = []
            self._log_step("open_project", True, f"Opened project {name} from {directory}")
            self._bump()
            listed = m.get("files") if isinstance(m.get("files"), list) else []
            known = set(listed) | {proj.MANIFEST, proj.CHAT, rel}
            self.project = {"name": name, "folder": directory, "saved_version": self.version,
                            "files": proj.fingerprint(directory, known)}
            return {"name": name, "folder": directory, "chat": chat, "version": self.version,
                    "summary": m.get("summary")}

    def locations(self) -> dict:
        """Where projects and the library are saved on this Mac (see ``project.read_locations``)."""
        return proj.read_locations(self.home, self.data_dir)

    def set_locations(self, projects=None, library=None) -> dict:
        """Change the projects and/or library location. An empty value goes back to the default."""
        return proj.write_locations(self.home, self.data_dir, projects=projects, library=library)

    def project_status(self) -> dict:
        """Did the open project's files change on disk since this Mac saved or opened it?"""
        with self._lock:
            if self.project is None:
                return {"project": None, "folder": None, "changed": [], "missing": [], "syncing": [],
                        "conflicts": [], "outside_changes": False}
            name, directory = self.project["name"], self.project["folder"]
            known = dict(self.project.get("files") or {})
        found = proj.outside_changes(directory, known)
        found["outside_changes"] = bool(found["changed"] or found["missing"] or found["conflicts"])
        return {"project": name, "folder": directory, **found}

    @staticmethod
    def _check_method(method) -> None:
        if not isinstance(method, dict) or method.get("tool") not in METHOD_TOOLS:
            raise ProjectError(f"{proj.MANIFEST}: the recipe method must be one of {', '.join(sorted(METHOD_TOOLS))}")

    @staticmethod
    def _open_model(scratch: "DesignSession", directory: str, model_info: dict, raw_overrides,
                    name: Optional[str] = None) -> str:
        """Load one model file (and its Rhino hand edits) into the scratch session. Returns its path."""
        rel = proj._safe_rel(model_info.get("file", proj.MODEL_FILE))
        if rel is None:
            raise ProjectError(f"{proj.MANIFEST} names a model file outside the project")
        try:
            mesh = parse_obj(proj._read_text(os.path.join(directory, rel), proj.MAX_MODEL_BYTES, rel))
        except MeshFileError as exc:
            raise ProjectError(f"{rel}: {exc}") from None
        model_name = model_info.get("name") if isinstance(model_info.get("name"), str) else (name or "model")
        scratch._set_model(mesh, model_name)
        raw_overrides = raw_overrides or []
        if not isinstance(raw_overrides, list):
            raise ProjectError(f"{proj.MANIFEST}: rhino_overrides must be a list")
        try:
            scratch.overrides = {o.part: o for o in map(Override.from_dict, raw_overrides)}
        except PartsError as exc:
            raise ProjectError(f"{proj.MANIFEST}: {exc}") from None
        return rel

    def _open_scene(self, scratch: "DesignSession", directory: str, info) -> str:
        """Rebuild each scene object (model, hand edits, method) in the scratch session."""
        objs = info.get("objects") if isinstance(info, dict) else None
        if not isinstance(objs, list) or not all(isinstance(o, dict) for o in objs):
            raise ProjectError(f"{proj.MANIFEST}: scene.objects must be a list of objects")
        first_rel = proj.MODEL_FILE
        for o in objs:
            pos = o.get("position") or [0, 0, 0]
            if (not isinstance(pos, list) or len(pos) != 3
                    or not all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in pos)):
                raise ProjectError(f"{proj.MANIFEST}: the position of {o.get('name')!r} must be [x, y, z]")
            try:
                e = scratch._new_entry(o.get("name"), o.get("prefix") or "", pos)
            except sc.SceneError as exc:
                raise ProjectError(f"{proj.MANIFEST}: {exc}") from None
            scratch.scene_objects.append(e)
            scratch._activate(e["name"])
            if isinstance(o.get("model"), dict):
                rel = self._open_model(scratch, directory, o["model"], o.get("rhino_overrides"), e["name"])
                if first_rel == proj.MODEL_FILE:
                    first_rel = rel
            method = o.get("method")
            if method is not None:
                self._check_method(method)
                try:
                    checked = _check_args(_TOOLS_BY_NAME[method["tool"]], method.get("input", {}))
                    getattr(scratch, "_t_" + method["tool"])(**checked)
                    scratch._record(method["tool"], checked)
                except (ToolInputError, *INPUT_ERRORS) as exc:
                    raise ProjectError(f"{proj.MANIFEST}: the {method['tool']} step of {e['name']} could not run "
                                       f"again: {exc}") from None
        active = info.get("active")
        if isinstance(active, str) and scratch._entry(active, required=False) is not None:
            scratch._activate(scratch._entry(active)["name"])
        return first_rel

    def push_model(self, mesh: Mesh, name: str) -> int:
        """A model sent from Rhino (Hops SendToFoldTheory) replaces the current one."""
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
            doc = self._sheet_doc()
            if doc is not None:
                for sheet in doc.layout():
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
                "parts": doc.to_json() if doc is not None else None,
            }

    def _live_summary(self) -> str:
        if self.model is None:
            return "No design in Fold Theory yet. Make one in the app's Design Assistant."
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
        if self.scene_objects:
            for e in self.scene_objects:
                st = self._object_state(e)
                if st["model"] is None:
                    continue
                kind = "assembly" if st["method"] == "unfold" else "model"
                objects.append(self._mesh_object(f"{e['prefix']}:model", kind, e["name"],
                                                 sc.moved(st["model"], e["position"])))
                for pid, loops in st["parts3d"].items():
                    try:
                        tris = extrude(sc.moved_loops(loops, e["position"]), st["board_thickness"])
                    except ValueError:
                        continue
                    objects.append(_triangles_object(f"{e['prefix']}-{pid}", tris))
        if self.model is not None:
            (lx, ly, lz), (hx, hy, hz) = self.model.bbox()
            model = {
                "name": self.model_name,
                "faces": len(self.model.faces),
                "size_mm": [round(hx - lx, 2), round(hy - ly, 2), round(hz - lz, 2)],
                "bbox": [[lx, ly, lz], [hx, hy, hz]],
            }
            if not self.scene_objects:
                kind = "assembly" if self.method == "unfold" else "model"
                objects.append(self._mesh_object("model", kind, self.model_name, self.model))
                for pid, loops in self.parts3d.items():
                    try:
                        tris = extrude(loops, self.board_thickness)
                    except ValueError:
                        continue
                    objects.append(_triangles_object(pid, tris))
        doc = self._sheet_doc()
        if doc is not None:
            util = self.nested["utilisation"] if self.nested else None
            for sheet in doc.layout():
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
            "nested": self.nested is not None if doc is not None else False,
            "sheet_size": [self.nested["sheet_width"], self.nested["sheet_height"]] if self.nested else None,
            "sheets": sheets,
            "estimate": self.last_estimate,
            "steps": list(self.steps),
            "scene": {"objects": objects},
            "objects": self._objects_info(),
            "active_object": self.active,
            "brief": {"values": dict(self.brief), "checks": self.brief_checks()},
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
