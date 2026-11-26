#!/usr/bin/env bash
# Release checks for the app and DMG (SPEC.md 10). Exit non-zero on any failure.
#
#   scripts/verify_bundle.sh [App.app] [App.dmg]
#   LOCAL_ONLY=1 scripts/verify_bundle.sh   # skip Gatekeeper (spctl) checks,
#                                           # e.g. for ad-hoc or un-notarized builds
set -uo pipefail
source "$(dirname "$0")/config.sh"

APP="${1:-$APP_BUNDLE}"
DMG="${2:-$DMG_PATH}"
FAILED=0
pass() { printf 'PASS  %s\n' "$*"; }
fail() { printf 'FAIL  %s\n' "$*"; FAILED=1; }

[ -d "$APP" ] || { fail "app not found: $APP"; exit 1; }

# 1. Signature, strict.
if codesign --verify --deep --strict --verbose=2 "$APP" 2>&1; then
  pass "codesign --verify --deep --strict"
else
  fail "codesign --verify --deep --strict"
fi

# 2. Gatekeeper.
if [ "${LOCAL_ONLY:-0}" != "1" ]; then
  OUT="$(spctl --assess --type execute -vv "$APP" 2>&1)"; echo "$OUT"
  if echo "$OUT" | grep -q "accepted" && echo "$OUT" | grep -q "source=Notarized Developer ID"; then
    pass "spctl execute: accepted, Notarized Developer ID"
  else
    fail "spctl execute"
  fi
  if [ -f "$DMG" ]; then
    OUT="$(spctl --assess --type open --context context:primary-signature -vv "$DMG" 2>&1)"; echo "$OUT"
    if echo "$OUT" | grep -q "accepted"; then pass "spctl open (dmg)"; else fail "spctl open (dmg)"; fi
  else
    fail "dmg not found: $DMG"
  fi
fi

# 3. No build-machine paths in any Mach-O load command.
#    Allowed: @rpath, @loader_path, @executable_path, /usr/lib/, /System/.
BAD=0
CHECKED=0
while IFS= read -r -d '' f; do
  file -b "$f" | grep -q 'Mach-O' || continue
  CHECKED=$((CHECKED + 1))
  # `otool -D` prints the file's own install name, which is not a dependency.
  SELF_ID="$(otool -D "$f" 2>/dev/null | tail -n +2 | head -1)"
  while IFS= read -r dep; do
    [ -z "$dep" ] && continue
    [ "$dep" = "$SELF_ID" ] && continue
    case "$dep" in
      @rpath/*|@loader_path/*|@executable_path/*|/usr/lib/*|/System/*) ;;
      *) printf '  %s -> %s\n' "${f#$APP/}" "$dep"; BAD=1 ;;
    esac
  done < <(otool -L "$f" 2>/dev/null | grep -E '^\s' | awk '{print $1}' | sort -u)
done < <(find "$APP" -type f -print0)
if [ "$BAD" -eq 0 ]; then
  pass "otool -L: $CHECKED Mach-O files reference only relative or system paths"
else
  fail "otool -L: absolute non-system paths found (listed above)"
fi

# 4. Hardened runtime on the app and the interpreter, no unexpected entitlements.
PY_HOME="$APP/$(/usr/libexec/PlistBuddy -c 'Print :CardboardPythonHome' "$APP/Contents/Info.plist" 2>/dev/null || python_home_rel)"
for bin in "$APP" "$PY_HOME/bin/python3.12"; do
  if codesign -dv "$bin" 2>&1 | grep -q "flags=.*runtime"; then
    pass "hardened runtime: ${bin#$APP/}"
  else
    fail "hardened runtime missing: ${bin#$APP/}"
  fi
  ENTS="$(codesign -d --entitlements - --xml "$bin" 2>/dev/null | grep -o '<key>[^<]*</key>' || true)"
  if echo "$ENTS" | grep -q "disable-library-validation"; then
    fail "disable-library-validation present on ${bin#$APP/} (needs documented justification)"
  fi
done

# 5. One signing identity for all code. With Hardened Runtime, library
#    validation refuses to load a library whose Team ID differs from the
#    process's ("mapping process and mapped file (non-platform) have different
#    Team IDs"), so a single file left with another signature breaks startup.
team_of() {
  local t
  t="$(codesign -dv "$1" 2>&1 | sed -n 's/^TeamIdentifier=//p')"
  printf '%s' "${t:-none}"
}
WANT_TEAM="$(team_of "$PY_HOME/bin/python3.12")"
MISMATCH=0
while IFS= read -r -d '' f; do
  file -b "$f" | grep -q 'Mach-O' || continue
  got="$(team_of "$f")"
  if [ "$got" != "$WANT_TEAM" ]; then
    printf '  %s: TeamIdentifier=%s (interpreter has %s)\n' "${f#$APP/}" "$got" "$WANT_TEAM"
    MISMATCH=1
  fi
done < <(find "$APP" -type f -print0)
if [ "$MISMATCH" -eq 0 ]; then
  pass "every Mach-O has TeamIdentifier=$WANT_TEAM"
else
  fail "Mach-O files with a different TeamIdentifier (listed above); re-run scripts/sign.sh on this exact bundle"
fi

[ "$FAILED" -eq 0 ] && echo "All checks passed." || echo "Some checks FAILED."
exit "$FAILED"
