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
else
  TS="--timestamp"
fi

sign() { codesign --force $TS --options runtime --sign "$SIGN_IDENTITY" "$@"; }

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
