"""Two-way Rhino link (issues #37 to #40).

The Fold Theory Rhino script (``assets/rhino_fold_theory.py``) keeps the
app's design in plain Rhino as tagged objects, and sends the designer's
edits back:

* ``GET /api/rhino/scene``: the design as objects with stable ids, a
  revision per object and the Rhino layer each belongs on (#37).
* ``POST /api/rhino/model``: an edited model replaces the app's model and
  the stored recipe runs again: slice, nest, cost (#38).
* ``POST /api/rhino/part``: an edited flat part becomes a manual override,
  kept through re-runs while it still fits and flagged when it does not (#39).
* Conflicts: the Rhino edit wins and the app says what it replaced; Rhino
  edits are handed to the Design Assistant as notes (#40).

Rhino itself is not available in CI: the script's Rhino-free logic
(debounce, sync planning, which part a new curve belongs to) is tested here,
and the RhinoCommon half is checked by hand (docs/RHINO_TWO_WAY.md).
"""

import importlib.util
import json
import os

import pytest

from cardboard_engine.components.export import to_dxf
from cardboard_engine.components.primitives import make_shape
from cardboard_engine.components.mesh_io import scale_mesh
from cardboard_engine import rhino_sync

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "cardboard_engine", "assets", "rhino_fold_theory.py")


# ---- helpers ---------------------------------------------------------------


def tool(client, tool_name, **args):
    body = client.post("/api/design/tool", json={"name": tool_name, "input": args}).get_json()
    assert not body["is_error"], body["content"]
    return body


def stool(client, x=5, y=5, nest=True, estimate=False):
    tool(client, "create_shape", shape="box", width_mm=300, depth_mm=300, height_mm=450)
    tool(client, "waffle_slice", x_ribs=x, y_ribs=y, thickness_mm=3)
    if nest:
        tool(client, "nest_sheets", sheet_width_mm=1200, sheet_height_mm=800)
    if estimate:
        tool(client, "estimate_cost", sheet_price=10, machine_rate=60)


def scene(client, since=None):
    q = "" if since is None else f"?since={since}"
    r = client.get("/api/rhino/scene" + q)
    assert r.status_code == 200, r.data
    return r.get_json()


def objects(sc, kind=None):
    return {o["id"]: o for o in sc["objects"] if kind is None or o["kind"] == kind}


def session(app):
    return app.config["DESIGN"]


def mesh_payload(mesh):
    return {"vertices": [list(v) for v in mesh.vertices], "faces": [list(f) for f in mesh.faces]}


def part_edit(obj, cut=None, score=None, etch=None, **extra):
    body = {"id": obj["part"], "frame": obj["frame"], "base_shape": obj["shape"],
            "cut": cut if cut is not None else obj["cut"],
            "score": score if score is not None else obj["score"],
            "etch": etch if etch is not None else obj["etch"]}
    body.update(extra)
    return body


def shifted_x(loop, dx, pick):
    """Move the points ``pick`` (indices) of a loop by dx."""
    return [[p[0] + dx, p[1]] if i in pick else list(p) for i, p in enumerate(loop)]


def slot_point_indices(loop):
    """Points of the first slot cut into the top of a rib outline (in sheet coordinates).

    A rib outline is a rectangle with notches. The slot points are the ones
    strictly inside the outline's bounding box in x (not on the outer left or
    right edge) and below its top: the walls and floor of the notches.
    """
    xs = [p[0] for p in loop]
    ys = [p[1] for p in loop]
    lo_x, hi_x, lo_y, hi_y = min(xs), max(xs), min(ys), max(ys)
    inner = [i for i, p in enumerate(loop) if lo_x + 1e-6 < p[0] < hi_x - 1e-6 and lo_y + 1e-6 < p[1] < hi_y - 1e-6]
    assert inner, "expected a notch in the outline"
    # The first notch: its floor points and the wall points next to them.
    first = sorted({i for i in inner})
    group = [first[0]]
    for i in first[1:]:
        if i == group[-1] + 1:
            group.append(i)
        else:
            break
    walls = {(group[0] - 1) % len(loop), (group[-1] + 1) % len(loop)}
    return set(group) | walls


# ---- #37: the scene, as tagged objects with stable ids ---------------------


def test_scene_of_an_empty_design(client):
    sc = scene(client)
    assert sc["changed"] is True
    assert sc["objects"] == []
    assert sc["root"] == "Fold Theory::Design"
    assert sc["units"] == "mm"


def test_ten_part_waffle_is_one_model_and_ten_parts_on_their_layers(client):
    stool(client)
    sc = scene(client)
    models = objects(sc, "model")
    parts = objects(sc, "part")
    assert list(models) == ["model"]
    assert sorted(parts) == sorted([f"part:X{i}" for i in range(1, 6)] + [f"part:Y{i}" for i in range(1, 6)])
    assert models["model"]["layer"] == "Fold Theory::Design::Model"
    for o in parts.values():
        assert o["layer"] == "Fold Theory::Design::Parts"
        assert o["part"] == o["id"].split(":", 1)[1]
        assert o["cut"] and all(len(p[0]) == 2 for p in o["cut"])
    assert {o["layer"] for o in objects(sc, "assembly").values()} == {"Fold Theory::Design::Assembly"}
    sheets = objects(sc, "sheet")
    assert sheets and {o["layer"] for o in sheets.values()} == {"Fold Theory::Design::Sheets"}
    for o in sc["objects"]:
        assert isinstance(o["revision"], str) and len(o["revision"]) >= 8
        assert o["version"] == sc["version"]


def test_scene_is_unchanged_since_its_own_version(client):
    stool(client)
    v = scene(client)["version"]
    assert scene(client, since=v) == {"version": v, "changed": False}
    assert client.get("/api/rhino/scene?since=x").status_code == 400


def test_reslicing_replaces_the_parts_and_keeps_the_model_revision(client):
    stool(client)
    before = scene(client)
    tool(client, "waffle_slice", x_ribs=4, y_ribs=3, thickness_mm=3)
    tool(client, "nest_sheets", sheet_width_mm=1200, sheet_height_mm=800)
    after = scene(client)
    assert len(objects(after, "part")) == 7
    assert "part:X5" not in objects(after) and "part:Y4" not in objects(after)
    assert objects(after)["model"]["revision"] == objects(before)["model"]["revision"]
    assert after["version"] > before["version"]


def test_revisions_are_stable_for_the_same_design(client, app):
    stool(client)
    a = {k: o["revision"] for k, o in objects(scene(client)).items()}
    tool(client, "model_info")  # changes the version, not the design
    b = {k: o["revision"] for k, o in objects(scene(client)).items()}
    assert a == b


def test_layer_root_is_the_project_name(client, tmp_path, app):
    session(app).home = str(tmp_path)
    stool(client)
    tool(client, "save_project", name="Acme Expo - Concept A")
    sc = scene(client)
    assert sc["root"] == "Fold Theory::Acme Expo - Concept A"
    assert objects(sc)["model"]["layer"] == "Fold Theory::Acme Expo - Concept A::Model"


def test_layer_names_drop_characters_rhino_cannot_use():
    assert rhino_sync.layer_root("a::b [c]") == "Fold Theory::a b c"
    assert rhino_sync.layer_root("") == "Fold Theory::Design"
    assert rhino_sync.layer_root(None) == "Fold Theory::Design"


def test_frame_maps_sheet_points_back_to_the_part(client, app):
    stool(client)
    s = session(app)
    for o in objects(scene(client), "part").values():
        local = next(p for p in s.parts.parts if p.id == o["part"])
        back = rhino_sync.to_local(o["frame"], o["cut"][0])
        assert len(back) == len(local.cut[0])
        for (x, y), (lx, ly) in zip(back, local.cut[0]):
            assert x == pytest.approx(lx, abs=1e-6) and y == pytest.approx(ly, abs=1e-6)


def test_frame_round_trip_with_rotation():
    frame = {"rotation": 90.0, "offset": [100.0, 50.0]}
    pts = [[0.0, 0.0], [10.0, 0.0], [10.0, 5.0]]
    sheet = rhino_sync.to_sheet(frame, pts)
    assert sheet[1] == pytest.approx([100.0, 60.0])
    assert rhino_sync.to_local(frame, sheet) == [pytest.approx(p) for p in pts]


# ---- #38: model edits flow back and re-run the recipe ----------------------


def test_scaled_model_from_rhino_reslices_renests_and_recosts(client, app):
    stool(client, estimate=True)
    s = session(app)
    old_cost = s.last_estimate["total_cost"]
    v0 = s.version
    taller = scale_mesh(s.model, 600 / 450)
    r = client.post("/api/rhino/model", json={"mesh": mesh_payload(taller)})
    assert r.status_code == 200, r.data
    body = r.get_json()
    (_, _, lz), (_, _, hz) = s.model.bbox()
    assert hz - lz == pytest.approx(600, abs=0.01)
    assert s.method == "waffle" and len(s.parts.parts) == 10
    assert s.nested is not None and all(p.placement is not None for p in s.parts.parts)
    assert s.last_estimate is not None and s.last_estimate["total_cost"] > old_cost
    assert s.version == v0 + 1, "one edit is one new version"
    assert body["version"] == s.version
    assert body["revisions"]["model"] == objects(scene(client))["model"]["revision"]
    assert "600" in body["message"]
    assert any(st["tool"] == "rhino_model_edit" and st["ok"] for st in s.steps)


def test_model_from_rhino_without_a_recipe_only_replaces_the_model(client, app):
    tool(client, "create_shape", shape="box", width_mm=200, depth_mm=200, height_mm=200)
    mesh = make_shape("cylinder", width=100, depth=100, height=300, segments=12)
    r = client.post("/api/rhino/model", json={"mesh": mesh_payload(mesh), "name": "column"})
    assert r.status_code == 200, r.data
    s = session(app)
    assert s.parts is None
    assert s.model_name == "column"
    assert len(s.model.faces) == len(mesh.faces)


def test_model_from_rhino_keeps_the_name_when_none_is_given(client, app):
    stool(client, nest=False)
    s = session(app)
    client.post("/api/rhino/model", json={"mesh": mesh_payload(scale_mesh(s.model, 1.1))})
    assert s.model_name == "box"


@pytest.mark.parametrize("body", [
    None,
    {},
    {"mesh": {"vertices": [], "faces": []}},
    {"mesh": {"vertices": [[0, 0, 0], [1, 0, 0]], "faces": [[0, 1, 5]]}},
    {"mesh": {"vertices": "x", "faces": []}},
    {"mesh": {"vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]], "faces": [[0, 1, 2]]}, "name": 3},
])
def test_bad_model_from_rhino_is_refused_and_the_design_kept(client, app, body):
    stool(client, nest=False)
    s = session(app)
    v = s.version
    r = client.post("/api/rhino/model", json=body)
    assert r.status_code in (400, 422)
    assert r.get_json()["error"] == "invalid_input"
    assert s.version == v and len(s.parts.parts) == 10


def test_model_edit_that_breaks_the_recipe_keeps_the_new_model_and_says_why(client, app):
    tool(client, "create_shape", shape="box", width_mm=300, depth_mm=300, height_mm=300)
    tool(client, "waffle_slice", x_ribs=5, y_ribs=5, thickness_mm=3)
    tool(client, "nest_sheets", sheet_width_mm=400, sheet_height_mm=400)
    s = session(app)
    big = scale_mesh(s.model, 3)  # ribs no longer fit a 400 mm sheet
    r = client.post("/api/rhino/model", json={"mesh": mesh_payload(big)})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is False
    assert "nest_sheets" in body["message"]
    (lx, _, _), (hx, _, _) = s.model.bbox()
    assert hx - lx == pytest.approx(900, abs=0.01)


def test_model_conflict_rhino_wins_and_the_app_says_so(client, app):
    stool(client, nest=False)
    old_rev = objects(scene(client))["model"]["revision"]
    tool(client, "scale_model", height_mm=500)  # the app changed the model since Rhino saw it
    tool(client, "waffle_slice", x_ribs=5, y_ribs=5, thickness_mm=3)
    s = session(app)
    edited = scale_mesh(make_shape("box", width=300, depth=300, height=450), 1.2)
    body = client.post("/api/rhino/model", json={"mesh": mesh_payload(edited), "base_revision": old_rev}).get_json()
    assert body["conflict"] is True
    (_, _, lz), (_, _, hz) = s.model.bbox()
    assert hz - lz == pytest.approx(540, abs=0.01)
    assert any("Rhino" in n and "model" in n for n in scene(client)["notices"])


# ---- #39: edits to flat parts become manual overrides ----------------------


def _moved_slot_x3(client, dx=20.0):
    x3 = objects(scene(client))["part:X3"]
    loop = x3["cut"][0]
    pick = slot_point_indices(loop)
    edited = [shifted_x(loop, dx, pick)] + x3["cut"][1:]
    return x3, edited, pick


def test_moving_a_slot_in_rhino_overrides_that_part(client, app):
    stool(client)
    s = session(app)
    x3, edited, pick = _moved_slot_x3(client)
    r = client.post("/api/rhino/part", json=part_edit(x3, cut=edited))
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["status"] == "applied"
    part = next(p for p in s.parts.parts if p.id == "X3")
    want = rhino_sync.to_local(x3["frame"], edited[0])
    got = part.cut[0]
    assert len(got) == len(want)
    for (gx, gy), (wx, wy) in zip(got, want):
        assert gx == pytest.approx(wx, abs=1e-6) and gy == pytest.approx(wy, abs=1e-6)
    # The other parts are untouched, and the scene says X3 is overridden.
    sc = scene(client)
    assert objects(sc)["part:X3"]["override"] == "applied"
    assert objects(sc)["part:X2"]["override"] is None
    assert [o["part"] for o in sc["overrides"]] == ["X3"]


def test_overridden_part_is_in_the_cut_files(client, app):
    stool(client)
    s = session(app)
    x3, edited, pick = _moved_slot_x3(client)
    client.post("/api/rhino/part", json=part_edit(x3, cut=edited))
    new_sc = objects(scene(client))["part:X3"]
    moved_point = new_sc["cut"][0][min(pick)]
    dxf = "".join(to_dxf(sheet) for sheet in s.parts.layout())
    lines = dxf.split("\n")
    xs = {round(float(lines[i + 1]), 3) for i, v in enumerate(lines) if v.strip() == "10"}
    sheet_x = moved_point[0] - next(o for o in scene(client)["objects"]
                                    if o["kind"] == "sheet" and o["index"] == new_sc["frame"]["sheet"])["outline"][0][0]
    assert round(sheet_x, 3) in xs


def test_override_survives_the_same_recipe_running_again(client, app):
    stool(client)
    x3, edited, _ = _moved_slot_x3(client)
    client.post("/api/rhino/part", json=part_edit(x3, cut=edited))
    body = tool(client, "waffle_slice", x_ribs=5, y_ribs=5, thickness_mm=3)
    assert "X3" in body["content"] and "kept" in body["content"]
    assert objects(scene(client))["part:X3"]["override"] == "applied"


def test_override_that_no_longer_fits_is_flagged_not_dropped(client, app):
    stool(client)
    s = session(app)
    x3, edited, _ = _moved_slot_x3(client)
    client.post("/api/rhino/part", json=part_edit(x3, cut=edited))
    body = tool(client, "waffle_slice", x_ribs=5, y_ribs=4, thickness_mm=3)  # X3's slots move
    assert "no longer fits" in body["content"] and "X3" in body["content"]
    sc = scene(client)
    assert objects(sc)["part:X3"]["override"] == "stale"
    assert sc["overrides"][0]["status"] == "stale"
    generated = s.generated["X3"]
    assert next(p for p in s.parts.parts if p.id == "X3").cut == generated.cut
    # Back to 5 Y ribs: X3 is the same part again, so the override comes back.
    tool(client, "waffle_slice", x_ribs=5, y_ribs=5, thickness_mm=3)
    assert objects(scene(client))["part:X3"]["override"] == "applied"


def test_override_kept_when_other_ribs_change_but_x3_does_not(client, app):
    stool(client)
    x3, edited, _ = _moved_slot_x3(client)
    client.post("/api/rhino/part", json=part_edit(x3, cut=edited))
    # On a box, more X ribs move X3 along X but do not change its shape.
    tool(client, "waffle_slice", x_ribs=7, y_ribs=5, thickness_mm=3)
    assert objects(scene(client))["part:X3"]["override"] == "applied"


def test_override_for_a_part_that_is_gone_is_flagged_missing(client, app):
    stool(client)
    x3, edited, _ = _moved_slot_x3(client)
    client.post("/api/rhino/part", json=part_edit(x3, cut=edited))
    body = tool(client, "waffle_slice", x_ribs=2, y_ribs=5, thickness_mm=3)
    assert "X3" in body["content"]
    assert scene(client)["overrides"][0]["status"] == "missing"


def test_moving_a_whole_part_is_not_an_override(client, app):
    stool(client)
    x2 = objects(scene(client))["part:X2"]
    moved = [[[x + 30, y + 12] for x, y in loop] for loop in x2["cut"]]
    body = client.post("/api/rhino/part", json=part_edit(x2, cut=moved)).get_json()
    assert body["status"] == "moved"
    assert "nest" in body["message"].lower()
    assert scene(client)["overrides"] == []


def test_adding_a_handle_hole_counts_in_the_cost(client, app):
    stool(client, estimate=True)
    s = session(app)
    before = s.last_estimate["cut_length_mm"]
    x3 = objects(scene(client))["part:X3"]
    xs = [p[0] for p in x3["cut"][0]]
    ys = [p[1] for p in x3["cut"][0]]
    cx, cy = (min(xs) + max(xs)) / 2, min(ys) + 60
    hole = [[cx - 40, cy - 12], [cx + 40, cy - 12], [cx + 40, cy + 12], [cx - 40, cy + 12]]
    body = client.post("/api/rhino/part", json=part_edit(x3, cut=x3["cut"] + [hole])).get_json()
    assert body["status"] == "applied"
    assert s.last_estimate["cut_length_mm"] == pytest.approx(before + 208, abs=0.01)
    assert "1 more hole" in body["message"]


def test_editing_a_part_back_to_the_generated_shape_clears_the_override(client, app):
    stool(client)
    x3, edited, _ = _moved_slot_x3(client)
    client.post("/api/rhino/part", json=part_edit(x3, cut=edited))
    now = objects(scene(client))["part:X3"]
    back = [rhino_sync.to_sheet(now["frame"], loop) for loop in session(app).generated["X3"].cut]
    body = client.post("/api/rhino/part", json=part_edit(now, cut=back)).get_json()
    assert body["status"] == "cleared"
    assert scene(client)["overrides"] == []


@pytest.mark.parametrize("change", [
    {"id": "Q9"},
    {"id": 5},
    {"frame": {"rotation": "x", "offset": [0, 0]}},
    {"frame": None},
    {"cut": []},
    {"cut": [[[0, 0], [1, 1]]]},
    {"score": "x"},
])
def test_bad_part_edits_are_refused(client, app, change):
    stool(client)
    x3 = objects(scene(client))["part:X3"]
    body = part_edit(x3)
    body.update(change)
    v = session(app).version
    r = client.post("/api/rhino/part", json=body)
    assert r.status_code in (400, 422), r.data
    assert session(app).version == v


def test_part_edit_without_parts_is_refused(client):
    tool(client, "create_shape", shape="box")
    r = client.post("/api/rhino/part", json={"id": "X1", "frame": {"rotation": 0, "offset": [0, 0]},
                                             "cut": [[[0, 0], [1, 0], [1, 1]]]})
    assert r.status_code == 422


def test_override_is_saved_in_the_project_and_reopened(client, app, tmp_path):
    session(app).home = str(tmp_path)
    stool(client)
    x3, edited, _ = _moved_slot_x3(client)
    client.post("/api/rhino/part", json=part_edit(x3, cut=edited))
    want = next(p for p in session(app).parts.parts if p.id == "X3").cut
    saved = client.post("/api/design/save", json={"name": "Stool"}).get_json()
    folder = saved["folder"]
    with open(os.path.join(folder, "project.json")) as fh:
        manifest = json.load(fh)
    assert [o["part"] for o in manifest["rhino_overrides"]] == ["X3"]
    client.post("/api/design/reset")
    assert scene(client)["overrides"] == []
    r = client.post("/api/design/open", json={"folder": folder})
    assert r.status_code == 200, r.data
    got = next(p for p in session(app).parts.parts if p.id == "X3").cut
    assert [[pytest.approx(c, abs=1e-5) for c in pt] for pt in want[0]] == [list(pt) for pt in got[0]]
    assert scene(client)["overrides"][0]["status"] == "applied"


def test_a_new_model_clears_overrides(client, app):
    stool(client)
    x3, edited, _ = _moved_slot_x3(client)
    client.post("/api/rhino/part", json=part_edit(x3, cut=edited))
    tool(client, "create_shape", shape="cylinder")
    assert scene(client)["overrides"] == []


def test_scaling_keeps_overrides_but_they_go_stale(client, app):
    stool(client)
    x3, edited, _ = _moved_slot_x3(client)
    client.post("/api/rhino/part", json=part_edit(x3, cut=edited))
    tool(client, "scale_model", factor=1.2)
    tool(client, "waffle_slice", x_ribs=5, y_ribs=5, thickness_mm=3)
    assert scene(client)["overrides"][0]["status"] == "stale"


# ---- #40: conflicts, and Claude knows about Rhino edits --------------------


def test_part_conflict_rhino_edit_wins_and_the_app_says_what_it_replaced(client, app):
    stool(client)
    x3, edited, _ = _moved_slot_x3(client)
    tool(client, "waffle_slice", x_ribs=5, y_ribs=5, thickness_mm=4)  # the app changed X3 meanwhile
    tool(client, "nest_sheets", sheet_width_mm=1200, sheet_height_mm=800)
    body = client.post("/api/rhino/part", json=part_edit(x3, cut=edited)).get_json()
    assert body["conflict"] is True
    assert body["status"] == "applied"
    assert any("X3" in n and "replaced" in n for n in scene(client)["notices"])
    part = next(p for p in session(app).parts.parts if p.id == "X3")
    want = rhino_sync.to_local(x3["frame"], edited[0])
    assert part.cut[0][0] == pytest.approx(tuple(want[0]), abs=1e-6)


def test_rhino_edits_become_notes_for_the_assistant_once(client, app):
    stool(client)
    x3, edited, _ = _moved_slot_x3(client)
    client.post("/api/rhino/part", json=part_edit(x3, cut=edited))
    notes = client.get("/api/design/notes").get_json()["notes"]
    assert len(notes) == 1 and "X3" in notes[0] and "Rhino" in notes[0]
    assert client.get("/api/design/notes").get_json()["notes"] == []


def test_model_info_lists_the_rhino_overrides(client):
    stool(client)
    x3, edited, _ = _moved_slot_x3(client)
    client.post("/api/rhino/part", json=part_edit(x3, cut=edited))
    text = tool(client, "model_info")["content"]
    assert "X3" in text and "Rhino" in text


def test_system_prompt_tells_claude_to_keep_rhino_edits():
    from cardboard_engine.design import SYSTEM_PROMPT

    assert "Rhino" in SYSTEM_PROMPT and "override" in SYSTEM_PROMPT and "ask" in SYSTEM_PROMPT


def test_clear_rhino_override_tool(client):
    stool(client)
    x3, edited, _ = _moved_slot_x3(client)
    client.post("/api/rhino/part", json=part_edit(x3, cut=edited))
    body = tool(client, "clear_rhino_override", part="X3")
    assert "X3" in body["content"]
    assert scene(client)["overrides"] == []
    bad = client.post("/api/design/tool", json={"name": "clear_rhino_override", "input": {"part": "Z1"}}).get_json()
    assert bad["is_error"]


def test_notices_are_in_the_app_state(client):
    stool(client)
    x3, edited, _ = _moved_slot_x3(client)
    client.post("/api/rhino/part", json=part_edit(x3, cut=edited))
    state = client.get("/api/design/state").get_json()
    assert state["rhino"]["overrides"][0]["part"] == "X3"
    assert state["rhino"]["notices"]


# ---- the Rhino script's own logic (no Rhino needed) ------------------------


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("rhino_fold_theory", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # must import without Rhino
    return mod


def test_script_is_served_for_the_app_to_copy(client):
    r = client.get("/rhino/fold_theory.py")
    assert r.status_code == 200
    assert r.headers["Content-Type"].startswith("text/x-python")
    with open(SCRIPT, "rb") as fh:
        assert r.data == fh.read()


def test_script_only_talks_to_loopback(script):
    assert script.base_url(5001) == "http://127.0.0.1:5001"
    with pytest.raises(ValueError):
        script.base_url(0)
    with pytest.raises(ValueError):
        script.base_url("5001; rm")


def test_plan_sync_adds_replaces_and_deletes(script):
    have = {"model": "r1", "part:X1": "a", "part:X2": "b", "part:X9": "z"}
    incoming = [{"id": "model", "revision": "r1"}, {"id": "part:X1", "revision": "a2"},
                {"id": "part:X2", "revision": "b"}, {"id": "part:Y1", "revision": "c"}]
    plan = script.plan_sync(have, incoming)
    assert [o["id"] for o in plan.add] == ["part:Y1"]
    assert [o["id"] for o in plan.replace] == ["part:X1"]
    assert plan.delete == ["part:X9"]
    assert plan.keep == ["model", "part:X2"]


def test_plan_sync_replaces_objects_marked_edited(script):
    plan = script.plan_sync({"part:X1": script.EDITED}, [{"id": "part:X1", "revision": "a"}])
    assert [o["id"] for o in plan.replace] == ["part:X1"]


def test_debouncer_turns_ten_quick_edits_into_one_send(script):
    clock = [0.0]
    d = script.Debouncer(quiet_s=1.0, now=lambda: clock[0])
    for i in range(10):
        d.touch("model")
        clock[0] += 0.2
        assert d.due() == []
    clock[0] += 1.0
    assert d.due() == ["model"]
    assert d.due() == []


def test_debouncer_keeps_keys_separate(script):
    clock = [0.0]
    d = script.Debouncer(quiet_s=1.0, now=lambda: clock[0])
    d.touch("part:X1")
    clock[0] = 0.8
    d.touch("model")
    clock[0] = 1.1
    assert d.due() == ["part:X1"]
    clock[0] = 1.9
    assert d.due() == ["model"]


def test_new_curve_belongs_to_the_part_it_is_drawn_in(script):
    parts = {"X1": ((0, 0), (100, 50)), "X2": ((110, 0), (200, 50)), "big": ((-10, -10), (300, 300))}
    assert script.owner_for_curve(((20, 10), (40, 20)), parts) == "X1"
    assert script.owner_for_curve(((120, 10), (130, 20)), parts) == "X2"
    assert script.owner_for_curve(((250, 250), (260, 260)), parts) == "big"
    assert script.owner_for_curve(((500, 500), (510, 510)), parts) is None


def test_part_edit_body_from_tagged_curves(script):
    frame = {"rotation": 0, "offset": [0, 0], "sheet": 0}
    curves = [("cut", [[0, 0], [10, 0], [10, 5], [0, 5], [0, 0]], True),
              ("score", [[1, 1], [9, 1]], False)]
    body = script.part_edit_body("X1", frame, "abc", curves)
    assert body == {"id": "X1", "frame": frame, "base_shape": "abc",
                    "cut": [[[0, 0], [10, 0], [10, 5], [0, 5]]], "score": [[[1, 1], [9, 1]]], "etch": []}


def test_mesh_body_scales_to_mm(script):
    body = script.mesh_body([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2, 2)], to_mm=10.0, base_revision="r")
    assert body["mesh"]["vertices"][1] == [10.0, 0.0, 0.0]
    assert body["mesh"]["faces"] == [[0, 1, 2]]
    assert body["base_revision"] == "r"
