# CardboardCAD Live listener for Grasshopper.
#
# Paste this whole file into a Grasshopper Python script component
# (Rhino 8: Maths > Script > Python 3 Script; Rhino 7: Maths > Script > GhPython).
# Then give the component these inputs and outputs (zoom in on the component
# and use the + / - buttons, then right-click each one to rename it):
#
#   Inputs:  On       (bool, optional; default True)   live updates on or off
#            Interval (int, optional; default 500)     how often to check, in ms
#            Port     (int, optional; default 5001)    the CardboardCAD engine port
#   Outputs: Version  the app's design version (connect to LiveDesign's V input)
#            Status   what the listener is doing
#
# It asks the CardboardCAD app (http://127.0.0.1:<Port>/api/design/version)
# for its design version on a background thread, and re-runs only when the
# version changes, so Grasshopper does no work while the design is unchanged.
# Full setup: docs/RHINO_LIVE_SYNC.md in the folding-grasshoppers repository.
#
# Works with Rhino 8 (Python 3) and Rhino 7 (IronPython 2.7): no f-strings.

import json
import threading

try:
    from urllib.request import urlopen
except ImportError:  # IronPython 2.7 (Rhino 7)
    from urllib2 import urlopen

DEFAULT_PORT = 5001
DEFAULT_INTERVAL_MS = 500
MIN_INTERVAL_MS = 100
MAX_INTERVAL_MS = 60000
TIMEOUT_S = 0.5
SCHEDULE_DELAY_MS = 5


def version_url(port):
    return "http://127.0.0.1:%d/api/design/version" % int(port)


def poll_version(port=DEFAULT_PORT, opener=None, timeout=TIMEOUT_S):
    """The app's design version, or None when the app is not running."""
    opener = opener or urlopen
    try:
        reply = opener(version_url(port), timeout=timeout)
        try:
            return int(json.loads(reply.read().decode("utf-8"))["version"])
        finally:
            close = getattr(reply, "close", None)
            if close is not None:
                close()
    except Exception:
        return None


def clamp_interval(ms):
    if ms is None:
        return DEFAULT_INTERVAL_MS
    try:
        ms = int(ms)
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_MS
    return max(MIN_INTERVAL_MS, min(MAX_INTERVAL_MS, ms))


class VersionWatcher(object):
    """Calls ``on_change(version)`` when the polled version changes.

    A dropped poll (the app busy or restarting) is not a change: firing on
    reconnects re-ran Grasshopper while Hops was still waiting for an answer.
    """

    def __init__(self, poll, on_change):
        self.poll = poll
        self.on_change = on_change
        self.version = None
        self.connected = False

    def check(self):
        v = self.poll()
        if v is None:
            self.connected = False
            return False
        self.connected = True
        if v != self.version:
            self.version = v
            self.on_change(v)
            return True
        return False

    def start(self, interval_ms, stop):
        """Poll on a daemon thread until ``stop`` (a threading.Event) is set."""
        delay = clamp_interval(interval_ms) / 1000.0

        def run():
            while not stop.is_set():
                try:
                    self.check()
                except Exception:
                    self.connected = False
                stop.wait(delay)

        t = threading.Thread(target=run, name="CardboardCAD live listener")
        t.daemon = True
        t.start()
        return t


def _stop(entry):
    if entry is not None:
        entry["stop"].set()


def run_in_grasshopper(ghenv, on, interval, port):
    """One solve of the Grasshopper component. Returns (Version, Status)."""
    import Rhino
    import scriptcontext as sc
    import System
    import Grasshopper

    comp = ghenv.Component
    key = "cardboardcad_live_" + str(comp.InstanceGuid)
    entry = sc.sticky.get(key)
    port = int(port) if port else DEFAULT_PORT
    interval = clamp_interval(interval)

    if on is False:
        _stop(entry)
        sc.sticky.pop(key, None)
        last = entry["watcher"].version if entry else None
        return (last if last is not None else -1), "Live updates are off (On is False)."

    if (entry is None or entry["port"] != port or entry["interval"] != interval
            or not entry["thread"].is_alive()):
        _stop(entry)
        stop = threading.Event()
        watcher = VersionWatcher(lambda: poll_version(port), lambda v: None)
        watcher.check()  # first answer now, without re-running this component

        pending = {"scheduled": False}

        def expire_in_solution(doc):
            # Runs at the start of the scheduled solution: marking this
            # component expired makes that solution recompute it and
            # everything downstream (LiveDesign), without cancelling anything.
            pending["scheduled"] = False
            comp.ExpireSolution(False)

        def schedule():
            doc = comp.OnPingDocument()
            if doc is None:  # the component was deleted
                stop.set()
                return
            if pending["scheduled"]:
                return  # one scheduled solution picks up every change before it
            pending["scheduled"] = True
            doc.ScheduleSolution(SCHEDULE_DELAY_MS, Grasshopper.Kernel.GH_Document.GH_ScheduleDelegate(expire_in_solution))

        def on_change(v):
            # Never ExpireSolution(True) from outside: that can cancel a solution
            # in progress, such as Hops waiting for the app.
            Rhino.RhinoApp.InvokeOnUiThread(System.Action(schedule))

        watcher.on_change = on_change
        thread = watcher.start(interval, stop)
        entry = {"stop": stop, "watcher": watcher, "thread": thread, "port": port, "interval": interval}
        sc.sticky[key] = entry

    w = entry["watcher"]
    if w.version is None:
        return -1, "Waiting for CardboardCAD on port %d. Is the app open?" % port
    if not w.connected:
        return w.version, "CardboardCAD is not answering on port %d; showing design version %d." % (port, w.version)
    return w.version, "Live: CardboardCAD design version %d (checking every %d ms)." % (w.version, interval)


if "ghenv" in globals():  # inside a Grasshopper script component
    _g = globals()
    Version, Status = run_in_grasshopper(ghenv, _g.get("On"), _g.get("Interval"), _g.get("Port"))  # noqa: F821
