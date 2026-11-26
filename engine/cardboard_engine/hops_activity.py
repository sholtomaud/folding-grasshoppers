"""Record the Hops (Grasshopper) requests the engine serves.

Hops never keeps a connection open: it sends a request each time a component
loads its definition (GET /<component>) or solves (POST /solve). This WSGI
middleware counts those requests so the app can show when Grasshopper last
called the engine. It sits outside the ghhops_server middleware, which
answers Hops routes before Flask's own hooks would see them.
"""

from __future__ import annotations

import io
import json
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

SOLVE_PATH = "/solve"
MAX_PEEK_BYTES = 1 << 20  # only /solve bodies are peeked, for the pointer


class HopsActivity:
    def __init__(self, app, is_hops_path: Callable[[str], bool], clock=time.time):
        self.app = app
        self._is_hops_path = is_hops_path
        self._clock = clock
        self._lock = threading.Lock()
        self._requests = 0
        self._last_at: Optional[float] = None
        self._last_path: Optional[str] = None
        self._last_status: Optional[int] = None

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "") or "/"
        if not self._is_hops_path(path):
            return self.app(environ, start_response)

        label = path
        if path == SOLVE_PATH and environ.get("REQUEST_METHOD") == "POST":
            pointer = self._peek_pointer(environ)
            if pointer:
                label = f"{SOLVE_PATH} {pointer}"

        status_holder = {}

        def recording_start_response(status, headers, exc_info=None):
            try:
                status_holder["code"] = int(str(status).split(" ", 1)[0])
            except ValueError:
                status_holder["code"] = None
            if exc_info is not None:
                return start_response(status, headers, exc_info)
            return start_response(status, headers)

        try:
            result = self.app(environ, recording_start_response)
        finally:
            with self._lock:
                self._requests += 1
                self._last_at = self._clock()
                self._last_path = label
                self._last_status = status_holder.get("code")
        return result

    @staticmethod
    def _peek_pointer(environ) -> Optional[str]:
        """Read the /solve body for its "pointer" and put the bytes back."""
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            return None
        if length <= 0 or length > MAX_PEEK_BYTES:
            return None
        body = environ["wsgi.input"].read(length)
        environ["wsgi.input"] = io.BytesIO(body)
        try:
            pointer = json.loads(body).get("pointer")
        except (ValueError, AttributeError):
            return None
        if not isinstance(pointer, str):
            return None
        return pointer if pointer.startswith("/") else "/" + pointer

    def snapshot(self) -> dict:
        with self._lock:
            last_at = self._last_at
            out = {
                "requests": self._requests,
                "last_request_at": None,
                "seconds_since_last": None,
                "last_path": self._last_path,
                "last_status": self._last_status,
            }
        if last_at is not None:
            out["last_request_at"] = (
                datetime.fromtimestamp(last_at, tz=timezone.utc)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z")
            )
            out["seconds_since_last"] = round(max(0.0, self._clock() - last_at), 1)
        return out
