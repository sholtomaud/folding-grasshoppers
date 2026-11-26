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
from .lifecycle import DRAIN_TIMEOUT_S, InFlightTracker, Lifecycle  # noqa: E402

BIND_HOST = "127.0.0.1"  # never 0.0.0.0
DEFAULT_PORT = 5001
EXIT_PORT_IN_USE = 98

log = logging.getLogger("cardboard_engine")

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

    # ---- Hops components -----------------------------------------------
    @hops.component(
        "/echo",
        name="Echo",
        nickname="Echo",
        description="Smoke test: returns the number it is given",
        category="CardboardCAD",
        subcategory="Debug",
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
        category="CardboardCAD",
        subcategory="Estimate",
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

    return app


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
