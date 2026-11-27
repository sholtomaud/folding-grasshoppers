"""Projects location (#49): save projects into a folder that Google Drive,
OneDrive or iCloud Drive already syncs, and cope with files changed by
another Mac or still syncing.

The location is kept per Mac in ``$ENGINE_DATA_DIR/locations.json`` (the
path differs between Macs: ``~/Library/CloudStorage/GoogleDrive-<account>``),
so it survives relaunches but is not itself synced.
"""

import json
import os

import pytest

from cardboard_engine.design import DesignSession
from cardboard_engine.project import (
    MANIFEST,
    ProjectChangedError,
    ProjectError,
    default_library_dir,
    default_projects_dir,
)


@pytest.fixture
def home(tmp_path):
    return tmp_path / "home"


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path / "app-support"


@pytest.fixture
def session(home, data_dir):
    home.mkdir()
    s = DesignSession(home=str(home))
    s.data_dir = str(data_dir)
    return s


def drive(home):
    """What Google Drive for desktop mounts on macOS 12.3 and later."""
    d = home / "Library" / "CloudStorage" / "GoogleDrive-tobias@example.com" / "My Drive"
    d.mkdir(parents=True, exist_ok=True)
    return d


def run(session, tool_name, **args):
    r = session.run_tool(tool_name, args)
    assert not r.is_error, r.text
    return r.text


def build(session):
    run(session, "create_shape", shape="box", width_mm=300, depth_mm=200, height_mm=150)
    run(session, "waffle_slice", x_ribs=2, y_ribs=2, thickness_mm=6)
    run(session, "nest_sheets", sheet_width_mm=1200, sheet_height_mm=800)


# ---- the setting ----------------------------------------------------------


def test_default_locations_are_in_documents(session, home):
    loc = session.locations()
    assert loc["projects"] == default_projects_dir(str(home))
    assert loc["library"] == default_library_dir(str(home))
    assert loc["library"].endswith(os.path.join("Documents", "Fold Theory", "Library"))
    assert loc["defaults"] == {"projects": loc["projects"], "library": loc["library"]}
    assert loc["is_default"] == {"projects": True, "library": True}


def test_set_projects_location_inside_a_synced_folder(session, home, data_dir):
    target = drive(home) / "Fold Theory" / "Projects"
    loc = session.set_locations(projects=str(target))
    assert loc["projects"] == str(target)
    assert loc["is_default"]["projects"] is False
    assert target.is_dir(), "the last folder is made when its parent exists"
    saved = json.loads((data_dir / "locations.json").read_text())
    assert saved["schema"] == "foldtheory.locations/1"
    assert saved["projects"] == str(target)


def test_location_accepts_tilde_and_survives_a_relaunch(session, home, data_dir):
    drive(home)
    session.set_locations(projects="~/Library/CloudStorage/GoogleDrive-tobias@example.com/My Drive/FT",
                          library="~/Library/CloudStorage/GoogleDrive-tobias@example.com/My Drive/FT Library")
    relaunched = DesignSession(home=str(home))
    relaunched.data_dir = str(data_dir)
    loc = relaunched.locations()
    assert loc["projects"] == str(drive(home) / "FT")
    assert loc["library"] == str(drive(home) / "FT Library")


def test_location_outside_home_is_refused(session, tmp_path):
    with pytest.raises(ProjectError, match="home folder"):
        session.set_locations(projects="/Volumes/Shared/Projects")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(ProjectError, match="home folder"):
        session.set_locations(library=str(outside))
    assert session.locations()["is_default"] == {"projects": True, "library": True}


def test_location_whose_parent_is_missing_is_refused(session, home):
    """A typo, or a Drive account that is not signed in on this Mac."""
    with pytest.raises(ProjectError, match="does not exist"):
        session.set_locations(projects=str(home / "Library" / "CloudStorage" / "GoogleDrive-x" / "My Drive" / "P"))


def test_location_that_is_a_file_or_a_project_is_refused(session, home):
    (home / "notes.txt").write_text("x")
    with pytest.raises(ProjectError, match="not a folder"):
        session.set_locations(projects=str(home / "notes.txt"))
    run(session, "create_shape", shape="box")
    out = session.save_project(name="Box")
    with pytest.raises(ProjectError, match="is a project"):
        session.set_locations(projects=out["folder"])


def test_empty_location_goes_back_to_the_default(session, home):
    session.set_locations(projects=str(drive(home) / "P"))
    loc = session.set_locations(projects="")
    assert loc["projects"] == default_projects_dir(str(home))
    assert loc["is_default"]["projects"] is True


def test_location_must_be_text(session):
    with pytest.raises(ProjectError, match="text"):
        session.set_locations(projects=3)


def test_unreadable_locations_file_falls_back_to_defaults(session, home, data_dir):
    data_dir.mkdir(parents=True)
    (data_dir / "locations.json").write_text("{nope")
    assert session.locations()["projects"] == default_projects_dir(str(home))
    (data_dir / "locations.json").write_text(json.dumps({"schema": "foldtheory.locations/1", "projects": "/etc"}))
    assert session.locations()["projects"] == default_projects_dir(str(home))


def test_without_a_data_dir_the_setting_lives_in_application_support(home):
    home.mkdir()
    s = DesignSession(home=str(home))
    s.set_locations(projects=str(drive(home) / "P"))
    assert (home / "Library" / "Application Support" / "Fold Theory" / "locations.json").is_file()


# ---- saving there ----------------------------------------------------------


def test_save_writes_into_the_projects_location(session, home, data_dir):
    target = drive(home) / "Fold Theory" / "Projects"
    session.set_locations(projects=str(target))
    build(session)
    out = session.save_project(name="Acme - Stool")
    assert out["folder"] == str(target / "Acme - Stool")
    assert (target / "Acme - Stool" / MANIFEST).is_file()
    assert (target / "Acme - Stool" / "cut-files").is_dir()

    relaunched = DesignSession(home=str(home))
    relaunched.data_dir = str(data_dir)
    opened = relaunched.open_project(out["folder"])
    assert opened["name"] == "Acme - Stool"
    assert relaunched.state()["method"] == "waffle"


def test_claudes_save_project_tool_uses_the_location_too(session, home):
    target = drive(home) / "P"
    session.set_locations(projects=str(target))
    run(session, "create_shape", shape="box")
    assert str(target / "Concept A") in run(session, "save_project", name="Concept A")


def test_a_chosen_folder_still_wins_over_the_location(session, home):
    session.set_locations(projects=str(drive(home) / "P"))
    run(session, "create_shape", shape="box")
    chosen = home / "Desktop" / "Box"
    chosen.mkdir(parents=True)
    assert session.save_project(folder=str(chosen))["folder"] == str(chosen)


# ---- files changed by another Mac ------------------------------------------


def _saved(session, home):
    session.set_locations(projects=str(drive(home) / "P"))
    build(session)
    return session.save_project(name="Stool", chat={"items": [], "history": []})["folder"]


def test_status_is_clean_right_after_saving_and_opening(session, home, data_dir):
    folder = _saved(session, home)
    st = session.project_status()
    assert st["folder"] == folder and st["project"] == "Stool"
    assert st["changed"] == [] and st["missing"] == [] and st["syncing"] == [] and st["conflicts"] == []
    assert st["outside_changes"] is False
    other = DesignSession(home=str(home))
    other.data_dir = str(data_dir)
    other.open_project(folder)
    assert other.project_status()["outside_changes"] is False


def test_status_without_a_project(session):
    st = session.project_status()
    assert st["project"] is None and st["outside_changes"] is False


def test_open_project_warns_when_its_files_change_underneath(session, home):
    folder = _saved(session, home)
    manifest = os.path.join(folder, MANIFEST)
    m = json.loads(open(manifest).read())
    m["saved_at"] = "2026-11-27T09:00:00Z"  # another Mac saved it
    with open(manifest, "w") as fh:
        fh.write(json.dumps(m, indent=2) + "\n")
    os.unlink(os.path.join(folder, "preview", "model.svg"))
    st = session.project_status()
    assert st["changed"] == [MANIFEST]
    assert st["missing"] == ["preview/model.svg"]
    assert st["outside_changes"] is True


def test_touching_a_file_without_changing_it_is_not_a_change(session, home):
    """Sync clients rewrite mtimes when they download; the content decides."""
    folder = _saved(session, home)
    path = os.path.join(folder, "model", "model.obj")
    os.utime(path, (1, 1))
    assert session.project_status()["outside_changes"] is False


def test_designer_added_files_are_not_changes(session, home):
    folder = _saved(session, home)
    with open(os.path.join(folder, "notes.txt"), "w") as fh:
        fh.write("client wants blue")
    assert session.project_status()["outside_changes"] is False


def test_sync_conflict_copies_are_reported(session, home):
    folder = _saved(session, home)
    for name in ("project (1).json", "project 2.json", "project-Tobias-MacBook.json", "projects.json"):
        with open(os.path.join(folder, name), "w") as fh:
            fh.write("{}")
    st = session.project_status()
    assert st["conflicts"] == ["project (1).json", "project 2.json", "project-Tobias-MacBook.json"]
    assert st["outside_changes"] is True


def test_icloud_placeholders_are_syncing_not_missing(session, home):
    folder = _saved(session, home)
    os.unlink(os.path.join(folder, "parts", "parts.json"))
    with open(os.path.join(folder, "parts", ".parts.json.icloud"), "wb") as fh:
        fh.write(b"bplist")
    st = session.project_status()
    assert st["syncing"] == ["parts/parts.json"]
    assert st["missing"] == []


def test_save_refuses_to_overwrite_changes_from_another_mac(session, home):
    folder = _saved(session, home)
    model = os.path.join(folder, "model", "model.obj")
    with open(model, "a") as fh:
        fh.write("# edited on the other Mac\n")
    with pytest.raises(ProjectChangedError, match="changed") as exc:
        session.save_project(name="Stool", folder=folder)
    assert exc.value.files == ["model/model.obj"]
    assert open(model).read().endswith("# edited on the other Mac\n"), "nothing was written"
    session.save_project(name="Stool", folder=folder, overwrite=True)
    assert not open(model).read().endswith("# edited on the other Mac\n")
    assert session.project_status()["outside_changes"] is False


def test_save_as_a_new_project_is_not_blocked_by_changes_in_the_old_one(session, home):
    folder = _saved(session, home)
    with open(os.path.join(folder, "model", "model.obj"), "a") as fh:
        fh.write("# edited\n")
    out = session.save_project(name="Stool v2")
    assert out["folder"].endswith("Stool v2")


def test_save_without_chat_ignores_a_chat_changed_elsewhere(session, home):
    """Claude's save_project tool sends no chat, so chat.json is left as it is."""
    folder = _saved(session, home)
    with open(os.path.join(folder, "chat.json"), "w") as fh:
        fh.write('{"items": [1]}\n')
    session.save_project(name="Stool", folder=folder)
    assert json.loads(open(os.path.join(folder, "chat.json")).read()) == {"items": [1]}


def test_reopening_shows_the_other_macs_changes(session, home, data_dir):
    folder = _saved(session, home)
    other = DesignSession(home=str(home))
    other.data_dir = str(data_dir)
    other.open_project(folder)
    run(other, "waffle_slice", x_ribs=4, y_ribs=2, thickness_mm=6)
    other.save_project(name="Stool", folder=folder)
    assert session.project_status()["outside_changes"] is True
    session.open_project(folder)
    assert session.project_status()["outside_changes"] is False
    m = json.loads(open(os.path.join(folder, MANIFEST)).read())
    assert m["recipe"]["method"]["input"]["x_ribs"] == 4


def test_opening_a_project_whose_model_is_still_in_icloud(session, home):
    folder = _saved(session, home)
    os.unlink(os.path.join(folder, "model", "model.obj"))
    with open(os.path.join(folder, "model", ".model.obj.icloud"), "wb") as fh:
        fh.write(b"bplist")
    with pytest.raises(ProjectError, match="still syncing"):
        DesignSession(home=str(home)).open_project(folder)


# ---- HTTP -----------------------------------------------------------------


@pytest.fixture
def http(client, tmp_path):
    s = client.application.config["DESIGN"]
    (tmp_path / "h").mkdir()
    s.home = str(tmp_path / "h")
    s.data_dir = str(tmp_path / "d")
    return client


def test_locations_routes(http, tmp_path):
    r = http.get("/api/locations")
    assert r.status_code == 200
    assert r.get_json()["is_default"]["projects"] is True
    target = drive(tmp_path / "h") / "P"
    r = http.post("/api/locations", json={"projects": str(target)})
    assert r.status_code == 200, r.data
    assert r.get_json()["projects"] == str(target)
    r = http.post("/api/locations", json={"projects": "/etc/x"})
    assert r.status_code == 422 and "home folder" in r.get_json()["message"]
    assert http.post("/api/locations", json=[1]).status_code == 400


def test_project_status_route_and_save_conflict_is_409(http, tmp_path):
    assert http.get("/api/design/project/status").get_json()["project"] is None
    http.post("/api/design/tool", json={"name": "create_shape", "input": {"shape": "box"}})
    body = http.post("/api/design/save", json={"name": "Box"}).get_json()
    with open(os.path.join(body["folder"], "model", "model.obj"), "a") as fh:
        fh.write("# x\n")
    st = http.get("/api/design/project/status").get_json()
    assert st["changed"] == ["model/model.obj"] and st["outside_changes"] is True
    r = http.post("/api/design/save", json={"name": "Box", "folder": body["folder"]})
    assert r.status_code == 409
    assert r.get_json()["error"] == "changed_on_disk"
    assert r.get_json()["files"] == ["model/model.obj"]
    r = http.post("/api/design/save", json={"name": "Box", "folder": body["folder"], "overwrite": True})
    assert r.status_code == 200, r.data
    assert http.post("/api/design/save", json={"name": "Box", "overwrite": "yes"}).status_code == 400
