"""Process lifecycle: parent watchdog, graceful shutdown, in-flight tracking.

The engine is a child of the macOS app. It must never outlive it, so a daemon
thread polls the parent PID. Shutdown can be triggered by:

* the parent disappearing (crash, force-quit),
* SIGTERM / SIGINT,
* an authenticated ``POST /shutdown``.

All of them funnel into :meth:`Lifecycle.request_shutdown`, which stops the
HTTP server from a helper thread (``serve_forever`` cannot be stopped from its
own thread) and lets in-flight requests finish within a bounded drain window.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
from typing import Callable, Optional

log = logging.getLogger("cardboard_engine.lifecycle")

WATCHDOG_INTERVAL_S = 2.0
DRAIN_TIMEOUT_S = 2.5  # SIGTERM must lead to exit 0 within 3 s overall


def pid_alive(pid: int) -> bool:
    """True if a process with this PID exists (signal 0 probes without sending)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but owned by someone else. Still alive.
        return True
    return True


class InFlightTracker:
    """WSGI middleware that counts requests currently being handled."""

    def __init__(self, app):
        self.app = app
        self._count = 0
        self._cond = threading.Condition()

    @property
    def count(self) -> int:
        with self._cond:
            return self._count

    def __call__(self, environ, start_response):
        with self._cond:
            self._count += 1
        try:
            # Materialise the body so "in flight" covers the whole response.
            return list(self.app(environ, start_response))
        finally:
            with self._cond:
                self._count -= 1
                self._cond.notify_all()

    def wait_idle(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._cond:
            while self._count > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cond.wait(remaining)
            return True


class Lifecycle:
    def __init__(
        self,
        parent_pid: Optional[int] = None,
        interval: float = WATCHDOG_INTERVAL_S,
    ):
        self.parent_pid = parent_pid
        self.interval = interval
        self.initial_ppid = os.getppid()
        self.shutdown_event = threading.Event()
        self.shutdown_reason: Optional[str] = None
        self._stop_server: Optional[Callable[[], None]] = None
        self._lock = threading.Lock()

    # -- wiring ------------------------------------------------------------
    def bind_server(self, stop_server: Callable[[], None]) -> None:
        """Register the callable that stops the HTTP server (e.g. server.shutdown)."""
        self._stop_server = stop_server

    def install_signal_handlers(self) -> None:
        def _handler(signum, _frame):
            self.request_shutdown(f"signal {signal.Signals(signum).name}")

        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT, _handler)

    # -- shutdown ----------------------------------------------------------
    def request_shutdown(self, reason: str, delay: float = 0.0) -> bool:
        """Idempotent. Returns True the first time it is called."""
        with self._lock:
            if self.shutdown_event.is_set():
                return False
            self.shutdown_event.set()
            self.shutdown_reason = reason
        log.info("Shutdown requested: %s", reason)

        def _stop():
            if delay:
                time.sleep(delay)
            if self._stop_server is not None:
                self._stop_server()

        threading.Thread(target=_stop, name="engine-shutdown", daemon=True).start()
        return True

    # -- watchdog ----------------------------------------------------------
    def parent_gone(self) -> Optional[str]:
        if self.parent_pid is not None and not pid_alive(self.parent_pid):
            return f"parent pid {self.parent_pid} is gone"
        ppid = os.getppid()
        if ppid != self.initial_ppid:
            return f"reparented from {self.initial_ppid} to {ppid}"
        return None

    def start_watchdog(self) -> threading.Thread:
        def _run():
            while not self.shutdown_event.wait(self.interval):
                reason = self.parent_gone()
                if reason:
                    self.request_shutdown(f"watchdog: {reason}")
                    return

        t = threading.Thread(target=_run, name="parent-watchdog", daemon=True)
        t.start()
        return t
