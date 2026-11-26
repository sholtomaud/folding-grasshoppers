#!/usr/bin/env bash
# Smoke-test build/python-runtime on this Mac with the runtime's own
# interpreter (the one CardboardCAD.app ships and launches), in isolated mode.
# No system Python, uv or Node is used. `make runtime` builds the runtime in
# the dev container, where this macOS interpreter cannot run, so the Makefile
# and build_app.sh run this step afterwards.
#
#   scripts/smoke_runtime.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME="$ROOT/build/python-runtime"
PY="$RUNTIME/bin/python3.12"

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
[ -x "$PY" ] || die "no runtime at ${RUNTIME#$ROOT/}; run: make runtime"

pyc_count() { find "$RUNTIME" -name '*.pyc' | wc -l | tr -d ' '; }
BEFORE="$(pyc_count)"

echo "==> Smoke test: imports"
"$PY" -I -B -c "import cardboard_engine, flask, ghhops_server, rhino3dm; print('imports ok', cardboard_engine.__version__)"
echo "==> Smoke test: start engine, GET /health, SIGTERM"
"$PY" -I -B -u "$ROOT/scripts/smoke_engine.py" "$PY"

AFTER="$(pyc_count)"
[ "$BEFORE" = "$AFTER" ] || die "smoke test wrote bytecode ($BEFORE -> $AFTER .pyc files)"
echo "==> Runtime smoke tests passed"
