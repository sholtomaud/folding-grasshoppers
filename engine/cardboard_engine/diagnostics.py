"""Diagnostics: one rolling log that every part of Fold Theory reports to.

Grasshopper's LiveDesign listener and Hops components, the Rhino plugin, the
engine's tools, and validation of forms, views, formulas and workflows write
structured entries here. The app shows them in its Diagnostics panel and the
Design Assistant reads them with ``read_diagnostics``, so "why is Rhino not
updating?" is answered from what really happened.

Entries are JSON lines in ``$ENGINE_DATA_DIR/diagnostics/diagnostics.jsonl``,
rotated to ``.1`` at a fixed size. They stay on the Mac. API keys, bearer
tokens and the engine's shutdown token are redacted before anything is
written.

Others report with ``POST /api/diagnostics``::

    {"source": "rhino", "level": "warning", "message": "Rib X3 edited in Rhino and in the app",
     "design_version": 12, "details": {"object": "rib-X3"}}
"""

from __future__ import annotations

import datetime
import json
import os
import re
import threading
from typing import Any, Dict, List, Optional

SOURCES = ("grasshopper", "hops", "rhino", "engine", "validation", "app")
LEVELS = ("debug", "info", "warning", "error")
FILE_NAME = "diagnostics.jsonl"
MAX_BYTES = 1024 * 1024
MAX_MESSAGE = 2000
MAX_DETAILS = 8000
MEMORY_ENTRIES = 1000
REDACTED = "[redacted]"

_SECRET_PATTERNS = [
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"), REDACTED),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=\-]{8,}"), r"\1 " + REDACTED),
    (re.compile(r"(?i)\b(x-api-key|x-engine-token|api[_-]?key|token|secret|password)(\"?\s*[:=]\s*\"?)"
                r"[^\s\"',;}]{4,}"), r"\1\2" + REDACTED),
]
_SECRET_KEYS = re.compile(r"(?i)(token|secret|password|api[_-]?key|authorization|cookie)")


def redact(text: str) -> str:
    """The text with anything shaped like a key or token replaced by [redacted]."""
    if not isinstance(text, str):
        return text
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def _redact_value(value, depth=0):
    if depth > 6:
        return "…"
    if isinstance(value, str):
        return redact(value)[:MAX_MESSAGE]
    if isinstance(value, dict):
        return {str(k)[:80]: (REDACTED if _SECRET_KEYS.search(str(k)) and isinstance(v, (str, int, float))
                              else _redact_value(v, depth + 1))
                for k, v in list(value.items())[:50]}
    if isinstance(value, (list, tuple)):
        return [_redact_value(v, depth + 1) for v in list(value)[:50]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact(str(value))[:MAX_MESSAGE]


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"


class DiagnosticsLog:
    """Thread-safe rolling log. With no folder, entries are kept in memory only (tests, no data dir)."""

    def __init__(self, folder: Optional[str] = None, max_bytes: int = MAX_BYTES):
        self.folder = folder
        self.max_bytes = max_bytes
        self._lock = threading.Lock()
        self._memory: List[dict] = []
        self._next_id = 1
        if folder:
            os.makedirs(folder, exist_ok=True)
            last = self._read_files()[-1:] or [{}]
            self._next_id = int(last[0].get("id", 0)) + 1

    @property
    def path(self) -> Optional[str]:
        return os.path.join(self.folder, FILE_NAME) if self.folder else None

    def write(self, source: str, level: str, message: str, design_version: Optional[int] = None,
              details: Optional[Dict[str, Any]] = None) -> dict:
        if source not in SOURCES:
            raise ValueError(f"source must be one of {', '.join(SOURCES)}")
        if level not in LEVELS:
            raise ValueError(f"level must be one of {', '.join(LEVELS)}")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must be text")
        if design_version is not None and (isinstance(design_version, bool) or not isinstance(design_version, int)):
            design_version = None
        clean_details = _redact_value(details if isinstance(details, dict) else {})
        if len(json.dumps(clean_details)) > MAX_DETAILS:
            clean_details = {"note": "details too large, dropped"}
        with self._lock:
            entry = {"id": self._next_id, "time": _now(), "source": source, "level": level,
                     "message": redact(message.strip())[:MAX_MESSAGE], "design_version": design_version,
                     "details": clean_details}
            self._next_id += 1
            if self.folder:
                line = json.dumps(entry, ensure_ascii=False) + "\n"
                data = line.encode("utf-8")
                try:
                    size = os.path.getsize(self.path)
                except OSError:
                    size = 0
                if size and size + len(data) > self.max_bytes:
                    os.replace(self.path, self.path + ".1")
                with open(self.path, "ab") as fh:
                    fh.write(data)
            else:
                self._memory.append(entry)
                del self._memory[:-MEMORY_ENTRIES]
            return entry

    def _read_files(self) -> List[dict]:
        out = []
        for path in (self.path + ".1", self.path):
            try:
                with open(path, encoding="utf-8") as fh:
                    for line in fh:
                        try:
                            e = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(e, dict) and "id" in e:
                            out.append(e)
            except OSError:
                continue
        return out

    def read(self, source: Optional[str] = None, level: Optional[str] = None, since_id: Optional[int] = None,
             limit: int = 100) -> List[dict]:
        """Matching entries, oldest first, at most ``limit`` (the newest ones)."""
        if level is not None and level not in LEVELS:
            raise ValueError(f"level must be one of {', '.join(LEVELS)}")
        if source is not None and source not in SOURCES:
            raise ValueError(f"source must be one of {', '.join(SOURCES)}")
        floor = LEVELS.index(level) if level else 0
        with self._lock:
            entries = self._read_files() if self.folder else list(self._memory)
        out = [e for e in entries
               if (source is None or e.get("source") == source)
               and (e.get("level") in LEVELS and LEVELS.index(e["level"]) >= floor)
               and (since_id is None or int(e.get("id", 0)) > since_id)]
        limit = max(1, min(int(limit), 1000))
        return out[-limit:]


def format_entries(entries: List[dict]) -> str:
    """Entries as short lines for Claude."""
    if not entries:
        return "No diagnostics match."
    lines = []
    for e in entries:
        v = f" v{e['design_version']}" if e.get("design_version") is not None else ""
        details = f" {json.dumps(e['details'], ensure_ascii=False)}" if e.get("details") else ""
        lines.append(f"[{e['time']}] {e['source']} {e['level']}{v}: {e['message']}{details}")
    return "\n".join(lines)
