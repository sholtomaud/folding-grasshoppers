#!/usr/bin/env bash
# Try each runtime location with ad-hoc signing and report what codesign says
# and whether the engine starts from the signed bundle. Informational: the
# authoritative answer needs a Developer ID signature and a notarization pass.
#
#   scripts/ci/layout_probe.sh build/app/CardboardCAD.app >> "$GITHUB_STEP_SUMMARY"
set -uo pipefail
SRC="${1:?path to built .app}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WORK="$ROOT/build/layout-probe"
CUR="$(/usr/libexec/PlistBuddy -c 'Print :CardboardPythonHome' "$SRC/Contents/Info.plist")"

echo "### Runtime layout probe (ad-hoc signed)"
echo
echo "| Layout | sign.sh | codesign --verify --deep --strict | engine starts |"
echo "|---|---|---|---|"
for layout in frameworks helpers resources; do
  case "$layout" in
    frameworks) REL="Contents/Frameworks/python" ;;
    helpers)    REL="Contents/Helpers/python" ;;
    resources)  REL="Contents/Resources/python" ;;
  esac
  rm -rf "$WORK"; mkdir -p "$WORK"
  APP="$WORK/CardboardCAD.app"
  ditto "$SRC" "$APP"
  if [ "$REL" != "$CUR" ]; then
    mkdir -p "$APP/$(dirname "$REL")"
    mv "$APP/$CUR" "$APP/$REL"
    rmdir "$APP/Contents/Frameworks" 2>/dev/null || true
    /usr/libexec/PlistBuddy -c "Set :CardboardPythonHome $REL" "$APP/Contents/Info.plist"
  fi
  codesign --remove-signature "$APP" 2>/dev/null || true
  if SIGN_IDENTITY=- PYTHON_LAYOUT="$layout" "$ROOT/scripts/sign.sh" "$APP" > "$WORK/sign.log" 2>&1; then S="ok"; else S="FAIL: $(grep -m1 -iE 'error|fail|not signed' "$WORK/sign.log" | cut -c1-120)"; fi
  if codesign --verify --deep --strict "$APP" > "$WORK/verify.log" 2>&1; then V="ok"; else V="FAIL: $(head -1 "$WORK/verify.log" | cut -c1-120)"; fi
  if "$APP/$REL/bin/python3.12" -I -B -u "$ROOT/scripts/smoke_engine.py" "$APP/$REL/bin/python3.12" > "$WORK/run.log" 2>&1; then R="ok"; else R="FAIL: $(tail -1 "$WORK/run.log" | cut -c1-120)"; fi
  echo "| \`$REL\` | $S | $V | $R |"
done
rm -rf "$WORK"
