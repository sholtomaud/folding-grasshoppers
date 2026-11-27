"""Flask + ghhops_server HTTP engine.

Usage::

    python -I -B -u -m cardboard_engine.server --port 5001 --parent-pid <pid>

The shutdown token is read from the ``ENGINE_SHUTDOWN_TOKEN`` env var so it
never appears in ``ps``. The server binds to 127.0.0.1 only.

Exit codes: 0 clean shutdown, 2 bad arguments, 98 port in use.
"""

from __future__ import annotations

import argparse
import errno
import hmac
import json
import logging
import os
import socket
import sys
from importlib import metadata
from typing import Optional

sys.dont_write_bytecode = True  # the app bundle is signed and read-only

import ghhops_server as hs  # noqa: E402
from flask import Flask, jsonify, request  # noqa: E402

from . import __version__  # noqa: E402
from .components.cut_estimate import (  # noqa: E402
    CutEstimateError,
    CutEstimateInput,
    estimate,
)
from .components.hello import greet  # noqa: E402
from . import hops_geometry  # noqa: E402
from .design import SYSTEM_PROMPT, TOOLS, DesignSession  # noqa: E402,F401
from .workflow import WorkflowError  # noqa: E402
from .diagnostics import DiagnosticsLog  # noqa: E402
from .settings import SettingsError  # noqa: E402
from .views import ViewError  # noqa: E402
from .project import ProjectError  # noqa: E402
from . import hops_live  # noqa: E402
from .hops_activity import HopsActivity  # noqa: E402
from .lifecycle import DRAIN_TIMEOUT_S, InFlightTracker, Lifecycle  # noqa: E402

BIND_HOST = "127.0.0.1"  # never 0.0.0.0
DEFAULT_PORT = 5001
EXIT_PORT_IN_USE = 98

log = logging.getLogger("cardboard_engine")

# Hops shows components under Category > Subcategory, with this icon.
HOPS_CATEGORY = "Fold Theory"
HOPS_SUBCATEGORY = "Cardboard"
HOPS_ICON = "assets/foldtheory-24.png"  # relative to this package

REPORTED_PACKAGES = ("flask", "werkzeug", "ghhops-server", "rhino3dm")


def _package_versions() -> dict:
    out = {}
    for name in REPORTED_PACKAGES:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = None
    return out


def create_app(
    shutdown_token: Optional[str] = None,
    lifecycle: Optional[Lifecycle] = None,
) -> Flask:
    """Build the Flask app with Hops components registered.

    ``shutdown_token`` of None or "" disables ``/shutdown`` (always 403).
    """
    app = Flask("cardboard_engine")
    app.config["SHUTDOWN_TOKEN"] = shutdown_token or ""
    app.config["LIFECYCLE"] = lifecycle
    hops = hs.Hops(app)

    # ---- plain JSON routes ---------------------------------------------
    @app.get("/health")
    def health():
        return jsonify(status="healthy")

    @app.get("/version")
    def version():
        return jsonify(
            engine=__version__,
            python=sys.version,
            packages=_package_versions(),
        )

    @app.post("/shutdown")
    def shutdown():
        expected = app.config["SHUTDOWN_TOKEN"]
        given = request.headers.get("X-Engine-Token", "")
        if not expected or not hmac.compare_digest(
            given.encode("utf-8"), expected.encode("utf-8")
        ):
            return jsonify(error="forbidden"), 403
        lc: Optional[Lifecycle] = app.config["LIFECYCLE"]
        if lc is not None:
            # Small delay so this 202 is flushed before the server stops.
            lc.request_shutdown("POST /shutdown", delay=0.05)
        return jsonify(status="shutting_down"), 202

    @app.post("/api/cut_estimate")
    def api_cut_estimate():
        """Plain-JSON twin of the Hops component, for the Swift app."""
        data = request.get_json(silent=True)
        try:
            result = estimate(CutEstimateInput.from_mapping(data))
        except CutEstimateError as exc:
            return jsonify(error="invalid_input", message=str(exc)), 422
        return jsonify(result.to_dict())

    @app.get("/api/hops_activity")
    def api_hops_activity():
        """When Grasshopper (Hops) last called the engine, for the app's status bar."""
        return jsonify(app.config["HOPS_ACTIVITY"].snapshot())

    @app.post("/api/hello")
    def api_hello():
        """Plain-JSON hello-world demo for the Swift app."""
        data = request.get_json(silent=True) or {}
        name = data.get("name") if isinstance(data, dict) else None
        try:
            return jsonify(message=greet(name))
        except TypeError as exc:
            return jsonify(error="invalid_input", message=str(exc)), 422

    # ---- Hops components -----------------------------------------------
    @hops.component(
        "/hello",
        name="Hello",
        nickname="Hello",
        description="Hello-world demo: greets a name",
        category=HOPS_CATEGORY,
        subcategory=HOPS_SUBCATEGORY,
        icon=HOPS_ICON,
        inputs=[hs.HopsString("Name", "Name", "Who to greet")],
        outputs=[hs.HopsString("Message", "Msg", "The greeting")],
    )
    def hello(name: str = "world"):
        return greet(name)

    @hops.component(
        "/echo",
        name="Echo",
        nickname="Echo",
        description="Smoke test: returns the number it is given",
        category=HOPS_CATEGORY,
        subcategory=HOPS_SUBCATEGORY,
        icon=HOPS_ICON,
        inputs=[hs.HopsNumber("Number", "N", "Any number")],
        outputs=[hs.HopsNumber("Number", "N", "The same number")],
    )
    def echo(number: float):
        return number

    @hops.component(
        "/cut_estimate",
        name="CutEstimate",
        nickname="CutEst",
        description="Estimate cutting time and cost (mm, s)",
        category=HOPS_CATEGORY,
        subcategory=HOPS_SUBCATEGORY,
        icon=HOPS_ICON,
        inputs=[
            hs.HopsNumber("Cut length (mm)", "Lc", "Total cut length in mm"),
            hs.HopsNumber("Score length (mm)", "Ls", "Total score length in mm"),
            hs.HopsNumber("Pierce count", "N", "Number of pierces"),
            hs.HopsNumber("Sheet count", "S", "Number of sheets"),
            hs.HopsNumber("Cut speed (mm/s)", "Vc", "Cutting speed"),
            hs.HopsNumber("Score speed (mm/s)", "Vs", "Scoring speed"),
            hs.HopsNumber("Pierce time (s)", "Tp", "Time per pierce"),
            hs.HopsNumber("Travel overhead (fraction)", "k", "Extra time for travel moves"),
            hs.HopsNumber("Sheet price", "Ps", "Price per sheet"),
            hs.HopsNumber("Machine rate (per hour)", "R", "Machine cost per hour"),
        ],
        outputs=[
            hs.HopsNumber("Time (min)", "T", "Estimated machine time in minutes"),
            hs.HopsNumber("Material cost", "Cm", "Sheet count x sheet price"),
            hs.HopsNumber("Machine cost", "Cmach", "Machine time x rate"),
            hs.HopsNumber("Total cost", "C", "Material + machine cost"),
        ],
    )
    # Defaults live in the signature: ghhops_server copies them into the
    # Hops metadata (passing default=0 to HopsNumber would be dropped).
    def cut_estimate(
        Lc,
        Ls=0.0,
        N=0.0,
        S=1.0,
        Vc=20.0,
        Vs=40.0,
        Tp=0.5,
        k=0.15,
        Ps=0.0,
        R=0.0,
    ):
        # A raised exception becomes a Hops component error (red balloon).
        r = estimate(
            CutEstimateInput(
                cut_length=Lc,
                score_length=Ls,
                pierce_count=N,
                sheet_count=S,
                cut_speed=Vc,
                score_speed=Vs,
                pierce_time=Tp,
                travel_overhead=k,
                sheet_price=Ps,
                machine_rate=R,
            )
        )
        return r.time_min, r.material_cost, r.machine_cost, r.total_cost

    hops_geometry.register(
        app, hops, category=HOPS_CATEGORY, subcategory=HOPS_SUBCATEGORY, icon=HOPS_ICON
    )
    session = _register_design(app)
    hops_live.register(hops, session, category=HOPS_CATEGORY, subcategory=HOPS_SUBCATEGORY, icon=HOPS_ICON)

    # Outermost, so it also sees the requests ghhops_server answers itself.
    activity = HopsActivity(app.wsgi_app, is_hops_path=_hops_path_check(hops))
    app.wsgi_app = activity
    app.config["HOPS_ACTIVITY"] = activity
    return app


def _register_design(app: Flask) -> DesignSession:
    """Design Assistant routes: the app's chat runs tools here; /preview draws the result."""
    data_dir = os.environ.get("ENGINE_DATA_DIR")
    diagnostics = DiagnosticsLog(os.path.join(data_dir, "diagnostics") if data_dir else None)
    session = DesignSession(home=os.environ.get("HOME") or os.path.expanduser("~"), diagnostics=diagnostics)
    app.config["DESIGN"] = session
    preview_path = os.path.join(os.path.dirname(__file__), "assets", "preview.html")
    listener_path = os.path.join(os.path.dirname(__file__), "assets", "rhino_live_listener.py")

    @app.get("/api/design/tools")
    def design_tools():
        return jsonify(system=session.system_prompt(), tools=TOOLS)

    @app.post("/api/design/tool")
    def design_tool():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get("name"), str):
            return jsonify(error="invalid_input", message="Body must be {name, input}"), 400
        args = data.get("input", {})
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return jsonify(error="invalid_input", message="input must be an object"), 400
        result = session.run_tool(data["name"], args)
        out = dict(content=result.text, is_error=result.is_error, version=session.version,
                   setup_changed=result.setup_changed)
        if result.form is not None:
            out["form"] = result.form
        return jsonify(out)

    # ---- settings, forms and the quote -----------------------------------
    @app.get("/api/settings")
    def settings_list():
        """Every setting with its value and the layer it comes from."""
        return jsonify(session.settings.listing())

    @app.post("/api/settings")
    def settings_set():
        """Body: {key, value, scope: project|all, set_by?}. The Project view edits settings here."""
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get("key"), str):
            return jsonify(error="invalid_input", message="Body must be {key, value, scope}"), 400
        set_by = data.get("set_by") if isinstance(data.get("set_by"), str) else ""
        try:
            session.set_setting(data["key"], data.get("value"), scope=data.get("scope") or "project", set_by=set_by)
        except SettingsError as exc:
            return jsonify(error="invalid_input", message=str(exc)), 422
        return jsonify(session.settings.listing())

    @app.post("/api/settings/form")
    def settings_form():
        """The designer's answer to a request_inputs form. Body: {form_id, values, scope, set_by?}."""
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get("form_id"), str):
            return jsonify(error="invalid_input", message="Body must be {form_id, values, scope}"), 400
        set_by = data.get("set_by") if isinstance(data.get("set_by"), str) else ""
        out = session.submit_form(data["form_id"], data.get("values"), scope=data.get("scope") or "project",
                                  set_by=set_by)
        return jsonify(out), (200 if out["ok"] else 422)

    @app.get("/api/quote")
    def quote():
        """The last quote the engine worked out (lines, total, the settings and formulas it used)."""
        return jsonify(session.last_quote or {"lines": [], "total": None})

    @app.get("/api/design/numbers")
    def design_numbers():
        """Numbers the engine has shown, as text: the app checks Claude's replies against them."""
        return jsonify(numbers=session.numbers())

    # ---- views -----------------------------------------------------------
    @app.get("/api/views")
    def views_list():
        return jsonify(views=session.views.listing())

    @app.get("/api/views/<view_id>")
    def views_get(view_id):
        try:
            return jsonify(session.views.render(view_id))
        except ViewError as exc:
            return jsonify(error="not_found", message=str(exc)), 404

    @app.post("/api/views")
    def views_save():
        """Body: {view, scope: all|project}. A view that fails the schema check is rejected (422)."""
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error="invalid_input", message="Body must be {view, scope}"), 400
        try:
            out = session.views.save(data.get("view"), scope=data.get("scope") or "all",
                                     set_by=data.get("set_by") if isinstance(data.get("set_by"), str) else "")
        except ViewError as exc:
            return jsonify(error="invalid_input", message=str(exc)), 422
        return jsonify(out)

    @app.post("/api/views/<view_id>/undo")
    def views_undo(view_id):
        data = request.get_json(silent=True) or {}
        try:
            return jsonify(session.views.undo(view_id, scope=data.get("scope") or "all"))
        except ViewError as exc:
            return jsonify(error="invalid_input", message=str(exc)), 422

    # ---- diagnostics -----------------------------------------------------
    @app.get("/api/diagnostics")
    def diagnostics_read():
        """?source=&level=&since=<id>&limit=. Newest last."""
        args = request.args
        try:
            since = int(args["since"]) if args.get("since") else None
            limit = int(args.get("limit") or 200)
            entries = session.diagnostics.read(source=args.get("source") or None, level=args.get("level") or None,
                                               since_id=since, limit=limit)
        except ValueError as exc:
            return jsonify(error="invalid_input", message=str(exc)), 400
        return jsonify(entries=entries)

    @app.post("/api/diagnostics")
    def diagnostics_write():
        """Body: {source, level?, message, details?, design_version?}: Grasshopper, Rhino and the app report here."""
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error="invalid_input", message="Body must be {source, level, message}"), 400
        try:
            entry = session.diagnostics.write(data.get("source"), data.get("level") or "info", data.get("message"),
                                              design_version=data.get("design_version", session.version),
                                              details=data.get("details"))
        except ValueError as exc:
            return jsonify(error="invalid_input", message=str(exc)), 400
        return jsonify(entry)

    @app.get("/api/workflows")
    def workflows_list():
        """The saved workflows, the active one and whether apply defaults is on."""
        return jsonify(session.workflows.listing())

    @app.post("/api/workflows/settings")
    def workflows_settings():
        """Body: {active?, apply_defaults?}. Changes what the next Claude request is told."""
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error="invalid_input", message="Body must be {active, apply_defaults}"), 400
        active, apply = data.get("active"), data.get("apply_defaults")
        if active is not None and not isinstance(active, str):
            return jsonify(error="invalid_input", message="active must be text"), 400
        if apply is not None and not isinstance(apply, bool):
            return jsonify(error="invalid_input", message="apply_defaults must be true or false"), 400
        try:
            session.workflows.set_settings(active=active, apply_defaults=apply)
        except WorkflowError as exc:
            return jsonify(error="invalid_input", message=str(exc)), 422
        return jsonify(session.workflows.listing())

    @app.get("/api/design/version")
    def design_version():
        return jsonify(version=session.version)

    @app.get("/api/design/state")
    def design_state():
        return jsonify(session.state())

    @app.post("/api/design/reset")
    def design_reset():
        session.reset()
        return jsonify(version=session.version)

    @app.post("/api/design/save")
    def design_save():
        """Save the design as a project folder. Body: {name?, folder?, chat?}."""
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error="invalid_input", message="Body must be {name, folder, chat}"), 400
        name, folder = data.get("name") or "", data.get("folder") or ""
        if not isinstance(name, str) or not isinstance(folder, str):
            return jsonify(error="invalid_input", message="name and folder must be text"), 400
        try:
            out = session.save_project(folder=folder, name=name, chat=data.get("chat"))
        except ProjectError as exc:
            return jsonify(error="invalid_input", message=str(exc)), 422
        return jsonify(out)

    @app.post("/api/design/open")
    def design_open():
        """Open a project folder (or its project.json). Body: {folder}."""
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get("folder"), str) or not data["folder"]:
            return jsonify(error="invalid_input", message="Body must be {folder}"), 400
        try:
            out = session.open_project(data["folder"])
        except ProjectError as exc:
            return jsonify(error="invalid_input", message=str(exc)), 422
        return jsonify(out)

    @app.get("/api/design/live")
    def design_live():
        """The design as plain geometry, for Rhino. ?since=<version> answers {changed: false} when up to date."""
        since = request.args.get("since")
        if since is not None:
            try:
                since = int(since)
            except ValueError:
                return jsonify(error="invalid_input", message="since must be a whole number"), 400
        return jsonify(session.live(since))

    @app.get("/rhino/live_listener.py")
    def rhino_listener():
        """The Grasshopper listener script (see docs/RHINO_LIVE_SYNC.md)."""
        with open(listener_path, "rb") as fh:
            body = fh.read()
        return body, 200, {"Content-Type": "text/x-python; charset=utf-8", "Cache-Control": "no-store"}

    @app.get("/preview")
    def preview():
        with open(preview_path, "rb") as fh:
            body = fh.read()
        return body, 200, {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store"}

    return session


def _hops_path_check(hops):
    """Return a predicate for the paths ghhops_server answers (components, /solve, /)."""
    from types import SimpleNamespace

    def is_hops_path(path: str) -> bool:
        # HopsBase.handles() only looks at request.path.
        return bool(hops.handles(SimpleNamespace(path=path)))

    return is_hops_path


# ---------------------------------------------------------------------------
# process entry point
# ---------------------------------------------------------------------------


def _port_in_use(port: int) -> None:
    print(json.dumps({"error": "port_in_use", "port": port}), file=sys.stderr, flush=True)
    sys.exit(EXIT_PORT_IN_USE)


def _something_listening(port: int) -> bool:
    """Probe loopback for an existing listener.

    Needed because the server socket uses SO_REUSEADDR (so restarts are not
    blocked by TIME_WAIT), and on macOS/BSD that lets a bind to 127.0.0.1
    succeed even when another process listens on the wildcard address.
    """
    for family, addr in ((socket.AF_INET, ("127.0.0.1", port)), (socket.AF_INET6, ("::1", port))):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as s:
                s.settimeout(0.3)
                if s.connect_ex(addr) == 0:
                    return True
        except OSError:
            continue
    return False


def _bind_socket(port: int) -> socket.socket:
    """Bind and listen on 127.0.0.1 ourselves so EADDRINUSE is reported precisely."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((BIND_HOST, port))
        sock.listen(128)
    except BaseException:
        sock.close()
        raise
    return sock


def _prepare_dirs() -> None:
    for var in ("ENGINE_DATA_DIR", "ENGINE_CACHE_DIR"):
        path = os.environ.get(var)
        if path:
            os.makedirs(path, exist_ok=True)


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="cardboard_engine.server")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--parent-pid", type=int, default=None)
    args = p.parse_args(argv)
    if not (1 <= args.port <= 65535):
        p.error("--port must be between 1 and 65535")
    return args


def main(argv=None) -> int:
    from werkzeug.serving import make_server

    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _prepare_dirs()

    token = os.environ.get("ENGINE_SHUTDOWN_TOKEN", "")
    if not token:
        log.warning("ENGINE_SHUTDOWN_TOKEN not set; POST /shutdown is disabled")

    lifecycle = Lifecycle(parent_pid=args.parent_pid)
    app = create_app(shutdown_token=token, lifecycle=lifecycle)
    tracked = InFlightTracker(app)

    if _something_listening(args.port):
        _port_in_use(args.port)
    try:
        sock = _bind_socket(args.port)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            _port_in_use(args.port)
        raise
    # werkzeug dup()s the descriptor, so our copy can be closed.
    server = make_server(BIND_HOST, args.port, tracked, threaded=True, fd=sock.fileno())
    sock.close()

    lifecycle.bind_server(server.shutdown)
    lifecycle.install_signal_handlers()
    lifecycle.start_watchdog()

    port = server.port
    print(
        "ENGINE_READY " + json.dumps({"port": port, "version": __version__}),
        flush=True,
    )
    log.info("Engine %s listening on http://%s:%d", __version__, BIND_HOST, port)

    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        if not tracked.wait_idle(DRAIN_TIMEOUT_S):
            log.warning("Exiting with %d request(s) still in flight", tracked.count)
        server.server_close()
        log.info("Engine stopped (%s)", lifecycle.shutdown_reason or "serve_forever returned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
