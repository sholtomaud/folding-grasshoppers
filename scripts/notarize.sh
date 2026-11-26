#!/usr/bin/env bash
# Notarize and staple a signed .app or .dmg.
#
#   NOTARY_PROFILE=cardboardcad-notary scripts/notarize.sh [build/app/CardboardCAD.app | build/CardboardCAD.dmg]
#
# Credentials come from a notarytool keychain profile, or in CI from an App
# Store Connect API key via NOTARY_KEY_PATH / NOTARY_KEY_ID / NOTARY_ISSUER.
set -euo pipefail
source "$(dirname "$0")/config.sh"

TARGET="${1:-$APP_BUNDLE}"
[ -e "$TARGET" ] || die "not found: $TARGET"

if [ -n "${NOTARY_KEY_PATH:-}" ]; then
  AUTH=(--key "$NOTARY_KEY_PATH" --key-id "$NOTARY_KEY_ID" --issuer "$NOTARY_ISSUER")
else
  AUTH=(--keychain-profile "$NOTARY_PROFILE")
fi

case "$TARGET" in
  *.app)
    SUBMIT="$BUILD/$(basename "$TARGET" .app)-notarize.zip"
    log "Zipping $(basename "$TARGET")"
    rm -f "$SUBMIT"
    ditto -c -k --keepParent "$TARGET" "$SUBMIT"
    ;;
  *.dmg) SUBMIT="$TARGET" ;;
  *) die "expected a .app or .dmg: $TARGET" ;;
esac

log "Submitting to the notary service (this can take a few minutes)"
OUT="$BUILD/notary-$(basename "$SUBMIT").json"
set +e
xcrun notarytool submit "$SUBMIT" "${AUTH[@]}" --wait --output-format json > "$OUT"
RC=$?
set -e
cat "$OUT"; echo

SUB_ID="$(/usr/bin/plutil -extract id raw -o - "$OUT" 2>/dev/null || true)"
STATUS="$(/usr/bin/plutil -extract status raw -o - "$OUT" 2>/dev/null || true)"

if [ "$RC" -ne 0 ] || [ "$STATUS" != "Accepted" ]; then
  printf 'Notarization failed (status=%s).\n' "${STATUS:-unknown}" >&2
  if [ -n "$SUB_ID" ]; then
    log "Notary log for $SUB_ID"
    xcrun notarytool log "$SUB_ID" "${AUTH[@]}" || true
  fi
  exit 1
fi

log "Accepted; stapling"
xcrun stapler staple "$TARGET"
xcrun stapler validate "$TARGET"
[ "$SUBMIT" != "$TARGET" ] && rm -f "$SUBMIT"
log "Notarized and stapled: ${TARGET#$ROOT/}"
