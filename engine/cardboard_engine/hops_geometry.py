"""Hops wrappers and plain-JSON routes for the geometry components.

Only this module touches rhino3dm. The components themselves
(``components/waffle.py``, ``components/export.py``...) work on plain
Python lists, so they are tested without Rhino or HTTP.

Components chain through the ``Parts`` text output (a cardboardcad.parts/1
JSON document): Waffle Slice / Contour Stack -> (Nest) -> Export.
"""

from __future__ import annotations

import json
import os
from typing import Iterable, List, Sequence

import ghhops_server as hs
from flask import Flask, jsonify, request

from .components.contour import ContourError, ContourInput, contour_stack
from .components.export import ExportError, export_files
from .components.geometry import GeometryError, Mesh
from .components.nest import NestError, NestInput, nest, parse_rotations
from .components.parts import PartsDoc, PartsError
from .components.unfold import UnfoldError, UnfoldInput, unfold
from .components.waffle import WaffleError, WaffleInput, waffle_slice

INPUT_ERRORS = (GeometryError, PartsError, WaffleError, ContourError, NestError, UnfoldError, ExportError)


def mesh_from_rhino(obj) -> Mesh:
    """rhino3dm.Mesh -> Mesh. Anything else is a clear component error."""
    import rhino3dm

    if not isinstance(obj, rhino3dm.Mesh):
        raise GeometryError("Connect a mesh (use Mesh Brep in Grasshopper to mesh a Brep first)")
    verts = obj.Vertices
    faces = obj.Faces
    return Mesh.from_lists(
        ((verts[i].X, verts[i].Y, verts[i].Z) for i in range(len(verts))),
        (tuple(faces[i]) for i in range(len(faces))),
    )


def polyline_curve(points: Sequence[Sequence[float]], closed: bool = True):
    import rhino3dm

    pl = rhino3dm.Polyline(len(points) + 1)
    for p in points:
        pl.Add(p[0], p[1], p[2] if len(p) > 2 else 0.0)
    if closed and points:
        p = points[0]
        pl.Add(p[0], p[1], p[2] if len(p) > 2 else 0.0)
    return pl.ToPolylineCurve()


def _curves(loops: Iterable[Sequence[Sequence[float]]], closed=True) -> List:
    return [polyline_curve(loop, closed) for loop in loops]


def _home() -> str:
    return os.environ.get("HOME") or os.path.expanduser("~")


def register(app: Flask, hops: hs.Hops, *, category: str, subcategory: str, icon: str) -> None:
    brand = dict(category=category, subcategory=subcategory, icon=icon)

    # ---- /waffle_slice ---------------------------------------------------
    @hops.component(
        "/waffle_slice",
        name="WaffleSlice",
        nickname="Waffle",
        description="Slice a closed mesh into interlocking X/Y cardboard ribs with half-depth slots (mm)",
        inputs=[
            hs.HopsMesh("Mesh", "M", "Closed mesh to slice (mm)"),
            hs.HopsInteger("X ribs", "Nx", "Number of ribs across X (planes normal to X)"),
            hs.HopsInteger("Y ribs", "Ny", "Number of ribs across Y (planes normal to Y)"),
            hs.HopsNumber("Thickness (mm)", "T", "Board thickness: slot width before clearance"),
            hs.HopsNumber("Slot clearance (mm)", "C", "Extra slot width so ribs slide together"),
            hs.HopsNumber("Kerf (mm)", "K", "Cutter width; outlines grow by K/2"),
        ],
        outputs=[
            hs.HopsString("Parts", "P", "Parts document: connect to Nest or Export"),
            hs.HopsCurve("Cut curves", "Cut", "Flat rib outlines laid out in a row (XY plane)", hs.HopsParamAccess.LIST),
            hs.HopsCurve("Ribs 3D", "R3", "Slotted ribs in place, to preview the assembly", hs.HopsParamAccess.LIST),
            hs.HopsString("Labels", "L", "Part labels, in the same order as the parts", hs.HopsParamAccess.LIST),
        ],
        **brand,
    )
    def waffle(M, Nx=5, Ny=5, T=3.0, C=0.2, K=0.0):
        result = waffle_slice(
            mesh_from_rhino(M),
            WaffleInput(nx=int(Nx), ny=int(Ny), thickness=float(T), clearance=float(C), kerf=float(K)),
        )
        doc = PartsDoc(parts=result.parts)
        flat = doc.layout()[0]
        cut = _curves(loop for p in flat.parts for loop in p.cut)
        ribs = _curves(loop for p in result.parts for loop in result.ribs3d[p.id])
        return doc.to_json(), cut, ribs, [p.id for p in result.parts]

    @app.post("/api/waffle_slice")
    def api_waffle_slice():
        data = request.get_json(silent=True)
        try:
            if not isinstance(data, dict) or not isinstance(data.get("mesh"), dict):
                raise GeometryError("Body must be an object with a mesh: {vertices, faces}")
            mesh = Mesh.from_lists(data["mesh"].get("vertices", []), data["mesh"].get("faces", []))
            inp = WaffleInput(
                nx=data.get("nx", 5),
                ny=data.get("ny", 5),
                thickness=data.get("thickness", 3.0),
                clearance=data.get("clearance", 0.2),
                kerf=data.get("kerf", 0.0),
            )
            result = waffle_slice(mesh, inp)
        except (TypeError, *INPUT_ERRORS) as exc:
            return jsonify(error="invalid_input", message=str(exc)), 422
        return jsonify({"schema": "cardboardcad.parts/1", "parts": [p.to_dict() for p in result.parts]})

    def _mesh_from_json(data):
        if not isinstance(data, dict) or not isinstance(data.get("mesh"), dict):
            raise GeometryError("Body must be an object with a mesh: {vertices, faces}")
        return Mesh.from_lists(data["mesh"].get("vertices", []), data["mesh"].get("faces", []))

    # ---- /contour_stack ----------------------------------------------------
    @hops.component(
        "/contour_stack",
        name="ContourStack",
        nickname="Contour",
        description="Slice a closed mesh into stacked layers of board, etched for alignment (mm)",
        inputs=[
            hs.HopsMesh("Mesh", "M", "Closed mesh to slice (mm)"),
            hs.HopsNumber("Layer thickness (mm)", "T", "Board thickness: one layer per band this thick"),
            hs.HopsNumber("Kerf (mm)", "K", "Cutter width; outlines grow by K/2"),
            hs.HopsBoolean("Etch", "E", "Etch neighbouring layers' outlines onto each layer for alignment"),
            hs.HopsString("Axis", "A", "Stacking axis: x, y or z"),
        ],
        outputs=[
            hs.HopsString("Parts", "P", "Parts document: connect to Nest or Export"),
            hs.HopsCurve("Cut curves", "Cut", "Flat layer outlines laid out in a row (XY plane)", hs.HopsParamAccess.LIST),
            hs.HopsCurve("Etch curves", "Etch", "Alignment etch lines, in the same layout", hs.HopsParamAccess.LIST),
            hs.HopsCurve("Layers 3D", "L3", "Layer outlines in place, to preview the stack", hs.HopsParamAccess.LIST),
            hs.HopsString("Labels", "L", "Layer labels L01, L02... bottom up", hs.HopsParamAccess.LIST),
        ],
        **brand,
    )
    def contour(M, T=3.0, K=0.0, E=True, A="z"):
        result = contour_stack(
            mesh_from_rhino(M), ContourInput(thickness=float(T), kerf=float(K), etch=bool(E), axis=str(A))
        )
        doc = PartsDoc(parts=result.parts)
        flat = doc.layout()[0]
        cut = _curves(loop for p in flat.parts for loop in p.cut)
        etch = _curves((line for p in flat.parts for line in p.etch), closed=False)
        layers = _curves(loop for p in result.parts for loop in result.layers3d[p.id])
        return doc.to_json(), cut, etch, layers, [p.id for p in result.parts]

    @app.post("/api/contour_stack")
    def api_contour_stack():
        data = request.get_json(silent=True)
        try:
            mesh = _mesh_from_json(data)
            inp = ContourInput(
                thickness=data.get("thickness", 3.0),
                kerf=data.get("kerf", 0.0),
                etch=bool(data.get("etch", True)),
                axis=str(data.get("axis", "z")),
            )
            result = contour_stack(mesh, inp)
        except (TypeError, *INPUT_ERRORS) as exc:
            return jsonify(error="invalid_input", message=str(exc)), 422
        return jsonify({"schema": "cardboardcad.parts/1", "parts": [p.to_dict() for p in result.parts]})

    # ---- /unfold -------------------------------------------------------------
    @hops.component(
        "/unfold",
        name="Unfold",
        nickname="Unfold",
        description="Unfold a polyhedral mesh into flat nets with fold lines, glue tabs and edge numbers (mm)",
        inputs=[
            hs.HopsMesh("Mesh", "M", "Mesh with flat faces (triangles or planar quads), mm"),
            hs.HopsNumber("Tab width (mm)", "Tw", "Glue tab width; 0 for no tabs"),
            hs.HopsNumber("Tab angle (deg)", "Ta", "Slope of the tab sides (0-90)"),
            hs.HopsNumber("Flat angle (deg)", "Fa", "Folds flatter than this are not scored"),
        ],
        outputs=[
            hs.HopsString("Parts", "P", "Parts document: connect to Nest or Export"),
            hs.HopsCurve("Cut curves", "Cut", "Net outlines (with tabs) laid out in a row", hs.HopsParamAccess.LIST),
            hs.HopsCurve("Score curves", "Score", "Fold lines, same layout", hs.HopsParamAccess.LIST),
            hs.HopsString("Labels", "L", "Net labels N1, N2... largest first", hs.HopsParamAccess.LIST),
            hs.HopsInteger("Net count", "N", "Number of separate nets"),
        ],
        **brand,
    )
    def unfold_component(M, Tw=8.0, Ta=45.0, Fa=1.0):
        result = unfold(
            mesh_from_rhino(M), UnfoldInput(tab_width=float(Tw), tab_angle=float(Ta), flat_angle=float(Fa))
        )
        doc = PartsDoc(parts=result.parts)
        flat = doc.layout()[0]
        cut = _curves(loop for p in flat.parts for loop in p.cut)
        score = _curves((line for p in flat.parts for line in p.score), closed=False)
        return doc.to_json(), cut, score, [p.id for p in result.parts], len(result.parts)

    @app.post("/api/unfold")
    def api_unfold():
        data = request.get_json(silent=True)
        try:
            mesh = _mesh_from_json(data)
            inp = UnfoldInput(
                tab_width=data.get("tab_width", 8.0),
                tab_angle=data.get("tab_angle", 45.0),
                flat_angle=data.get("flat_angle", 1.0),
            )
            result = unfold(mesh, inp)
        except (TypeError, *INPUT_ERRORS) as exc:
            return jsonify(error="invalid_input", message=str(exc)), 422
        return jsonify({"schema": "cardboardcad.parts/1", "parts": [p.to_dict() for p in result.parts]})

    # ---- /nest ---------------------------------------------------------------
    @hops.component(
        "/nest",
        name="NestSheets",
        nickname="Nest",
        description="Nest parts onto as many sheets as needed (true shape, bottom-left fill, mm)",
        inputs=[
            hs.HopsString("Parts", "P", "Parts document from Waffle Slice or Contour Stack"),
            hs.HopsNumber("Sheet width (mm)", "W", "Sheet width"),
            hs.HopsNumber("Sheet height (mm)", "H", "Sheet height"),
            hs.HopsNumber("Gap (mm)", "G", "Minimum space between parts"),
            hs.HopsNumber("Margin (mm)", "E", "Minimum space from the sheet edge"),
            hs.HopsString("Rotations", "R", "Allowed rotations in degrees, e.g. 0,90 or 0 for grain lock"),
        ],
        outputs=[
            hs.HopsString("Parts", "P", "Parts with placements: connect to Export"),
            hs.HopsCurve("Sheets", "S", "Sheet outlines, side by side on XY", hs.HopsParamAccess.LIST),
            hs.HopsCurve("Placed curves", "Cut", "Cut outlines on their sheets", hs.HopsParamAccess.LIST),
            hs.HopsInteger("Sheet count", "N", "Number of sheets used"),
            hs.HopsNumber("Use %", "U", "Area of parts / sheet area, per sheet", hs.HopsParamAccess.LIST),
        ],
        **brand,
    )
    def nest_component(P, W=1200.0, H=800.0, G=5.0, E=10.0, R="0,90"):
        inp = NestInput(
            sheet_width=float(W), sheet_height=float(H), gap=float(G), margin=float(E),
            rotations=tuple(parse_rotations(R)),
        )
        result = nest(PartsDoc.from_json(P), inp)
        step = inp.sheet_width * 1.1  # preview spacing between sheets
        sheets, placed = [], []
        for sheet in result.doc.layout():
            dx = sheet.index * step
            sheets.append(polyline_curve([(dx, 0), (dx + inp.sheet_width, 0), (dx + inp.sheet_width, inp.sheet_height), (dx, inp.sheet_height)]))
            placed += _curves([(x + dx, y) for x, y in loop] for p in sheet.parts for loop in p.cut)
        use = [round(u * 100, 2) for u in result.utilisation]
        return result.doc.to_json(), sheets, placed, result.sheet_count, use

    @app.post("/api/nest")
    def api_nest():
        data = request.get_json(silent=True)
        try:
            if not isinstance(data, dict):
                raise NestError("Body must be an object: {parts, sheet_width, sheet_height, ...}")
            doc = PartsDoc.from_json(json.dumps(data.get("parts")))
            rotations = data.get("rotations", [0, 90])
            if isinstance(rotations, str):
                rotations = parse_rotations(rotations)
            inp = NestInput(
                sheet_width=data.get("sheet_width", 1200.0),
                sheet_height=data.get("sheet_height", 800.0),
                gap=data.get("gap", 5.0),
                margin=data.get("margin", 10.0),
                rotations=tuple(float(r) for r in rotations),
            )
            result = nest(doc, inp)
        except (TypeError, ValueError, *INPUT_ERRORS) as exc:
            return jsonify(error="invalid_input", message=str(exc)), 422
        return jsonify(
            parts=json.loads(result.doc.to_json()),
            sheet_count=result.sheet_count,
            utilisation=result.utilisation,
        )

    # ---- /export ---------------------------------------------------------
    @hops.component(
        "/export",
        name="ExportCutFiles",
        nickname="Export",
        description="Write parts as DXF (R12) or SVG cut files, one per sheet, layers CUT/SCORE/ETCH/LABEL",
        inputs=[
            hs.HopsString("Parts", "P", "Parts document from Waffle Slice or Nest"),
            hs.HopsString("Format", "F", "dxf or svg"),
            hs.HopsString("Name", "N", "File name stem; files are <name>-sheet<n>.<format>"),
            hs.HopsString("Folder", "D", "Folder inside your home folder; empty = ~/Documents/CardboardCAD"),
            hs.HopsBoolean("Write", "W", "Set True (e.g. with a Toggle) to write the files"),
        ],
        outputs=[
            hs.HopsString("Paths", "Paths", "Files written (empty while Write is False)", hs.HopsParamAccess.LIST),
            hs.HopsString("Text", "Text", "File contents, one per sheet", hs.HopsParamAccess.LIST),
        ],
        **brand,
    )
    def export(P, F="dxf", N="cardboardcad", D="", W=False):
        doc = PartsDoc.from_json(P)
        out = export_files(doc, fmt=F, name=N, folder=D, write=bool(W), home=_home())
        return out.paths, out.texts
