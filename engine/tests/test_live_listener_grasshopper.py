"""The listener inside Grasshopper: it must never interrupt a running solution.

Calling ExpireSolution(True) from outside a solution can cancel one that is
running (for example Hops waiting for the app), so nothing ever arrives. The
listener instead asks Grasshopper to schedule a solution and expires itself
from that callback, which is the way Grasshopper supports for outside events.
"""

import importlib.util
import os
import sys
import threading
import types

import pytest

LISTENER = os.path.join(os.path.dirname(__file__), "..", "cardboard_engine", "assets", "rhino_live_listener.py")


def load_listener():
    spec = importlib.util.spec_from_file_location("rhino_live_listener_gh", LISTENER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeDoc:
    def __init__(self):
        self.scheduled = []

    def ScheduleSolution(self, delay_ms, callback):
        self.scheduled.append((delay_ms, callback))


class FakeComponent:
    def __init__(self, doc):
        self.doc = doc
        self.InstanceGuid = "guid-1"
        self.expired = []

    def OnPingDocument(self):
        return self.doc

    def ExpireSolution(self, recompute):
        self.expired.append(recompute)


@pytest.fixture
def gh(monkeypatch):
    sticky = {}
    ui_calls = []
    rhino = types.ModuleType("Rhino")
    rhino.RhinoApp = types.SimpleNamespace(InvokeOnUiThread=lambda action: ui_calls.append(action) or action())
    system = types.ModuleType("System")
    system.Action = lambda f: f
    kernel = types.SimpleNamespace(GH_Document=types.SimpleNamespace(GH_ScheduleDelegate=lambda f: f))
    grasshopper = types.ModuleType("Grasshopper")
    grasshopper.Kernel = kernel
    sc = types.ModuleType("scriptcontext")
    sc.sticky = sticky
    for name, mod in (("Rhino", rhino), ("System", system), ("Grasshopper", grasshopper), ("scriptcontext", sc)):
        monkeypatch.setitem(sys.modules, name, mod)
    doc = FakeDoc()
    comp = FakeComponent(doc)
    ghenv = types.SimpleNamespace(Component=comp)
    yield types.SimpleNamespace(sticky=sticky, doc=doc, comp=comp, ghenv=ghenv, ui_calls=ui_calls)
    for entry in sticky.values():
        entry["stop"].set()


def test_a_new_version_schedules_a_solution_instead_of_forcing_one(gh, monkeypatch):
    mod = load_listener()
    versions = {"v": 4}
    monkeypatch.setattr(mod, "poll_version", lambda port, **kw: versions["v"])
    version, status = mod.run_in_grasshopper(gh.ghenv, True, 100, 5001)
    assert version == 4 and status.startswith("Live")
    entry = next(iter(gh.sticky.values()))
    entry["watcher"].poll = lambda: 5
    entry["watcher"].check()
    assert gh.comp.expired == [], "never expire straight away from outside a solution"
    assert len(gh.doc.scheduled) == 1
    delay, callback = gh.doc.scheduled[0]
    assert delay >= 1
    callback(gh.doc)
    assert gh.comp.expired == [False], "expire inside the scheduled solution, without starting another"


def test_several_changes_before_the_solution_schedule_once(gh, monkeypatch):
    mod = load_listener()
    monkeypatch.setattr(mod, "poll_version", lambda port, **kw: 1)
    mod.run_in_grasshopper(gh.ghenv, True, 100, 5001)
    w = next(iter(gh.sticky.values()))["watcher"]
    for v in (2, 3, 4):
        w.poll = lambda v=v: v
        w.check()
    assert len(gh.doc.scheduled) == 1
    gh.doc.scheduled[0][1](gh.doc)
    w.poll = lambda: 5
    w.check()
    assert len(gh.doc.scheduled) == 2


def test_the_listener_does_not_rerun_when_the_app_comes_back_unchanged():
    mod = load_listener()
    versions = iter([4, None, 4, 4])
    changes = []
    w = mod.VersionWatcher(poll=lambda: next(versions), on_change=changes.append)
    for _ in range(4):
        w.check()
    assert changes == [4]
    assert w.connected is True


def test_off_stops_the_thread(gh, monkeypatch):
    mod = load_listener()
    monkeypatch.setattr(mod, "poll_version", lambda port, **kw: 7)
    mod.run_in_grasshopper(gh.ghenv, True, 100, 5001)
    entry = next(iter(gh.sticky.values()))
    version, status = mod.run_in_grasshopper(gh.ghenv, False, 100, 5001)
    assert version == 7 and "off" in status.lower()
    assert entry["stop"].is_set()
    entry["thread"].join(timeout=2)
    assert not entry["thread"].is_alive()
