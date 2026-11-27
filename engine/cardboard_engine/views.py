"""Configurable views: screens as JSON, drawn by a fixed set of native blocks.

A view is a JSON document (schema ``cardboardcad.view/1``)::

    {"schema": "cardboardcad.view/1", "id": "costs", "title": "Costs",
     "blocks": [{"type": "section", "title": "Quote", "blocks": [
         {"type": "table", "bind": "quote.lines", "columns": ["label", "value", "unit"]},
         {"type": "total", "bind": "quote.total", "label": "Total"}]}]}

The app draws each block with its own SwiftUI code; a view only arranges
blocks and binds them to data the engine holds. Before a view is used it is
checked here: unknown block types, unknown keys, unknown bindings and actions
that are not registered are rejected with a reason Claude can act on, and the
current view stays as it was. A new *kind* of block is a code change.

Where views live, most specific wins::

    engine assets/views/*.json                     Fold Theory's defaults (read only)
    ~/Documents/Fold Theory/Views/<id>.json        saved for all projects
    <project folder>/views/<id>.json               saved for one project

Saving keeps the file it replaces under ``.history/<id>/``, so undo restores
it byte for byte (or removes a view that was new).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Dict, List, Optional

from .brand import documents_dir

VIEW_SCHEMA = "cardboardcad.view/1"
ASSETS = os.path.join(os.path.dirname(__file__), "assets", "views")
ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,40}$")
MAX_BLOCKS = 200
MAX_DEPTH = 6
MAX_TEXT = 2000
MAX_HISTORY = 50
MAX_FILE_BYTES = 256 * 1024

# Registered actions a button may run. The app maps each one to its own code.
ACTIONS: Dict[str, str] = {
    "estimate_cost": "Work out the estimate and quote again",
    "nest_sheets": "Nest the parts on sheets again",
    "export_cut_files": "Write the cut files",
    "save_project": "Save the project",
    "open_diagnostics": "Show the Diagnostics panel",
}

_COMMON = {"type", "id"}
# Block type -> (allowed keys, required keys, what it binds: None, "scalar" or "collection")
BLOCK_TYPES: Dict[str, tuple] = {
    "section": ({"title", "blocks", "collapsed"}, {"blocks"}, None),
    "tabs": ({"tabs"}, {"tabs"}, None),
    "text": ({"text", "style"}, {"text"}, None),
    "field": ({"bind", "label", "editable"}, {"bind"}, "scalar"),
    "total": ({"bind", "label"}, {"bind"}, "scalar"),
    "table": ({"bind", "columns", "title"}, {"bind"}, "collection"),
    "settings": ({"bind", "group", "editable"}, {"bind"}, "collection"),
    "checklist": ({"bind", "title"}, {"bind"}, "collection"),
    "image": ({"bind", "caption"}, {"bind"}, "scalar"),
    "model3d": ({"height"}, set(), None),
    "cut_sheets": ({"height"}, set(), None),
    "button": ({"label", "action"}, {"label", "action"}, None),
}
TEXT_STYLES = ("body", "caption", "heading")


class ViewError(ValueError):
    """A view that cannot be used. The message names the block and what is wrong."""


def views_dir(home: str) -> str:
    return os.path.join(documents_dir(home), "Views")


def _scalar(label: str, editable: bool = False) -> dict:
    return {"kind": "scalar", "label": label, "editable": editable}


def _collection(label: str, columns) -> dict:
    return {"kind": "collection", "label": label, "columns": list(columns)}


def static_catalog(setting_keys=()) -> Dict[str, dict]:
    """Every binding a view may use: what it is and, for collections, its columns."""
    from .settings import BUILTIN_DEFINITIONS

    cat = {
        "project.name": _scalar("Project name"),
        "project.folder": _scalar("Project folder"),
        "project.status": _scalar("Saved or unsaved"),
        "design.method": _scalar("How the parts are made"),
        "design.size": _scalar("Model size"),
        "design.parts": _scalar("Number of parts"),
        "design.sheets": _scalar("Number of sheets"),
        "design.version": _scalar("Design version"),
        "estimate.time_min": _scalar("Machine time, min"),
        "estimate.sheet_count": _scalar("Sheets"),
        "estimate.cut_length_mm": _scalar("Cut length, mm"),
        "quote.total": _scalar("Quote total"),
        "quote.subtotal": _scalar("Cost before margin and GST"),
        "quote.currency": _scalar("Currency"),
        "quote.lines": _collection("Quote lines", ("key", "label", "value", "unit", "role", "version")),
        "settings": _collection("Settings", ("key", "label", "value", "unit", "layer", "set_by", "set_at",
                                             "group", "type")),
        "formulas": _collection("Formulas", ("key", "label", "expr", "unit", "version", "source", "role")),
        "workflow.name": _scalar("Workflow"),
        "workflow.status": _scalar("Next workflow step"),
        "workflow.steps": _collection("Workflow steps", ("title", "tools", "optional", "done")),
        "parts": _collection("Parts", ("id", "label")),
        "sheets": _collection("Sheets", ("index", "parts", "utilisation")),
        "diagnostics": _collection("Diagnostics", ("time", "source", "level", "message")),
        "preview.model": _scalar("Picture of the model"),
    }
    for key in set(BUILTIN_DEFINITIONS) | set(setting_keys):
        cat[f"settings.{key}"] = _scalar(f"Setting {key}", editable=True)
    return cat


def _check_block(block, path: str, catalog: Dict[str, dict], depth: int, counter: List[int]) -> dict:
    if not isinstance(block, dict):
        raise ViewError(f"{path}: a block must be an object with a type")
    counter[0] += 1
    if counter[0] > MAX_BLOCKS:
        raise ViewError(f"A view can have at most {MAX_BLOCKS} blocks")
    t = block.get("type")
    if t not in BLOCK_TYPES:
        raise ViewError(f"{path}: unknown block type {t!r}. Block types: {', '.join(BLOCK_TYPES)}. "
                        "A new kind of block needs a new version of the app")
    allowed, required, binds = BLOCK_TYPES[t]
    for key in block:
        if key not in allowed | _COMMON:
            raise ViewError(f"{path}: a {t} block has no key {key!r}. Keys: {', '.join(sorted(allowed))}")
    for key in required:
        if key not in block:
            raise ViewError(f"{path}: a {t} block needs {key!r}")
    out: Dict[str, Any] = {"type": t}
    if "id" in block:
        if not isinstance(block["id"], str) or not ID_RE.match(block["id"]):
            raise ViewError(f"{path}: id must be short lower-case text")
        out["id"] = block["id"]
    for key in ("title", "label", "text", "caption", "group"):
        if key in block:
            if not isinstance(block[key], str):
                raise ViewError(f"{path}: {key} must be text")
            out[key] = block[key][:MAX_TEXT]
    for key in ("editable", "collapsed"):
        if key in block:
            if not isinstance(block[key], bool):
                raise ViewError(f"{path}: {key} must be true or false")
            out[key] = block[key]
    if "height" in block:
        h = block["height"]
        if isinstance(h, bool) or not isinstance(h, (int, float)) or not 100 <= h <= 2000:
            raise ViewError(f"{path}: height must be between 100 and 2000 points")
        out["height"] = h
    if "style" in block:
        if block["style"] not in TEXT_STYLES:
            raise ViewError(f"{path}: style must be one of {', '.join(TEXT_STYLES)}")
        out["style"] = block["style"]
    if binds is not None:
        b = block["bind"]
        if not isinstance(b, str) or b not in catalog:
            near = sorted(k for k in catalog if isinstance(b, str) and k.split(".")[0] == b.split(".")[0])
            hint = f" Bindings under {b.split('.')[0]}: {', '.join(near)}" if near else \
                f" Bindings: {', '.join(sorted(catalog))}"
            raise ViewError(f"{path}: unknown binding {b!r}.{hint}")
        info = catalog[b]
        if info["kind"] != binds:
            raise ViewError(f"{path}: a {t} block needs a {binds} binding, but {b!r} is a {info['kind']}")
        if t == "settings" and b != "settings":
            raise ViewError(f"{path}: a settings block binds 'settings'")
        if out.get("editable") and t == "field" and not info.get("editable"):
            raise ViewError(f"{path}: {b!r} cannot be edited; only settings.<key> fields can")
        out["bind"] = b
        if "columns" in block:
            cols = block["columns"]
            if not isinstance(cols, list) or not cols or not all(isinstance(c, str) for c in cols):
                raise ViewError(f"{path}: columns must be a list of column names")
            bad = [c for c in cols if c not in info["columns"]]
            if bad:
                raise ViewError(f"{path}: {b!r} has no column {bad[0]!r}. Columns: {', '.join(info['columns'])}")
            out["columns"] = cols
    if t == "button":
        a = block["action"]
        if a not in ACTIONS:
            raise ViewError(f"{path}: {a!r} is not a registered action. Actions: {', '.join(ACTIONS)}")
        out["action"] = a
    if t == "section":
        out["blocks"] = _check_blocks(block["blocks"], path + ".blocks", catalog, depth + 1, counter)
    if t == "tabs":
        tabs = block["tabs"]
        if not isinstance(tabs, list) or not tabs:
            raise ViewError(f"{path}: tabs must be a list of {{title, blocks}}")
        out["tabs"] = []
        for i, tab in enumerate(tabs):
            tp = f"{path}.tabs[{i}]"
            if not isinstance(tab, dict) or set(tab) - {"title", "blocks"} or not isinstance(tab.get("title"), str):
                raise ViewError(f"{tp}: a tab is {{title, blocks}}")
            out["tabs"].append({"title": tab["title"][:100],
                                "blocks": _check_blocks(tab.get("blocks") or [], tp + ".blocks", catalog,
                                                        depth + 1, counter)})
    return out


def _check_blocks(blocks, path: str, catalog, depth: int, counter) -> List[dict]:
    if depth > MAX_DEPTH:
        raise ViewError(f"{path}: blocks are nested too deeply (at most {MAX_DEPTH})")
    if not isinstance(blocks, list):
        raise ViewError(f"{path} must be a list of blocks")
    return [_check_block(b, f"{path}[{i}]", catalog, depth, counter) for i, b in enumerate(blocks)]


def validate_view(raw, catalog: Optional[Dict[str, dict]] = None) -> dict:
    """The clean view, or ViewError naming the block (``blocks[0].blocks[1]``) and the reason."""
    catalog = catalog or static_catalog()
    if not isinstance(raw, dict):
        raise ViewError("A view must be a JSON object with schema, id, title and blocks")
    for key in raw:
        if key not in ("schema", "id", "title", "description", "blocks"):
            raise ViewError(f"A view has no key {key!r}. Keys: schema, id, title, description, blocks")
    if raw.get("schema", VIEW_SCHEMA) != VIEW_SCHEMA:
        raise ViewError(f"schema must be {VIEW_SCHEMA!r}")
    vid = raw.get("id")
    if not isinstance(vid, str) or not ID_RE.match(vid):
        raise ViewError("id must be short lower-case text, for example \"costs\"")
    title = raw.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ViewError("A view needs a title (the tab's name)")
    out = {"schema": VIEW_SCHEMA, "id": vid, "title": title.strip()[:60]}
    if raw.get("description"):
        if not isinstance(raw["description"], str):
            raise ViewError("description must be text")
        out["description"] = raw["description"][:MAX_TEXT]
    out["blocks"] = _check_blocks(raw.get("blocks"), "blocks", catalog, 1, [0])
    return out


def bindings_in(view: dict) -> List[str]:
    out: List[str] = []

    def walk(blocks):
        for b in blocks:
            if "bind" in b and b["bind"] not in out:
                out.append(b["bind"])
            walk(b.get("blocks", []))
            for tab in b.get("tabs", []):
                walk(tab["blocks"])

    walk(view["blocks"])
    return out


def _read_bytes(path: str) -> Optional[bytes]:
    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            return None
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


class ViewStore:
    """Finds, checks, saves and undoes views. ``data(binding)`` gives a binding's current value."""

    def __init__(self, home: str, project_folder: Callable[[], Optional[str]],
                 catalog: Callable[[], Dict[str, dict]], data: Callable[[str], Any],
                 on_rejected: Optional[Callable[[str, str], None]] = None):
        self.home = home
        self._project_folder = project_folder
        self._catalog = catalog
        self._data = data
        self._on_rejected = on_rejected

    def _dirs(self) -> List[tuple]:
        out = [("builtin", ASSETS), ("default", views_dir(self.home))]
        folder = self._project_folder()
        if folder:
            out.append(("project", os.path.join(folder, "views")))
        return out

    def _dir_for(self, scope: str) -> str:
        if scope == "all":
            return views_dir(self.home)
        if scope == "project":
            folder = self._project_folder()
            if not folder:
                raise ViewError("Save the project first: a project view is kept in the project folder. "
                                "Or save it for all projects (scope all)")
            return os.path.join(folder, "views")
        raise ViewError("scope must be project or all")

    def _load(self, path: str) -> Optional[dict]:
        raw = _read_bytes(path)
        if raw is None:
            return None
        try:
            return validate_view(json.loads(raw.decode("utf-8")), self._catalog())
        except (ValueError, UnicodeDecodeError) as exc:
            if self._on_rejected:
                self._on_rejected(os.path.basename(path), str(exc))
            return None

    def _all(self) -> Dict[str, dict]:
        found: Dict[str, dict] = {}
        for source, d in self._dirs():
            try:
                names = sorted(f for f in os.listdir(d) if f.endswith(".json") and not f.startswith("."))
            except OSError:
                continue
            for f in names[:100]:
                v = self._load(os.path.join(d, f))
                if v is not None and v["id"] == f[:-5]:
                    found[v["id"]] = {**v, "source": source}
        return found

    def listing(self) -> List[dict]:
        views = self._all()
        order = sorted(views.values(), key=lambda v: (v["id"] != "project", v["title"].lower()))
        return [{"id": v["id"], "title": v["title"], "source": v["source"]} for v in order]

    def get(self, view_id: str) -> Optional[dict]:
        v = self._all().get(view_id)
        if v is None:
            return None
        return {k: x for k, x in v.items() if k != "source"}

    def render(self, view_id: str) -> dict:
        v = self.get(view_id)
        if v is None:
            raise ViewError(f"There is no view called {view_id!r}")
        return {"view": v, "data": {b: self._data(b) for b in bindings_in(v)}}

    def save(self, raw, *, scope: str = "all", set_by: str = "") -> dict:
        try:
            view = validate_view(raw, self._catalog())
        except ViewError as exc:
            if self._on_rejected:
                vid = raw.get("id") if isinstance(raw, dict) else None
                self._on_rejected(str(vid), str(exc))
            raise
        directory = self._dir_for(scope)
        path = os.path.join(directory, view["id"] + ".json")
        self._push_history(directory, view["id"], _read_bytes(path))
        from .project import _write_atomic

        _write_atomic(path, json.dumps(view, indent=2, ensure_ascii=False) + "\n")
        return {**view, "source": "project" if scope == "project" else "default", "set_by": set_by}

    def _history_dir(self, directory: str, view_id: str) -> str:
        return os.path.join(directory, ".history", view_id)

    def _entries(self, hist: str) -> List[str]:
        try:
            names = [f for f in os.listdir(hist) if re.fullmatch(r"\d{6}\.(json|absent)", f)]
        except OSError:
            return []
        return sorted(names)

    def _push_history(self, directory: str, view_id: str, old: Optional[bytes]) -> None:
        hist = self._history_dir(directory, view_id)
        os.makedirs(hist, exist_ok=True)
        entries = self._entries(hist)
        n = int(entries[-1][:6]) + 1 if entries else 1
        name = f"{n:06d}." + ("json" if old is not None else "absent")
        with open(os.path.join(hist, name), "wb") as fh:
            fh.write(old or b"")
        for stale in entries[:-MAX_HISTORY]:
            os.remove(os.path.join(hist, stale))

    def undo(self, view_id: str, *, scope: str = "all") -> dict:
        """Put back the file the last save replaced (or remove a view that was new)."""
        if not isinstance(view_id, str) or not ID_RE.match(view_id):
            raise ViewError("Give the id of the view to undo")
        directory = self._dir_for(scope)
        hist = self._history_dir(directory, view_id)
        entries = self._entries(hist)
        if not entries:
            raise ViewError(f"There is nothing to undo for view {view_id!r}")
        last = os.path.join(hist, entries[-1])
        path = os.path.join(directory, view_id + ".json")
        if last.endswith(".absent"):
            if os.path.exists(path):
                os.remove(path)
        else:
            with open(last, "rb") as fh:
                old = fh.read()
            tmp = path + ".tmp"
            with open(tmp, "wb") as fh:
                fh.write(old)
            os.replace(tmp, path)
        os.remove(last)
        return {"id": view_id, "restored": not last.endswith(".absent")}
