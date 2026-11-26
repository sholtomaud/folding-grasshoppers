#!/usr/bin/env bash
# Sign CardboardCAD.app inside-out with Hardened Runtime. Never uses --deep to sign.
#
#   SIGN_IDENTITY="Developer ID Application: Name (TEAMID)" scripts/sign.sh [path/to/App.app]
#   SIGN_IDENTITY=- scripts/sign.sh      # ad-hoc, for local/CI layout checks only
set -euo pipefail
source "$(dirname "$0")/config.sh"

APP="${1:-$APP_BUNDLE}"
[ -d "$APP" ] || die "app not found: $APP"
resolve_identity

APP_ENTITLEMENTS="$ROOT/app/$APP_NAME/$APP_NAME.entitlements"
PY_ENTITLEMENTS="$ROOT/scripts/python.entitlements"
PY_HOME="$APP/$(/usr/libexec/PlistBuddy -c 'Print :CardboardPythonHome' "$APP/Contents/Info.plist" 2>/dev/null || python_home_rel)"
INTERPRETER="$PY_HOME/bin/python3.12"
[ -x "$INTERPRETER" ] || die "interpreter not found: $INTERPRETER"

if [ "$SIGN_IDENTITY" = "-" ]; then
  TS="--timestamp=none"   # ad-hoc signatures cannot be timestamped
  # Ad-hoc builds are for this Mac only and are never notarized, so they skip
  # the Hardened Runtime. With it on, library validation compares Team IDs,
  # and recent macOS refuses to load an ad-hoc library (rhino3dm) into an
  # ad-hoc process: "mapping process and mapped file (non-platform) have
  # different Team IDs" (seen on an M4 Mac, 2026-11-26; macOS 15 CI runners
  # still allowed it). Developer ID signing always uses the Hardened Runtime,
  # and there every file shares one Team ID. ADHOC_HARDENED=1 keeps it on.
  if [ "${ADHOC_HARDENED:-0}" = "1" ]; then RUNTIME_OPT=(--options runtime); else RUNTIME_OPT=(); fi
else
  TS="--timestamp"
  RUNTIME_OPT=(--options runtime)
fi

sign() { codesign --force $TS ${RUNTIME_OPT[@]+"${RUNTIME_OPT[@]}"} --sign "$SIGN_IDENTITY" "$@"; }
[ "${#RUNTIME_OPT[@]}" -gt 0 ] || log "Ad-hoc signing without Hardened Runtime (local build; never notarized)"

is_macho() { file -b "$1" | grep -q 'Mach-O'; }

# 1. Every Mach-O in the runtime except the interpreter, detected with `file`
#    (extensions are not reliable). Deepest paths first = inside-out.
log "Signing Mach-O files in ${PY_HOME#$APP/}"
LIST="$(mktemp)"
trap 'rm -f "$LIST"' EXIT
find "$PY_HOME" -type f -print0 | while IFS= read -r -d '' f; do
  if is_macho "$f"; then printf '%s\n' "$f"; fi
done | awk '{ print gsub("/", "/") "\t" $0 }' | sort -rn | cut -f2- > "$LIST"
COUNT=0
while IFS= read -r f; do
  [ "$f" = "$INTERPRETER" ] && continue
  sign "$f"
  COUNT=$((COUNT + 1))
done < "$LIST"
log "Signed $COUNT libraries"

# 2. The interpreter last within the runtime, with its own entitlements.
log "Signing interpreter"
sign --entitlements "$PY_ENTITLEMENTS" "$INTERPRETER"

# 3. Any other nested code (frameworks/dylibs Xcode embedded), if present.
if [ -d "$APP/Contents/Frameworks" ]; then
  find "$APP/Contents/Frameworks" -maxdepth 1 \( -name '*.framework' -o -name '*.dylib' \) -print0 |
    while IFS= read -r -d '' f; do sign "$f"; done
fi

# 4. The app itself.
log "Signing $APP_NAME.app"
sign --entitlements "$APP_ENTITLEMENTS" "$APP"

log "Verifying signature"
codesign --verify --deep --strict --verbose=2 "$APP"

# Library validation needs every library to share the interpreter's Team ID.
team_of() {
  local t
  t="$(codesign -dv "$1" 2>&1 | sed -n 's/^TeamIdentifier=//p')"
  printf '%s' "${t:-none}"
}
WANT_TEAM="$(team_of "$INTERPRETER")"
BAD=0
while IFS= read -r f; do
  got="$(team_of "$f")"
  if [ "$got" != "$WANT_TEAM" ]; then
    printf 'error: %s has TeamIdentifier=%s, interpreter has %s\n' "${f#$APP/}" "$got" "$WANT_TEAM" >&2
    BAD=1
  fi
done < "$LIST"
[ "$BAD" -eq 0 ] || die "signing left mixed Team IDs; the engine would fail to load these libraries"
log "All $((COUNT + 1)) runtime Mach-O files share TeamIdentifier=$WANT_TEAM"
