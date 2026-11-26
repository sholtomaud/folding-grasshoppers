"""Hops wrappers and plain-JSON routes for the geometry components.

Only this module touches rhino3dm. The components themselves
(``components/waffle.py``, ``components/export.py``...) work on plain
Python lists, so they are tested without Rhino or HTTP.

Components chain through the ``Parts`` text output (a cardboardcad.parts/1
JSON document): Waffle Slice -> (Nest) -> Export.
"""

from __future__ import annotations

import os
from typing import Iterable, List, Sequence

import ghhops_server as hs
from flask import Flask, jsonify, request

from .components.export import ExportError, export_files
from .components.geometry import GeometryError, Mesh
from .components.parts import PartsDoc, PartsError
from .components.waffle import WaffleError, WaffleInput, waffle_slice

INPUT_ERRORS = (GeometryError, PartsError, WaffleError, ExportError)


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
