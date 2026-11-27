"""The product name and its default folder.

The app was called CardboardCAD. Python module names (``cardboard_engine``)
and Hops URLs keep the old name so nothing saved before the rename breaks;
everything a designer reads says Fold Theory.
"""

from __future__ import annotations

import os

APP_NAME = "Fold Theory"
LEGACY_APP_NAME = "CardboardCAD"


def documents_dir(home: str) -> str:
    """~/Documents/Fold Theory: exports, Projects/ and Workflows/ live here."""
    return os.path.join(home, "Documents", APP_NAME)
