#!/usr/bin/env bash
# Full release: runtime -> app -> sign -> notarize app -> DMG (sign, notarize) -> verify.
#
#   TEAM_ID=ABCDE12345 NOTARY_PROFILE=cardboardcad-notary scripts/release.sh
set -euo pipefail
source "$(dirname "$0")/config.sh"

resolve_identity
[ "$SIGN_IDENTITY" != "-" ] || die "release.sh needs a Developer ID identity, not ad-hoc"
export SIGN_IDENTITY

S="$ROOT/scripts"
REBUILD_RUNTIME=1 "$S/build_app.sh"
"$S/sign.sh" "$APP_BUNDLE"
"$S/notarize.sh" "$APP_BUNDLE"
"$S/make_dmg.sh"
"$S/verify_bundle.sh" "$APP_BUNDLE" "$DMG_PATH"
log "Release artifacts: ${DMG_PATH#$ROOT/}"
