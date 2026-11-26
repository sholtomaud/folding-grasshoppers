#!/usr/bin/env bash
# Local verification gate (run by .githooks and by agents before declaring done).
#
#   scripts/verify.sh          # full: pytest, Swift tests (macOS), test floors, proof seal
#   scripts/verify.sh --fast   # pytest + proof seal only
#
# The proof seal check uses the agentic-uplift CLI pinned below; it is fetched
# into build/cache on first use (needs Node >= 22.18 and git).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
FAST=0; [ "${1:-}" = "--fast" ] && FAST=1

UPLIFT_SHA="e2158d3a4e92de8e5c07e4bc2c80d82933cf6c17"
UPLIFT_DIR="${AGENTIC_UPLIFT_DIR:-$ROOT/build/cache/agentic-uplift-$UPLIFT_SHA}"
mkdir -p build

echo "==> pytest (engine)"
(cd engine && uv run --frozen pytest -q --junitxml="$ROOT/build/pytest.xml")
python3 scripts/ci/check_test_floors.py pytest build/pytest.xml

if [ "$FAST" -eq 0 ] && [ "$(uname -s)" = "Darwin" ]; then
  echo "==> swift test (CardboardCADKit)"
  (cd app/CardboardCADKit && swift test --parallel --xunit-output "$ROOT/build/swift-xunit.xml")
  python3 scripts/ci/check_test_floors.py swift build/swift-xunit.xml
elif [ "$FAST" -eq 0 ]; then
  echo "==> swift test skipped (not macOS; CI runs it on macos-15)"
fi

echo "==> proof seal (agentic-uplift)"
if command -v node >/dev/null 2>&1; then
  if [ ! -f "$UPLIFT_DIR/src/cli.ts" ]; then
    rm -rf "$UPLIFT_DIR"
    git init -q "$UPLIFT_DIR"
    git -C "$UPLIFT_DIR" fetch -q --depth 1 https://github.com/sholtomaud/agentic-uplift "$UPLIFT_SHA"
    git -C "$UPLIFT_DIR" checkout -q FETCH_HEAD
  fi
  node "$UPLIFT_DIR/src/cli.ts" proof check --repo "$ROOT"
else
  echo "node not found; skipping proof seal check (CI enforces it)" >&2
fi
echo "verify: OK"
