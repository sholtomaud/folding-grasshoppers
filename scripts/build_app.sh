#!/usr/bin/env bash
# Build CardboardCAD.app (unsigned) and embed the Python runtime.
#
#   scripts/build_app.sh            # Release, arm64, runtime in Contents/Resources/python
#   PYTHON_LAYOUT=resources scripts/build_app.sh
#
# Embedding happens here, after the Xcode build and before signing, rather
# than in an Xcode Run Script phase: it keeps the Xcode project free of
# build-machine paths, works the same for Debug/Release/CI, and signing
# (scripts/sign.sh) always sees the final bundle. Debug runs from Xcode use
# the dev runtime via CARDBOARD_DEV_RUNTIME instead (see the shared scheme).
set -euo pipefail
source "$(dirname "$0")/config.sh"

CONFIGURATION="${CONFIGURATION:-Release}"
RUNTIME="$BUILD/python-runtime"
ARCHIVE="$BUILD/$APP_NAME.xcarchive"
HOME_REL="$(python_home_rel)"

[ "$(uname -s)" = "Darwin" ] || die "build_app.sh needs macOS + Xcode"

if [ ! -x "$RUNTIME/bin/python3.12" ] || [ "${REBUILD_RUNTIME:-0}" = "1" ]; then
  log "Building Python runtime"
  "$ROOT/scripts/build_python_runtime.sh"
fi

log "xcodebuild archive ($CONFIGURATION, $ARCHS)"
rm -rf "$ARCHIVE"
mkdir -p "$BUILD"
set +e
xcodebuild archive \
  -project "$ROOT/app/$APP_NAME.xcodeproj" \
  -scheme "$APP_NAME" \
  -configuration "$CONFIGURATION" \
  -archivePath "$ARCHIVE" \
  -destination "generic/platform=macOS" \
  ARCHS="$ARCHS" \
  PRODUCT_BUNDLE_IDENTIFIER="$BUNDLE_ID" \
  CODE_SIGNING_ALLOWED=NO \
  CODE_SIGN_IDENTITY="" \
  > "$BUILD/xcodebuild-archive.log" 2>&1
RC=$?
set -e
grep -E "(error:|\*\* ARCHIVE)" "$BUILD/xcodebuild-archive.log" || true
[ "$RC" -eq 0 ] || { tail -50 "$BUILD/xcodebuild-archive.log"; die "xcodebuild archive failed (build/xcodebuild-archive.log)"; }

# We do not use `xcodebuild -exportArchive`: it would sign with Xcode's
# settings, and scripts/sign.sh has to sign inside-out anyway.
log "Copying app out of the archive"
rm -rf "$APP_BUNDLE"
mkdir -p "$(dirname "$APP_BUNDLE")"
ditto "$ARCHIVE/Products/Applications/$APP_NAME.app" "$APP_BUNDLE"

log "Embedding runtime at $HOME_REL"
mkdir -p "$APP_BUNDLE/$(dirname "$HOME_REL")"
ditto "$RUNTIME" "$APP_BUNDLE/$HOME_REL"
/usr/libexec/PlistBuddy -c "Delete :CardboardPythonHome" "$APP_BUNDLE/Contents/Info.plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :CardboardPythonHome string $HOME_REL" "$APP_BUNDLE/Contents/Info.plist"

# Extended attributes (quarantine, Finder info) break codesign.
xattr -cr "$APP_BUNDLE"

log "Smoke test: engine from inside the (unsigned) bundle"
"$APP_BUNDLE/$HOME_REL/bin/python3.12" -I -B -u "$ROOT/scripts/smoke_engine.py" "$APP_BUNDLE/$HOME_REL/bin/python3.12"

log "Built ${APP_BUNDLE#$ROOT/} ($(du -sh "$APP_BUNDLE" | awk '{print $1}'))"
