"""The app is called Fold Theory (it used to be CardboardCAD).

Everything a designer reads says Fold Theory: the greeting, the Hops
components, the live listener, error messages and the default folder
~/Documents/Fold Theory. Hops URLs stay as they were (``/send_to_cardboardcad``)
so Grasshopper definitions saved before the rename keep working, and projects
saved by CardboardCAD still open.
"""

import json
import os

import pytest

from cardboard_engine import brand
from cardboard_engine.components.export import resolve_export_dir
from cardboard_engine.components.hello import greet
from cardboard_engine.design import DesignSession
from cardboard_engine.project import ProjectError, default_projects_dir, resolve_open_dir
from cardboard_engine.workflow import workflows_dir

ASSETS = os.path.join(os.path.dirname(__file__), "..", "cardboard_engine", "assets")


def test_brand_names():
    assert brand.APP_NAME == "Fold Theory"
    assert brand.LEGACY_APP_NAME == "CardboardCAD"


def test_greeting_says_fold_theory():
    assert greet("Mac") == "Hello, Mac, from Fold Theory"


def test_default_folders_are_in_documents_fold_theory(tmp_path):
    home = str(tmp_path)
    base = os.path.join(home, "Documents", "Fold Theory")
    assert brand.documents_dir(home) == base
    assert resolve_export_dir("", home=home) == base
    assert default_projects_dir(home) == os.path.join(base, "Projects")
    assert workflows_dir(home) == os.path.join(base, "Workflows")


def test_hops_components_say_fold_theory(client):
    listed = json.loads(client.get("/").data)
    assert listed
    for c in listed:
        m = json.loads(client.get(c["Uri"]).data)
        text = json.dumps(m)
        assert "CardboardCAD" not in text, c["Uri"]
        assert m["Category"] == "Fold Theory"


def test_send_component_keeps_its_url_but_has_the_new_name(client):
    uris = {c["Uri"] for c in json.loads(client.get("/").data)}
    assert "/send_to_cardboardcad" in uris
    m = json.loads(client.get("/send_to_cardboardcad").data)
    assert m["Name"] == "SendToFoldTheory"


def test_live_listener_and_preview_say_fold_theory():
    for name in ("rhino_live_listener.py", "preview.html"):
        with open(os.path.join(ASSETS, name), encoding="utf-8") as f:
            text = f.read()
        assert "CardboardCAD" not in text, name
        assert "Fold Theory" in text, name


def test_not_a_project_message_says_fold_theory(tmp_path):
    with pytest.raises(ProjectError, match="not a Fold Theory project"):
        resolve_open_dir(str(tmp_path), home=str(tmp_path))


def test_saved_project_manifest_names_fold_theory(tmp_path):
    session = DesignSession(home=str(tmp_path))
    assert session._manifest("Stool", [])["app"] == "Fold Theory"


def test_system_prompt_names_fold_theory():
    from cardboard_engine.design import SYSTEM_PROMPT

    assert "CardboardCAD" not in SYSTEM_PROMPT
    assert "Fold Theory" in SYSTEM_PROMPT
