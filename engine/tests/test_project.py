"""Saving a design as a project folder, and opening it again.

A project is a plain folder (easy to sync with Google Drive or OneDrive):

    ~/Documents/Fold Theory/Projects/<name>/
        project.json            manifest: name, recipe, summary, file list
        chat.json               the Design Assistant conversation (from the app)
        model/model.obj         the 3D model
        parts/parts.json        cardboardcad.parts/1 (nested)
        cut-files/<stem>-sheet<N>.dxf / .svg
        estimate/estimate.json, estimate.csv
        preview/model.svg       a picture of the design
"""

import csv
import json
import os

import pytest

from cardboard_engine.design import TOOLS, DesignSession
from cardboard_engine.project import (
    MANIFEST,
    PROJECT_SCHEMA,
    ProjectError,
    default_projects_dir,
    model_svg,
    safe_project_name,
)
from cardboard_engine.components.mesh_io import parse_obj, to_obj
from tests.meshes import box


@pytest.fixture
def home(tmp_path):
    return tmp_path


@pytest.fixture
def session(home):
    return DesignSession(home=str(home))


def run(session, tool_name, **args):
    r = session.run_tool(tool_name, args)
    assert not r.is_error, r.text
    return r.text


def build_stool(session):
    run(session, "create_shape", shape="cylinder", width_mm=350, depth_mm=350, height_mm=450, segments=24)
    run(session, "waffle_slice", x_ribs=3, y_ribs=3, thickness_mm=6)
    run(session, "nest_sheets", sheet_width_mm=1200, sheet_height_mm=800)
    run(session, "estimate_cost", sheet_price=12.5, machine_rate=60)


def projects(home):
    return home / "Documents" / "Fold Theory" / "Projects"


# ---- names and folders ----------------------------------------------------


def test_default_projects_dir_is_in_documents(home):
    assert default_projects_dir(str(home)) == str(projects(home))


@pytest.mark.parametrize(
    "raw, clean",
    [
        ("Acme Expo Stand", "Acme Expo Stand"),
        ("  Acme / Expo: Stand A  ", "Acme - Expo - Stand A"),
        ("Fold Theory (concept B) & co.", "Fold Theory (concept B) & co"),
        ("../../etc", "etc"),
    ],
)
def test_project_names_are_safe_folder_names(raw, clean):
    assert safe_project_name(raw) == clean


@pytest.mark.parametrize("raw", ["", "   ", "..", "///"])
def test_empty_project_names_are_refused(raw):
    with pytest.raises(ProjectError):
        safe_project_name(raw)


# ---- OBJ writer -----------------------------------------------------------


def test_obj_round_trips_exactly():
    v, f = box(123.456789, 60.1, 40.000001)
    from cardboard_engine.components.geometry import Mesh

    mesh = Mesh.from_lists(v, f)
    back = parse_obj(to_obj(mesh))
    assert back.vertices == mesh.vertices
    assert back.faces == mesh.faces


# ---- save -----------------------------------------------------------------


def test_save_writes_the_standard_folder(session, home):
    build_stool(session)
    out = session.save_project(name="Acme Expo - Stool")
    folder = projects(home) / "Acme Expo - Stool"
    assert out["folder"] == str(folder)
    assert out["name"] == "Acme Expo - Stool"
    for rel in [
        MANIFEST,
        "model/model.obj",
        "parts/parts.json",
        "cut-files/Acme-Expo-Stool-sheet1.dxf",
        "cut-files/Acme-Expo-Stool-sheet1.svg",
        "estimate/estimate.json",
        "estimate/estimate.csv",
        "preview/model.svg",
    ]:
        assert (folder / rel).is_file(), rel
        assert rel in out["files"]
    assert sorted(out["files"]) == out["files"]


def test_manifest_describes_the_project(session, home):
    build_stool(session)
    session.save_project(name="Stool")
    m = json.loads((projects(home) / "Stool" / MANIFEST).read_text())
    assert m["schema"] == PROJECT_SCHEMA
    assert m["name"] == "Stool"
    assert m["units"] == "mm"
    assert m["engine_version"]
    assert m["saved_at"].endswith("Z")
    assert m["model"] == {"file": "model/model.obj", "name": "cylinder"}
    assert m["recipe"]["method"]["tool"] == "waffle_slice"
    assert m["recipe"]["method"]["input"]["thickness_mm"] == 6
    assert m["recipe"]["nest"]["sheet_width_mm"] == 1200
    assert m["recipe"]["estimate"]["sheet_price"] == 12.5
    assert m["summary"]["sheet_count"] >= 1
    assert m["summary"]["part_count"] == 6
    assert m["summary"]["total_cost"] > 0
    assert m["summary"]["size_mm"] == [350.0, 350.0, 450.0]


def test_estimate_csv_opens_in_a_spreadsheet(session, home):
    build_stool(session)
    session.save_project(name="Stool")
    rows = list(csv.reader((projects(home) / "Stool" / "estimate" / "estimate.csv").open()))
    assert rows[0] == ["item", "value", "unit"]
    items = {r[0]: r for r in rows[1:]}
    assert items["sheet_count"][1] == str(json.loads(
        (projects(home) / "Stool" / "estimate" / "estimate.json").read_text())["sheet_count"])
    assert items["total_cost"][2] == "currency"
    assert items["cut_length"][2] == "mm"


def test_model_only_project_has_no_parts_files(session, home):
    run(session, "create_shape", shape="box", width_mm=100, depth_mm=80, height_mm=60)
    out = session.save_project(name="Just a box")
    assert out["files"] == sorted([MANIFEST, "model/model.obj", "preview/model.svg"])


def test_saving_an_empty_design_is_refused(session):
    with pytest.raises(ProjectError, match="nothing to save"):
        session.save_project(name="Empty")


def test_chat_is_saved_when_given_and_kept_when_not(session, home):
    run(session, "create_shape", shape="box")
    chat = {"items": [{"kind": "user", "text": "make a box"}], "history": [{"role": "user", "content": "make a box"}]}
    session.save_project(name="Box", chat=chat)
    path = projects(home) / "Box" / "chat.json"
    assert json.loads(path.read_text()) == chat
    session.save_project(name="Box")  # e.g. Claude's save_project tool: no chat to give
    assert json.loads(path.read_text()) == chat


def test_resave_removes_files_it_no_longer_makes(session, home):
    build_stool(session)
    session.save_project(name="Stool")
    folder = projects(home) / "Stool"
    assert (folder / "parts" / "parts.json").exists()
    run(session, "create_shape", shape="box")
    out = session.save_project(name="Stool")
    assert not (folder / "parts" / "parts.json").exists()
    assert not list((folder / "cut-files").glob("*")) if (folder / "cut-files").exists() else True
    assert "parts/parts.json" not in out["files"]


def test_resave_keeps_files_the_designer_added(session, home):
    run(session, "create_shape", shape="box")
    session.save_project(name="Box")
    folder = projects(home) / "Box"
    (folder / "inspiration").mkdir()
    (folder / "inspiration" / "logo.png").write_bytes(b"png")
    (folder / "notes.txt").write_text("client wants blue")
    session.save_project(name="Box")
    assert (folder / "inspiration" / "logo.png").read_bytes() == b"png"
    assert (folder / "notes.txt").read_text() == "client wants blue"


def test_save_refuses_a_folder_that_is_not_a_project(session, home):
    run(session, "create_shape", shape="box")
    other = home / "Documents" / "Taxes"
    other.mkdir(parents=True)
    (other / "2026.pdf").write_bytes(b"x")
    with pytest.raises(ProjectError, match="not a Fold Theory project"):
        session.save_project(folder=str(other))
    assert sorted(os.listdir(other)) == ["2026.pdf"]


def test_save_into_an_empty_chosen_folder(session, home):
    run(session, "create_shape", shape="box")
    target = home / "Drive" / "Client A"
    target.mkdir(parents=True)
    out = session.save_project(folder="~/Drive/Client A")
    assert out["folder"] == str(target)
    assert out["name"] == "Client A"
    assert (target / MANIFEST).is_file()


def test_save_outside_home_is_refused(session, tmp_path):
    run(session, "create_shape", shape="box")
    with pytest.raises(ProjectError, match="home folder"):
        session.save_project(folder="/etc/cardboard")


def test_save_remembers_the_project_and_clean_state(session, home):
    run(session, "create_shape", shape="box")
    assert session.state()["project"] is None
    session.save_project(name="Box")
    p = session.state()["project"]
    assert p["name"] == "Box"
    assert p["folder"] == str(projects(home) / "Box")
    assert p["unsaved_changes"] is False
    run(session, "waffle_slice", x_ribs=2, y_ribs=2)
    assert session.state()["project"]["unsaved_changes"] is True


def test_reset_forgets_the_project(session):
    run(session, "create_shape", shape="box")
    session.save_project(name="Box")
    session.reset()
    assert session.state()["project"] is None


# ---- open -----------------------------------------------------------------


def test_open_rebuilds_the_same_design(session, home):
    build_stool(session)
    before = session.state()
    session.save_project(name="Stool", chat={"items": [], "history": []})

    fresh = DesignSession(home=str(home))
    out = fresh.open_project(str(projects(home) / "Stool"))
    assert out["name"] == "Stool"
    assert out["chat"] == {"items": [], "history": []}
    after = fresh.state()
    for key in ("model", "method", "method_summary", "nested", "sheet_size", "estimate"):
        assert after[key] == before[key], key
    assert [s["svg"] for s in after["sheets"]] == [s["svg"] for s in before["sheets"]]
    assert after["project"]["unsaved_changes"] is False
    assert after["steps"][-1]["tool"] == "open_project"


def test_open_accepts_the_manifest_path_and_tilde(session, home):
    run(session, "create_shape", shape="cone")
    session.save_project(name="Cone")
    fresh = DesignSession(home=str(home))
    out = fresh.open_project("~/Documents/Fold Theory/Projects/Cone/project.json")
    assert out["folder"] == str(projects(home) / "Cone")
    assert out["chat"] is None
    assert fresh.state()["model"]["name"] == "cone"


def test_open_bumps_the_version(session, home):
    run(session, "create_shape", shape="box")
    session.save_project(name="Box")
    fresh = DesignSession(home=str(home))
    v = fresh.version
    assert fresh.open_project(str(projects(home) / "Box"))["version"] > v


def test_open_refuses_folders_that_are_not_projects(session, home):
    (home / "Documents").mkdir()
    with pytest.raises(ProjectError, match="not a Fold Theory project"):
        session.open_project(str(home / "Documents"))
    with pytest.raises(ProjectError, match="home folder"):
        session.open_project("/etc")


def test_open_refuses_a_broken_manifest(session, home):
    folder = home / "Broken"
    folder.mkdir()
    (folder / MANIFEST).write_text("{nope")
    with pytest.raises(ProjectError, match="project.json"):
        session.open_project(str(folder))
    (folder / MANIFEST).write_text(json.dumps({"schema": "something-else"}))
    with pytest.raises(ProjectError, match="project.json"):
        session.open_project(str(folder))


def test_open_reports_a_bad_recipe_step_and_keeps_the_current_design(session, home):
    run(session, "create_shape", shape="box")
    session.save_project(name="Box")
    folder = projects(home) / "Box"
    m = json.loads((folder / MANIFEST).read_text())
    m["recipe"] = {"method": {"tool": "waffle_slice", "input": {"x_ribs": -4}}}
    (folder / MANIFEST).write_text(json.dumps(m))
    other = DesignSession(home=str(home))
    run(other, "create_shape", shape="sphere")
    with pytest.raises(ProjectError, match="x_ribs"):
        other.open_project(str(folder))
    assert other.state()["model"]["name"] == "sphere"


def test_open_refuses_a_recipe_tool_that_is_not_a_design_step(session, home):
    run(session, "create_shape", shape="box")
    session.save_project(name="Box")
    folder = projects(home) / "Box"
    m = json.loads((folder / MANIFEST).read_text())
    m["recipe"] = {"method": {"tool": "export_cut_files", "input": {}}}
    (folder / MANIFEST).write_text(json.dumps(m))
    with pytest.raises(ProjectError, match="method"):
        DesignSession(home=str(home)).open_project(str(folder))


def test_opening_after_a_save_of_a_scaled_loaded_model(session, home):
    model = home / "rhino" / "stand.obj"
    model.parent.mkdir()
    v, f = box(1, 0.5, 2)
    model.write_text("".join(f"v {x} {y} {z}\n" for x, y, z in v) + "".join(
        "f " + " ".join(str(i + 1) for i in face) + "\n" for face in f))
    run(session, "load_model_file", path=str(model))
    run(session, "scale_model", factor=1000)
    run(session, "contour_stack", thickness_mm=100)
    session.save_project(name="Stand")
    model.unlink()  # the Rhino export can be gone; the project has its own copy
    fresh = DesignSession(home=str(home))
    fresh.open_project(str(projects(home) / "Stand"))
    s = fresh.state()
    assert s["model"]["name"] == "stand.obj"
    assert s["model"]["size_mm"] == [1000.0, 500.0, 2000.0]
    assert s["method"] == "contour"


# ---- the assistant can save too -----------------------------------------


def test_save_project_is_a_design_tool(session, home):
    assert "save_project" in {t["name"] for t in TOOLS}
    run(session, "create_shape", shape="box")
    text = run(session, "save_project", name="Concept A")
    assert str(projects(home) / "Concept A") in text
    assert (projects(home) / "Concept A" / MANIFEST).is_file()
    bad = session.run_tool("save_project", {"folder": "/etc/x"})
    assert bad.is_error and "home folder" in bad.text


# ---- preview picture ------------------------------------------------------


def test_model_svg_is_a_picture_of_the_triangles():
    v, f = box(100, 60, 40)
    svg = model_svg([tuple(tuple(v[i]) for i in face) for face in f])
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert svg.count("<polygon") == 12
    assert "viewBox" in svg


def test_model_svg_of_nothing_is_still_an_svg():
    svg = model_svg([])
    assert svg.startswith("<svg") and "<polygon" not in svg


# ---- HTTP -----------------------------------------------------------------


def test_save_and_open_routes(client, tmp_path):
    client.application.config["DESIGN"].home = str(tmp_path)
    client.post("/api/design/tool", json={"name": "create_shape", "input": {"shape": "box"}})
    r = client.post("/api/design/save", json={"name": "Route Box", "chat": {"items": [1]}})
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["folder"] == str(projects(tmp_path) / "Route Box")
    client.post("/api/design/reset")
    r = client.post("/api/design/open", json={"folder": body["folder"]})
    assert r.status_code == 200, r.data
    opened = r.get_json()
    assert opened["chat"] == {"items": [1]}
    assert opened["name"] == "Route Box"
    assert client.get("/api/design/state").get_json()["model"]["name"] == "box"


def test_save_and_open_route_errors_are_422(client, tmp_path):
    client.application.config["DESIGN"].home = str(tmp_path)
    assert client.post("/api/design/save", json={"name": "x"}).status_code == 422
    assert client.post("/api/design/save", json=[1]).status_code == 400
    assert client.post("/api/design/save", json={"name": 3}).status_code == 400
    r = client.post("/api/design/open", json={"folder": str(tmp_path)})
    assert r.status_code == 422
    assert "not a Fold Theory project" in r.get_json()["message"]
    assert client.post("/api/design/open", json={}).status_code == 400
