#!/usr/bin/env bash
# Build a compressed DMG (app + /Applications symlink), sign, notarize, staple.
#
#   scripts/make_dmg.sh            # expects a signed, notarized, stapled app
#   SKIP_NOTARIZE=1 scripts/make_dmg.sh
set -euo pipefail
source "$(dirname "$0")/config.sh"

[ -d "$APP_BUNDLE" ] || die "app not found: $APP_BUNDLE (run build_app.sh, sign.sh, notarize.sh first)"
VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP_BUNDLE/Contents/Info.plist")"
STAGE="$BUILD/dmg-stage"

log "Staging"
rm -rf "$STAGE" "$DMG_PATH"
mkdir -p "$STAGE"
ditto "$APP_BUNDLE" "$STAGE/$APP_NAME.app"
ln -s /Applications "$STAGE/Applications"

log "Creating $(basename "$DMG_PATH") ($APP_NAME $VERSION)"
hdiutil create -volname "$APP_NAME $VERSION" -srcfolder "$STAGE" \
  -fs HFS+ -format UDZO -imagekey zlib-level=9 -ov "$DMG_PATH"
rm -rf "$STAGE"

resolve_identity
if [ "$SIGN_IDENTITY" = "-" ]; then TS="--timestamp=none"; else TS="--timestamp"; fi
log "Signing DMG"
codesign --force $TS --sign "$SIGN_IDENTITY" "$DMG_PATH"

if [ "${SKIP_NOTARIZE:-0}" != "1" ]; then
  "$ROOT/scripts/notarize.sh" "$DMG_PATH"
fi
log "DMG ready: ${DMG_PATH#$ROOT/}"
