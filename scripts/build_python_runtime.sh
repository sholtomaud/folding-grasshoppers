#!/usr/bin/env bash
# Build the self-contained Python runtime that ships inside Fold Theory.app.
#
#   scripts/build_python_runtime.sh            # aarch64-apple-darwin (default)
#   TARGET=x86_64-apple-darwin scripts/build_python_runtime.sh
#
# Output: build/python-runtime/   (the python-build-standalone tree + engine)
# Cache:  build/cache/            (downloaded tarballs)
#
# Requires on the build machine: bash (3.2 is fine), curl, tar, uv.
# Never uses PyInstaller / py2app / cx_Freeze.
#
# Other targets (e.g. x86_64-unknown-linux-gnu) are accepted only so the script
# can be exercised on a Linux CI box; the app itself is macOS-only.
#
# Cross mode (RUNTIME_MODE=cross, the default when TARGET's OS differs from
# this machine's): the target interpreter is never executed. Wheels are
# installed with `uv pip install --target ... --python-platform $TARGET`, and
# bytecode is compiled by a build-machine CPython of the same version. This is
# how `make runtime` builds the macOS runtime inside a Linux container. The
# import and server smoke tests then run on the Mac (`make smoke`).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/build"
CACHE="$BUILD/cache"
RUNTIME="$BUILD/python-runtime"
ENGINE="$ROOT/engine"

# ---------------------------------------------------------------------------
# Pinned interpreter. Bump all three together. SHA-256 values come from the
# release's SHA256SUMS file:
#   https://github.com/astral-sh/python-build-standalone/releases/download/<tag>/SHA256SUMS
# ---------------------------------------------------------------------------
PBS_TAG="20260718"
PY_VERSION="3.12.13"
PY_MINOR="3.12"
TARGET="${TARGET:-aarch64-apple-darwin}"

case "$TARGET" in
  aarch64-apple-darwin)      PBS_SHA256="62aeee6161d57303a71a138b75fd5cc6fb8c89c4b1d9c7f0a052d89fa0b6652b" ;;
  x86_64-apple-darwin)       PBS_SHA256="10b47148de86f9d87ba6e96a3db606ced90a206a3454d7d6d8fa68536a05d81f" ;;
  x86_64-unknown-linux-gnu)  PBS_SHA256="7eea0959fa425c8aff3ea0a1352ee7d01d794b51439ed8f5fcfa017dbc0ec661" ;;
  aarch64-unknown-linux-gnu) PBS_SHA256="53fb0a17442cd03bcfbc21df8d82e739b4901b87844934e36e54ab5122a55dfa" ;;
  *) echo "error: no pinned SHA-256 for TARGET=$TARGET" >&2; exit 1 ;;
esac

TARBALL="cpython-${PY_VERSION}+${PBS_TAG}-${TARGET}-install_only.tar.gz"
URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/${TARBALL//+/%2B}"

WARN_SIZE_MB=300

log() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

sha256_of() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    sha256sum "$1" | awk '{print $1}'
  fi
}

command -v uv >/dev/null 2>&1 || die "uv is required (https://docs.astral.sh/uv/)"
command -v curl >/dev/null 2>&1 || die "curl is required"

case "$TARGET" in
  *-apple-darwin) TARGET_OS=Darwin ;;
  *)              TARGET_OS=Linux ;;
esac
if [ -z "${RUNTIME_MODE:-}" ]; then
  if [ "$TARGET_OS" = "$(uname -s)" ]; then RUNTIME_MODE=native; else RUNTIME_MODE=cross; fi
fi
case "$RUNTIME_MODE" in
  native|cross) ;;
  *) die "RUNTIME_MODE must be native or cross (got $RUNTIME_MODE)" ;;
esac
log "Target $TARGET, mode $RUNTIME_MODE"

# ---------------------------------------------------------------------------
# 1. Download (cached) and verify. Fail hard on mismatch.
# ---------------------------------------------------------------------------
mkdir -p "$CACHE"
if [ ! -f "$CACHE/$TARBALL" ]; then
  log "Downloading $TARBALL"
  curl -fL --retry 3 --retry-delay 2 -o "$CACHE/$TARBALL.part" "$URL"
  mv "$CACHE/$TARBALL.part" "$CACHE/$TARBALL"
else
  log "Using cached $TARBALL"
fi

ACTUAL_SHA="$(sha256_of "$CACHE/$TARBALL")"
if [ "$ACTUAL_SHA" != "$PBS_SHA256" ]; then
  rm -f "$CACHE/$TARBALL"
  die "SHA-256 mismatch for $TARBALL
  expected $PBS_SHA256
  actual   $ACTUAL_SHA
The cached file has been deleted."
fi
log "SHA-256 verified ($PBS_SHA256)"

# ---------------------------------------------------------------------------
# 2. Extract to build/python-runtime/
# ---------------------------------------------------------------------------
rm -rf "$RUNTIME" "$BUILD/python-extract"
mkdir -p "$BUILD/python-extract"
tar -xzf "$CACHE/$TARBALL" -C "$BUILD/python-extract"
[ -d "$BUILD/python-extract/python" ] || die "unexpected tarball layout (no python/ dir)"
mv "$BUILD/python-extract/python" "$RUNTIME"
rmdir "$BUILD/python-extract"

PY="$RUNTIME/bin/python${PY_MINOR}"
[ -x "$PY" ] || die "interpreter not found at $PY"
SITE="$RUNTIME/lib/python${PY_MINOR}/site-packages"
STDLIB="$RUNTIME/lib/python${PY_MINOR}"

# Native wheels must be compatible with the minimum macOS we support.
export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-14.0}"

# ---------------------------------------------------------------------------
# 3. Install locked deps (hash-checked, wheels only), then the engine itself.
# ---------------------------------------------------------------------------
log "Building cardboard_engine wheel"
rm -rf "$BUILD/engine-dist"
if [ "$RUNTIME_MODE" = native ]; then
  uv build --wheel --out-dir "$BUILD/engine-dist" "$ENGINE"
else
  uv build --wheel --python "$PY_VERSION" --out-dir "$BUILD/engine-dist" "$ENGINE"
fi

if [ "$RUNTIME_MODE" = native ]; then
  log "Installing locked dependencies into the runtime"
  uv pip install \
    --python "$PY" \
    --break-system-packages \
    --require-hashes \
    --no-deps \
    --only-binary :all: \
    --no-cache \
    -r "$ENGINE/requirements.lock"
  log "Installing cardboard_engine (non-editable)"
  uv pip install --python "$PY" --break-system-packages --no-deps --no-cache "$BUILD"/engine-dist/*.whl
  # pip is not needed at runtime and would be a writable-install footgun.
  uv pip uninstall --python "$PY" --break-system-packages pip >/dev/null 2>&1 || true
else
  # MACOSX_DEPLOYMENT_TARGET (above) sets which macOS wheel tags uv accepts.
  log "Installing locked dependencies for $TARGET (cross; target interpreter not run)"
  uv pip install \
    --target "$SITE" \
    --python-platform "$TARGET" \
    --python-version "$PY_VERSION" \
    --require-hashes \
    --no-deps \
    --only-binary :all: \
    --no-cache \
    -r "$ENGINE/requirements.lock"
  log "Installing cardboard_engine (non-editable, cross)"
  uv pip install --target "$SITE" --python-platform "$TARGET" --python-version "$PY_VERSION" \
    --no-deps --no-cache "$BUILD"/engine-dist/*.whl
  # --target puts console scripts in $SITE/bin; the app runs `python -m`.
  rm -rf "$SITE/bin" "$SITE"/pip "$SITE"/pip-*.dist-info
  # Every native wheel must be built for the target OS, not this machine.
  case "$TARGET" in *-apple-darwin) WANT_TAG='-macosx_' ;; *) WANT_TAG='linux' ;; esac
  for wheel in "$SITE"/*.dist-info/WHEEL; do
    grep -q '^Root-Is-Purelib: true' "$wheel" && continue
    grep -q "^Tag: .*${WANT_TAG}" "$wheel" || die "wrong-platform wheel installed: ${wheel#$RUNTIME/}"
  done
fi

# ---------------------------------------------------------------------------
# 4. Strip what the engine does not need. Keep this list in sync with
#    scripts/README.md. Everything here is deleted relative to $RUNTIME.
# ---------------------------------------------------------------------------
log "Stripping unneeded files"
REMOVED_LIST="$BUILD/python-runtime-removed.txt"
: > "$REMOVED_LIST"

remove() {
  # remove <path>... ; logs each existing path, relative to $RUNTIME
  local p
  for p in "$@"; do
    if [ -e "$p" ] || [ -L "$p" ]; then
      printf '%s\n' "${p#$RUNTIME/}" >> "$REMOVED_LIST"
      rm -rf "$p"
    fi
  done
}

# Stdlib modules we never import
remove "$STDLIB/idlelib" "$STDLIB/tkinter" "$STDLIB/turtledemo" "$STDLIB/turtle.py" \
       "$STDLIB/ensurepip" "$STDLIB/lib2to3" "$STDLIB/pydoc_data"
# Tcl/Tk (python-build-standalone ships these under lib/)
remove "$RUNTIME"/lib/tcl* "$RUNTIME"/lib/tk* "$RUNTIME"/lib/itcl* "$RUNTIME"/lib/thread* \
       "$RUNTIME"/lib/libtcl* "$RUNTIME"/lib/libtk*
remove "$STDLIB"/lib-dynload/_tkinter*
# Test suites: stdlib test/ plus any test/ or tests/ dir inside packages
remove "$STDLIB/test" "$STDLIB/unittest/test"
while IFS= read -r d; do remove "$d"; done <<EOF
$(find "$STDLIB" -type d \( -name test -o -name tests \) -prune -print)
EOF
# Headers, static libs, pkg-config, man pages, idle/pydoc launchers
remove "$RUNTIME/include" "$RUNTIME/share" "$RUNTIME/lib/pkgconfig"
while IFS= read -r f; do remove "$f"; done <<EOF
$(find "$RUNTIME" -type f -name '*.a')
EOF
remove "$RUNTIME"/bin/idle* "$RUNTIME"/bin/pydoc* "$RUNTIME"/bin/pip* "$RUNTIME"/bin/2to3* \
       "$RUNTIME"/bin/python*-config
# Console-script launchers (flask, cardboard-engine, ...) carry a shebang with
# this build machine's absolute path. The app runs `python -m`, so drop them.
for f in "$RUNTIME"/bin/*; do
  case "$(basename "$f")" in python|python3|python${PY_MINOR}) ;; *) remove "$f" ;; esac
done
# Existing bytecode (regenerated below with hash-based invalidation)
while IFS= read -r d; do remove "$d"; done <<EOF
$(find "$RUNTIME" -type d -name __pycache__ -prune -print)
EOF

log "Removed $(wc -l < "$REMOVED_LIST" | tr -d ' ') paths (list: ${REMOVED_LIST#$ROOT/})"

# ---------------------------------------------------------------------------
# 5. Precompile bytecode. unchecked-hash .pyc files are used as-is, whatever
#    the source mtimes are after copying/signing, so the signed bundle never
#    needs to recompile (and -B means it could not write anyway).
# ---------------------------------------------------------------------------
log "Precompiling bytecode"
if [ "$RUNTIME_MODE" = native ]; then
  COMPILE_PY=("$PY")
else
  # Same CPython version as the target, so the .pyc magic number matches.
  COMPILE_PY=(uv run --no-project --python "$PY_VERSION" python)
  "${COMPILE_PY[@]}" -I -c "import sys; v='%d.%d.%d' % sys.version_info[:3]; assert v == '$PY_VERSION', v"
fi
"${COMPILE_PY[@]}" -I -m compileall -q -j0 --invalidation-mode unchecked-hash "$STDLIB" >/dev/null

# ---------------------------------------------------------------------------
# 6. Size report
# ---------------------------------------------------------------------------
SIZE_MB=$(( $(du -sk "$RUNTIME" | awk '{print $1}') / 1024 ))
log "Runtime size: ${SIZE_MB} MB"
if [ "$SIZE_MB" -gt "$WARN_SIZE_MB" ]; then
  printf 'warning: runtime is %s MB (> %s MB)\n' "$SIZE_MB" "$WARN_SIZE_MB" >&2
fi

# ---------------------------------------------------------------------------
# 7. Smoke tests: imports in isolated mode, then a real engine start/stop.
# ---------------------------------------------------------------------------
if [ "$RUNTIME_MODE" = cross ]; then
  log "Smoke tests skipped in cross mode; on the Mac run: make smoke"
  log "Done: ${RUNTIME#$ROOT/} (Python $PY_VERSION, $TARGET, pbs $PBS_TAG, cross)"
  exit 0
fi

PYC_BEFORE="$(find "$RUNTIME" -name '*.pyc' | wc -l | tr -d ' ')"
log "Smoke test: imports"
"$PY" -I -B -c "import cardboard_engine, flask, ghhops_server, rhino3dm, pyclipper; print('imports ok', cardboard_engine.__version__)"

if [ "${SKIP_SERVER_SMOKE:-0}" != "1" ]; then
  log "Smoke test: start engine, GET /health, SIGTERM"
  "$PY" -I -B -u "$ROOT/scripts/smoke_engine.py" "$PY"
fi

# With -B nothing may write bytecode during the smoke tests.
PYC_AFTER="$(find "$RUNTIME" -name '*.pyc' | wc -l | tr -d ' ')"
[ "$PYC_BEFORE" = "$PYC_AFTER" ] || die "smoke test wrote bytecode ($PYC_BEFORE -> $PYC_AFTER .pyc files)"

log "Done: ${RUNTIME#$ROOT/} (Python $PY_VERSION, $TARGET, pbs $PBS_TAG)"
