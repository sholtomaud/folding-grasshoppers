"""Hello-world demo: the smallest possible round trip through the engine."""

from __future__ import annotations

PRODUCT = "CardboardCAD by Fold Theory"


def greet(name: str | None = None) -> str:
    """Return the demo greeting. Blank or missing names become "world"."""
    if name is not None and not isinstance(name, str):
        raise TypeError("name must be a string")
    who = (name or "").strip() or "world"
    return f"Hello, {who}, from {PRODUCT}"
