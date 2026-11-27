#! python3
"""Fold Theory for Rhino 8: the app's design as real Rhino objects, edits flow back.

Run this once in Rhino 8's Script Editor (Tools > Script Editor, or type
``ScriptEditor``) with the Fold Theory app open. It:

* draws the app's design on layers ``Fold Theory::<project>::Model``,
  ``Assembly``, ``Parts`` (``Cut``, ``Score``, ``Etch``, ``Labels``) and
  ``Sheets``, every object tagged with user text (``foldtheory.id``...),
  and replaces them in place whenever the app's design changes;
* sends your edits back about a second after you stop editing:
  - change the model (move points, scale, SubD, a new mesh on the Model
    layer) and the app re-runs its recipe: slice, nest, cost;
  - reshape a flat part (move a slot, draw a hole on the Cut layer inside
    the part) and it becomes a hand edit (override) the app keeps.

Run the script again to stop. Everything stays on this Mac (127.0.0.1).
Setup, with pictures: docs/RHINO_TWO_WAY.md in the folding-grasshoppers repo.

The top half (no Rhino needed) is tested in CI: engine/tests/test_rhino_two_way.py.
The Rhino half runs only inside Rhino.
"""

import json
import threading
import time
import urllib.error
import urllib.request

PORT = 5001          # the port in the app's Settings
POLL_S = 0.5         # how often to ask the app whether the design changed
QUIET_S = 1.0        # send an edit this long after the last change to it
TIMEOUT_S = 3.0      # for asking; sending an edit waits longer (the app re-slices)
SEND_TIMEOUT_S = 120.0

ID_KEY = "foldtheory.id"
KIND_KEY = "foldtheory.kind"
PART_KEY = "foldtheory.part"
ROLE_KEY = "foldtheory.role"
REV_KEY = "foldtheory.revision"
VERSION_KEY = "foldtheory.version"
FRAME_KEY = "foldtheory.frame"
SHAPE_KEY = "foldtheory.shape"
EDITED = "edited"    # revision of an object edited here: the next scene replaces it
STICKY_KEY = "foldtheory.link"
ROLES = ("cut", "score", "etch")


# ---------------------------------------------------------------------------
# Rhino-free logic (tested in CI)
# ---------------------------------------------------------------------------


def base_url(port):
    """Loopback only: the script never talks to another computer."""
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port must be a whole number from 1 to 65535")
    return "http://127.0.0.1:%d" % port


class Client:
    def __init__(self, port=PORT):
        self.base = base_url(port)

    def _call(self, path, body=None, timeout=TIMEOUT_S):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                msg = json.loads(exc.read().decode("utf-8")).get("message")
            except ValueError:
                msg = None
            raise RuntimeError(msg or "the app answered %d" % exc.code) from None

    def scene(self, since=None):
        return self._call("/api/rhino/scene" + ("" if since is None else "?since=%d" % since))

    def send_model(self, body):
        return self._call("/api/rhino/model", body, timeout=SEND_TIMEOUT_S)

    def send_part(self, body):
        return self._call("/api/rhino/part", body, timeout=SEND_TIMEOUT_S)


class SyncPlan:
    def __init__(self):
        self.add, self.replace, self.delete, self.keep = [], [], [], []


def sync_key(obj):
    """What must match for a Rhino object to be up to date: its revision, and its layer when known."""
    key = obj["revision"]
    if obj.get("layer"):
        key += "|" + obj["layer"]
    return key


def plan_sync(have, incoming):
    """``have``: {id: sync key} of the tagged objects in Rhino. ``incoming``: the scene's objects."""
    plan = SyncPlan()
    seen = set()
    for obj in incoming:
        oid = obj["id"]
        seen.add(oid)
        if oid not in have:
            plan.add.append(obj)
        elif have[oid] != sync_key(obj):
            plan.replace.append(obj)
        else:
            plan.keep.append(oid)
    plan.delete = sorted(oid for oid in have if oid not in seen)
    return plan


class Debouncer:
    """Collect edits per key; a key is due once it has been quiet for ``quiet_s``."""

    def __init__(self, quiet_s=QUIET_S, now=time.monotonic):
        self.quiet_s, self.now, self._last = quiet_s, now, {}

    def touch(self, key):
        self._last[key] = self.now()

    def pending(self, key):
        return key in self._last

    def due(self):
        t = self.now()
        keys = sorted((k for k, at in self._last.items() if t - at >= self.quiet_s), key=self._last.get)
        for k in keys:
            del self._last[k]
        return keys


def owner_for_curve(bbox, parts):
    """The part a newly drawn curve belongs to: the smallest part whose outline box holds it."""
    (x0, y0), (x1, y1) = bbox
    best, best_area = None, None
    for pid, ((px0, py0), (px1, py1)) in parts.items():
        if px0 <= x0 and py0 <= y0 and x1 <= px1 and y1 <= py1:
            area = (px1 - px0) * (py1 - py0)
            if best_area is None or area < best_area:
                best, best_area = pid, area
    return best


def part_edit_body(pid, frame, base_shape, curves):
    """POST /api/rhino/part body from a part's curves: [(role, [[x, y], ...] in mm, closed)]."""
    body = {"id": pid, "frame": frame, "base_shape": base_shape, "cut": [], "score": [], "etch": []}
    for role, pts, closed in curves:
        pts = [[float(p[0]), float(p[1])] for p in pts]
        if closed and len(pts) > 1 and pts[0] == pts[-1]:
            pts = pts[:-1]
        if role in ROLES:
            body[role].append(pts)
    return body


def mesh_body(vertices, faces, to_mm=1.0, base_revision=None, name=None):
    """POST /api/rhino/model body. Rhino stores a triangle as a quad with its last corner repeated."""
    out_faces = []
    for f in faces:
        f = [int(i) for i in f]
        if len(f) == 4 and f[2] == f[3]:
            f = f[:3]
        out_faces.append(f)
    body = {"mesh": {"vertices": [[float(c) * to_mm for c in v[:3]] for v in vertices], "faces": out_faces}}
    if base_revision:
        body["base_revision"] = base_revision
    if name:
        body["name"] = name
    return body


# ---------------------------------------------------------------------------
# Rhino side (RhinoCommon; runs only inside Rhino 8)
# ---------------------------------------------------------------------------


class FoldTheoryLink:
    """Keeps one Rhino document in step with the app. All document changes happen on Rhino's UI thread."""

    def __init__(self, doc, port=PORT):
        self.doc = doc
        self.client = Client(port)
        self.seen = None          # scene version drawn in Rhino
        self.latest = None        # newest scene fetched by the poll thread
        self.results = []         # answers to edits sent, to apply on the UI thread
        self.errors = []
        self.lock = threading.Lock()
        self.debounce = Debouncer()
        self.applying = False
        self.running = False
        self.sending = 0
        self.root = None
        self.warned = False
        # pythonnet removes an event handler only when given the same delegate object back.
        self.handlers = (self.on_idle, self.on_replace, self.on_add, self.on_delete)

    # ---- start / stop ------------------------------------------------------
    def start(self):
        import Rhino

        self.running = True
        threading.Thread(target=self._poll, name="foldtheory-poll", daemon=True).start()
        idle, replace, add, delete = self.handlers
        Rhino.RhinoApp.Idle += idle
        Rhino.RhinoDoc.ReplaceRhinoObject += replace
        Rhino.RhinoDoc.AddRhinoObject += add
        Rhino.RhinoDoc.DeleteRhinoObject += delete
        self.say("connected on port %d. Run the script again to stop." % PORT)

    def stop(self):
        import Rhino

        self.running = False
        idle, replace, add, delete = self.handlers
        Rhino.RhinoApp.Idle -= idle
        Rhino.RhinoDoc.ReplaceRhinoObject -= replace
        Rhino.RhinoDoc.AddRhinoObject -= add
        Rhino.RhinoDoc.DeleteRhinoObject -= delete
        self.say("stopped. The objects stay in Rhino.")

    def say(self, text):
        import Rhino

        Rhino.RhinoApp.WriteLine("Fold Theory: " + text)

    # ---- background: ask the app, send edits ---------------------------------
    def _poll(self):
        while self.running:
            try:
                sc = self.client.scene(self.seen)
                if sc.get("changed"):
                    with self.lock:
                        self.latest = sc
                self.warned = False
            except Exception as exc:  # the app is not running, or restarting
                if not self.warned:
                    with self.lock:
                        self.errors.append("waiting for the Fold Theory app on port %d (%s)" % (PORT, exc))
                    self.warned = True
            time.sleep(POLL_S)

    def _send(self, kind, key, body):
        try:
            out = self.client.send_model(body) if kind == "model" else self.client.send_part(body)
            with self.lock:
                self.results.append((kind, key, out))
        except Exception as exc:
            with self.lock:
                self.errors.append("could not send the %s edit: %s" % (key, exc))
        finally:
            with self.lock:
                self.sending -= 1

    # ---- UI thread ---------------------------------------------------------------
    def on_idle(self, sender, e):
        with self.lock:
            sc, self.latest = self.latest, None
            results, self.results = self.results, []
            errors, self.errors = self.errors, []
        for msg in errors:
            self.say(msg)
        for kind, key, out in results:
            self._after_send(kind, key, out)
        for key in self.debounce.due():
            self._send_edit(key)
        if sc is not None:
            if self.sending:
                with self.lock:  # an edit is on its way: draw the scene that answers it
                    self.latest = self.latest or sc
            else:
                self.apply_scene(sc)

    def _tags(self, obj):
        a = obj.Attributes
        return a.GetUserString(ID_KEY), a.GetUserString(KIND_KEY)

    def on_replace(self, sender, e):
        self._edited(e.NewRhinoObject)

    def on_add(self, sender, e):
        self._edited(e.TheObject)

    def on_delete(self, sender, e):
        self._edited(e.TheObject, deleted=True)

    def _edited(self, obj, deleted=False):
        if self.applying or obj is None or self.root is None:
            return
        oid, kind = self._tags(obj)
        layer = self.doc.Layers[obj.Attributes.LayerIndex].FullPath
        if oid is None and not deleted:
            if layer == self.root + "::Model":
                oid, kind = "model", "model"
            elif layer.startswith(self.root + "::Parts::") and layer.split("::")[-1].lower() in ROLES:
                oid = self._adopt(obj, layer.split("::")[-1].lower())
                kind = "part" if oid else None
        if kind == "model":
            self.debounce.touch("model")
        elif kind == "part" and oid:
            self.debounce.touch(oid)
        elif kind in ("assembly", "sheet") and not deleted:
            self.say("only the Model and Parts layers go back to the app; this edit stays in Rhino only.")

    def _adopt(self, obj, role):
        """Tag a curve drawn on a Parts layer with the part it was drawn inside."""
        import Rhino

        if not isinstance(obj.Geometry, Rhino.Geometry.Curve):
            return None
        bb = obj.Geometry.GetBoundingBox(True)
        boxes, sample = {}, {}
        for o in self._tagged():
            if o.Attributes.GetUserString(ROLE_KEY) != "cut":
                continue
            pid = o.Attributes.GetUserString(ID_KEY)
            b = o.Geometry.GetBoundingBox(True)
            lo, hi = boxes.get(pid, ((b.Min.X, b.Min.Y), (b.Max.X, b.Max.Y)))
            boxes[pid] = ((min(lo[0], b.Min.X), min(lo[1], b.Min.Y)), (max(hi[0], b.Max.X), max(hi[1], b.Max.Y)))
            sample[pid] = o
        owner = owner_for_curve(((bb.Min.X, bb.Min.Y), (bb.Max.X, bb.Max.Y)), boxes)
        if owner is None:
            self.say("draw new part curves inside a part's outline so they can be added to it.")
            return None
        src = sample[owner].Attributes
        attrs = obj.Attributes.Duplicate()
        for key in (ID_KEY, PART_KEY, FRAME_KEY, SHAPE_KEY, VERSION_KEY):
            attrs.SetUserString(key, src.GetUserString(key))
        attrs.SetUserString(KIND_KEY, "part")
        attrs.SetUserString(ROLE_KEY, role)
        attrs.SetUserString(REV_KEY, EDITED)
        self.applying = True
        try:
            self.doc.Objects.ModifyAttributes(obj, attrs, True)
        finally:
            self.applying = False
        return owner

    # ---- sending edits -------------------------------------------------------------
    def _to_mm(self):
        import Rhino

        return Rhino.RhinoMath.UnitScale(self.doc.ModelUnitSystem, Rhino.UnitSystem.Millimeters)

    def _tagged(self):
        """Every Fold Theory object in the document, hidden and locked ones too."""
        import Rhino

        es = Rhino.DocObjects.ObjectEnumeratorSettings()
        es.HiddenObjects = True
        es.LockedObjects = True
        es.DeletedObjects = False
        es.ReferenceObjects = False
        return [o for o in self.doc.Objects.GetObjectList(es)
                if o.Attributes.GetUserString(ID_KEY) and not o.IsDeleted]

    def _objects(self, oid):
        return [o for o in self._tagged() if o.Attributes.GetUserString(ID_KEY) == oid]

    def _model_layer_objects(self):
        idx = self.doc.Layers.FindByFullPath(self.root + "::Model", -1)
        if idx < 0:
            return []
        return [o for o in self.doc.Objects.FindByLayer(self.doc.Layers[idx]) if not o.IsDeleted]

    def _send_edit(self, key):
        if key == "model":
            body = self._model_body()
        else:
            body = self._part_body(key)
        if body is None:
            return
        with self.lock:
            self.sending += 1
        self.say("sending your %s edit to the app..." % ("model" if key == "model" else key.split(":", 1)[1]))
        threading.Thread(target=self._send, args=("model" if key == "model" else "part", key, body),
                         daemon=True).start()

    def _model_body(self):
        import Rhino

        G = Rhino.Geometry
        joined = G.Mesh()
        rev = None
        for o in self._model_layer_objects():
            g = o.Geometry
            rev = rev or o.Attributes.GetUserString(REV_KEY)
            if isinstance(g, G.Extrusion):
                g = g.ToBrep()
            if isinstance(g, G.Mesh):
                joined.Append(g)
            elif isinstance(g, G.Brep):
                for m in G.Mesh.CreateFromBrep(g, G.MeshingParameters.Default) or []:
                    joined.Append(m)
            elif isinstance(g, G.SubD):
                joined.Append(G.Mesh.CreateFromSubD(g, 3))
        if joined.Faces.Count == 0:
            self.say("the Model layer has no mesh, Brep or SubD, so nothing was sent.")
            return None
        verts = [(v.X, v.Y, v.Z) for v in joined.Vertices]
        faces = [(f.A, f.B, f.C, f.D) for f in joined.Faces]
        return mesh_body(verts, faces, to_mm=self._to_mm(), base_revision=rev if rev != EDITED else None)

    def _part_body(self, oid):
        import Rhino

        objs = self._objects(oid)
        curves, frame, shape = [], None, None
        mm = self._to_mm()
        for o in objs:
            a = o.Attributes
            role = a.GetUserString(ROLE_KEY)
            frame = frame or a.GetUserString(FRAME_KEY)
            shape = shape or a.GetUserString(SHAPE_KEY)
            g = o.Geometry
            if role not in ROLES or not isinstance(g, Rhino.Geometry.Curve):
                continue
            ok, pl = g.TryGetPolyline()
            if not ok:
                tol = self.doc.ModelAbsoluteTolerance
                pc = g.ToPolyline(tol, self.doc.ModelAngleToleranceRadians, 0, 0)
                pl = [pc.Point(i) for i in range(pc.PointCount)]
            curves.append((role, [(p.X * mm, p.Y * mm) for p in pl], g.IsClosed))
        if frame is None:
            return None
        if not any(r == "cut" for r, _, _ in curves):
            self.say("%s has no cut outline left, so the edit was not sent." % oid)
            return None
        return part_edit_body(oid.split(":", 1)[1], json.loads(frame), shape, curves)

    def _after_send(self, kind, key, out):
        self.say(out.get("message", "sent."))
        if kind == "model" and out.get("revisions", {}).get("model"):
            # The app now has this model: keep your Rhino object (SubD, Brep...) instead of redrawing it.
            # Every object on the Model layer is now "the model", so it is not drawn a second time.
            self.applying = True
            try:
                for o in self._model_layer_objects():
                    attrs = o.Attributes.Duplicate()
                    attrs.SetUserString(ID_KEY, "model")
                    attrs.SetUserString(KIND_KEY, "model")
                    attrs.SetUserString(REV_KEY, out["revisions"]["model"])
                    self.doc.Objects.ModifyAttributes(o, attrs, True)
            finally:
                self.applying = False
        elif kind == "part":
            self._retag(key, REV_KEY, EDITED)

    def _retag(self, oid, key, value):
        self.applying = True
        try:
            for o in self._objects(oid):
                attrs = o.Attributes.Duplicate()
                attrs.SetUserString(key, value)
                self.doc.Objects.ModifyAttributes(o, attrs, True)
        finally:
            self.applying = False

    # ---- drawing the scene -------------------------------------------------------------
    def _layer(self, path):
        import Rhino

        idx = self.doc.Layers.FindByFullPath(path, -1)
        if idx >= 0:
            return idx  # existing layers keep their visibility, lock and colour
        parent_id = None
        names = path.split("::")
        for i in range(len(names)):
            sub = "::".join(names[: i + 1])
            idx = self.doc.Layers.FindByFullPath(sub, -1)
            if idx < 0:
                layer = Rhino.DocObjects.Layer()
                layer.Name = names[i]
                if parent_id is not None:
                    layer.ParentLayerId = parent_id
                idx = self.doc.Layers.Add(layer)
            parent_id = self.doc.Layers[idx].Id
        return idx

    def _have(self):
        have = {}
        for o in self._tagged():
            oid = o.Attributes.GetUserString(ID_KEY)
            rev = o.Attributes.GetUserString(REV_KEY) or ""
            layer = self.doc.Layers[o.Attributes.LayerIndex].FullPath
            if o.Attributes.GetUserString(KIND_KEY) == "part":
                layer = layer.rsplit("::", 1)[0]  # parts sit on Parts::Cut and friends
            key = rev + "|" + layer if rev != EDITED else EDITED
            if have.get(oid) != EDITED:
                have[oid] = key
        return have

    def apply_scene(self, sc):
        self.root = sc["root"]
        plan = plan_sync(self._have(), sc["objects"])
        busy = [o for o in plan.add + plan.replace if self.debounce.pending(o["id"])]
        self.applying = True
        try:
            for oid in plan.delete + [o["id"] for o in plan.replace if o not in busy]:
                for o in self._objects(oid):
                    self.doc.Objects.Delete(o, True)
            for obj in plan.add + plan.replace:
                if obj not in busy:
                    self._draw(obj)
        finally:
            self.applying = False
        self.seen = None if busy else sc["version"]  # a skipped object is drawn on the next poll
        for notice in sc.get("notices", [])[-3:]:
            self.say(notice)
        self.doc.Views.Redraw()

    def _attrs(self, obj, layer, role=None):
        import Rhino

        a = Rhino.DocObjects.ObjectAttributes()
        a.LayerIndex = self._layer(layer)
        a.SetUserString(ID_KEY, obj["id"])
        a.SetUserString(KIND_KEY, obj["kind"])
        a.SetUserString(REV_KEY, obj["revision"])
        a.SetUserString(VERSION_KEY, str(obj["version"]))
        if obj.get("part"):
            a.SetUserString(PART_KEY, obj["part"])
        if role:
            a.SetUserString(ROLE_KEY, role)
        if obj["kind"] == "part":
            a.SetUserString(FRAME_KEY, json.dumps(obj["frame"]))
            a.SetUserString(SHAPE_KEY, obj["shape"])
        return a

    def _draw(self, obj):
        import Rhino

        G = Rhino.Geometry
        s = Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Millimeters, self.doc.ModelUnitSystem)

        def pt(p):
            return G.Point3d(p[0] * s, p[1] * s, (p[2] if len(p) > 2 else 0.0) * s)

        def poly(pts, closed):
            pts = [pt(p) for p in pts]
            if closed and pts:
                pts.append(pts[0])
            return G.PolylineCurve(pts)

        kind, add = obj["kind"], self.doc.Objects
        if kind == "model":
            m = G.Mesh()
            for v in obj["mesh"]["vertices"]:
                m.Vertices.Add(v[0] * s, v[1] * s, v[2] * s)
            for f in obj["mesh"]["faces"]:
                m.Faces.AddFace(*f)
            m.Normals.ComputeNormals()
            m.Compact()
            add.AddMesh(m, self._attrs(obj, obj["layer"]))
        elif kind == "assembly":
            for loop in obj["loops"]:
                add.AddCurve(poly(loop, True), self._attrs(obj, obj["layer"]))
        elif kind == "sheet":
            add.AddCurve(poly(obj["outline"], True), self._attrs(obj, obj["layer"]))
        elif kind == "part":
            ids = []
            for role in ROLES:
                for pts in obj[role]:  # cut loops come open; closed score lines repeat their first point
                    ids.append(add.AddCurve(poly(pts, role == "cut"),
                                            self._attrs(obj, obj["layer"] + "::" + role.title(), role)))
            plane = G.Plane(pt(obj["label_at"]), G.Vector3d.ZAxis)
            ids.append(add.AddText(obj["label"], plane, 8.0 * s, "Arial", False, False,
                                   self._attrs(obj, obj["layer"] + "::Labels", "label")))
            self.doc.Groups.Add(ids)  # click one curve of a part to select the whole part


def main():
    import Rhino
    import scriptcontext

    link = scriptcontext.sticky.get(STICKY_KEY)
    if link is not None:
        link.stop()
        del scriptcontext.sticky[STICKY_KEY]
        return
    link = FoldTheoryLink(Rhino.RhinoDoc.ActiveDoc, PORT)
    scriptcontext.sticky[STICKY_KEY] = link
    link.start()


if __name__ == "__main__":
    main()
