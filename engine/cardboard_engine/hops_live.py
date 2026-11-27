"""Live link between the app's design and Rhino / Grasshopper (Hops).

Hops only calls the engine when one of its inputs changes, so a Hops
component cannot watch the design on its own. The listener script
(``assets/rhino_live_listener.py``, in a Grasshopper Python component)
polls ``/api/design/version`` and outputs the version number; wiring that
into **LiveDesign** makes Hops fetch the design each time it changes.

**SendToFoldTheory** (URL ``/send_to_cardboardcad``, kept from the old name) goes the other way: a Rhino mesh becomes the app's
model, so the designer can model in Rhino and slice in the app.

See ``docs/RHINO_LIVE_SYNC.md`` for the Grasshopper setup.
"""

from __future__ import annotations

import ghhops_server as hs

from .components.geometry import Mesh
from .design import DesignSession
from .hops_geometry import mesh_from_rhino, polyline_curve


def _rhino_mesh(model: dict):
    import rhino3dm

    m = rhino3dm.Mesh()
    for x, y, z in model["vertices"]:
        m.Vertices.Add(x, y, z)
    for f in model["faces"]:
        if len(f) == 4:
            m.Faces.AddFace(f[0], f[1], f[2], f[3])
        else:
            m.Faces.AddFace(f[0], f[1], f[2])
    m.Normals.ComputeNormals()
    m.Compact()
    return m


def _shifted(poly, origin):
    ox, oy = origin
    return [(p[0] + ox, p[1] + oy, 0.0) for p in poly]


def _closed(poly) -> bool:
    return len(poly) > 2 and tuple(poly[0]) == tuple(poly[-1])


def live_geometry(live: dict):
    """The live payload as Hops outputs (rhino3dm objects)."""
    models = [_rhino_mesh(live["model"])] if live["model"] else []
    parts3d = [polyline_curve(loop) for p in live["parts3d"] for loop in p["loops"]]
    cut, score, outlines, labels = [], [], [], []
    for sheet in live["sheets"]:
        o = sheet["origin"]
        w, h = sheet["size"]
        outlines.append(polyline_curve(_shifted([(0, 0), (w, 0), (w, h), (0, h)], o)))
        for part in sheet["parts"]:
            labels.append(part["label"])
            cut += [polyline_curve(_shifted(poly, o)) for poly in part["cut"]]
            score += [polyline_curve(_shifted(poly, o), closed=_closed(poly))
                      for poly in (*part["score"], *part["etch"])]
    return models, parts3d, cut, score, outlines, labels


def register(hops: hs.Hops, session: DesignSession, *, category: str, subcategory: str, icon: str) -> None:
    brand = dict(category=category, subcategory=subcategory, icon=icon)

    @hops.component(
        "/live_design",
        name="LiveDesign",
        nickname="Live",
        description="The design open in the Fold Theory app, live. Connect the Version output of the "
                    "Fold Theory Live listener so it updates whenever the app's design changes",
        inputs=[hs.HopsInteger("Version", "V", "Design version from the listener; any change fetches the design")],
        outputs=[
            hs.HopsInteger("Version", "V", "Version of the design these outputs show"),
            hs.HopsMesh("Model", "M", "The 3D model (mm)", hs.HopsParamAccess.LIST),
            hs.HopsCurve("Parts 3D", "R3", "Rib or layer outlines in place (the assembly)", hs.HopsParamAccess.LIST),
            hs.HopsCurve("Cut curves", "Cut", "Cut outlines on the sheets, laid side by side on XY",
                         hs.HopsParamAccess.LIST),
            hs.HopsCurve("Score curves", "Sc", "Score and etch lines on the sheets", hs.HopsParamAccess.LIST),
            hs.HopsCurve("Sheet outlines", "Sh", "One rectangle per sheet of board", hs.HopsParamAccess.LIST),
            hs.HopsString("Labels", "L", "Part labels, sheet by sheet", hs.HopsParamAccess.LIST),
            hs.HopsString("Parts", "P", "Parts document: connect to Nest or Export"),
            hs.HopsString("Summary", "S", "What the design is: size, method, sheets, cost"),
        ],
        **brand,
    )
    def live_design(V=0):
        live = session.live()
        models, parts3d, cut, score, outlines, labels = live_geometry(live)
        return live["version"], models, parts3d, cut, score, outlines, labels, live["parts"] or "", live["summary"]

    @hops.component(
        "/send_to_cardboardcad",
        name="SendToFoldTheory",
        nickname="Send",
        description="Send a Rhino mesh to the Fold Theory app as its model (replaces the app's model and parts)",
        inputs=[
            hs.HopsMesh("Mesh", "M", "Closed mesh in mm (use Mesh Brep first for a Brep)"),
            hs.HopsString("Name", "N", "Name the app shows for the model"),
            hs.HopsBoolean("Send", "S", "Set True (e.g. with a Button or Toggle) to send"),
        ],
        outputs=[
            hs.HopsInteger("Version", "V", "The app's design version after sending"),
            hs.HopsString("Status", "S", "What happened"),
        ],
        **brand,
    )
    def send_to_cardboardcad(M, N="rhino model", S=False):
        if not S:
            return session.version, "Not sent. Set Send to True to send this mesh to Fold Theory."
        mesh: Mesh = mesh_from_rhino(M)
        name = (N or "rhino model").strip()[:80] or "rhino model"
        version = session.push_model(mesh, name)
        return version, f"Sent {name} ({len(mesh.faces)} faces) to Fold Theory."
