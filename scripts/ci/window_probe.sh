#!/usr/bin/env bash
# Launch the built app with CARDBOARD_WINDOW_PROBE set and check, in the real
# window, that the chat's message box is fully visible and above the status bar
# at several window sizes. Screenshots go to build/window-probe/ and are also
# printed (small JPEG, base64) between markers so they can be read from the log.
#
#   scripts/ci/window_probe.sh build/app/CardboardCAD.app
set -uo pipefail
APP="${1:?path to .app}"
BIN="$APP/Contents/MacOS/CardboardCAD"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="$ROOT/build/window-probe"
rm -rf "$OUT"; mkdir -p "$OUT"
pkill -f cardboard_engine.server 2>/dev/null || true

CARDBOARD_WINDOW_PROBE="$OUT" "$BIN" > "$OUT/app.log" 2>&1 &
APP_PID=$!

SHOT=0
for _ in $(seq 1 480); do   # up to 120 s
  for ready in "$OUT"/*.ready; do
    [ -e "$ready" ] || continue
    name="$(basename "$ready" .ready)"
    [ -e "$OUT/$name.png" ] && continue
    win="$(head -1 "$ready")"
    if screencapture -x -o -l "$win" "$OUT/$name.png" 2>>"$OUT/screencapture.log"; then SHOT=1; fi
    [ -s "$OUT/$name.png" ] || rm -f "$OUT/$name.png"
    touch "$OUT/$name.png.tried"
  done
  [ -e "$OUT/done" ] && break
  kill -0 "$APP_PID" 2>/dev/null || break
  sleep 0.25
done
# The probe ends by choosing the Cmd+Q menu item: the app must quit and stop the engine.
QUIT="ok"
for _ in $(seq 1 40); do kill -0 "$APP_PID" 2>/dev/null || break; sleep 0.25; done
if kill -0 "$APP_PID" 2>/dev/null; then QUIT="FAIL: app still running 10 s after Cmd+Q"; kill -9 "$APP_PID" 2>/dev/null; fi
sleep 1
if pgrep -f cardboard_engine.server >/dev/null; then
  [ "$QUIT" = "ok" ] && QUIT="FAIL: engine still running after Cmd+Q"
  pkill -9 -f cardboard_engine.server 2>/dev/null || true
fi
grep -q '"found" : true' "$OUT/quit-menu.json" 2>/dev/null || QUIT="FAIL: no enabled Cmd+Q menu item ($QUIT)"
echo "$QUIT" > "$OUT/quit.txt"

python3 - "$OUT" <<'EOF'
import base64, glob, json, os, subprocess, sys
out = sys.argv[1]
files = sorted(glob.glob(os.path.join(out, "probe-*.json")))
failed = not files
print("### Window probe: chat message box")
print()
print("| Window | Content | Message box (y, h, visible h) | Status bar top | Result |")
print("|---|---|---|---|---|")
for f in files:
    d = json.load(open(f))
    if "requested" not in d:
        print(f"| {os.path.basename(f)} | | | | {d} |"); failed = True; continue
    r, c, lay = d["requested"], d.get("composer") or {}, d["contentLayoutRect"]
    box = f'{c.get("y", 0):.0f}, {c.get("h", 0):.0f}, {c.get("visibleH", 0):.0f}' if c else "not found"
    res = "ok" if d["ok"] else "FAIL: " + "; ".join(d["problems"])
    print(f'| {r["w"]:.0f} x {r["h"]:.0f} | {lay["w"]:.0f} x {lay["h"]:.0f} | {box} | {d["statusBarTop"]:.0f} | {res} |')
    failed = failed or not d["ok"]
print()
for f in files:
    d = json.load(open(f))
    if not d.get("ok", False):
        print(f"<details><summary>{os.path.basename(f)} view tree</summary>\n\n```")
        print("\n".join(d.get("tree", [])[:400]))
        print("```\n</details>\n")
sys.stdout.flush()
# Small JPEGs for the log (stderr keeps them out of the step summary).
for png in sorted(glob.glob(os.path.join(out, "probe-*.png"))):
    jpg = png[:-4] + ".jpg"
    subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "55", "-Z", "1000", png, "--out", jpg],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.path.exists(jpg):
        sys.stderr.write(f"=====BEGIN {os.path.basename(jpg)}=====\n")
        sys.stderr.write(base64.encodebytes(open(jpg, "rb").read()).decode())
        sys.stderr.write(f"=====END {os.path.basename(jpg)}=====\n")
quit = open(os.path.join(out, "quit.txt")).read().strip()
print(f"Cmd+Q (menu item, then app and engine gone): {quit}")
failed = failed or quit != "ok"
sys.exit(1 if failed else 0)
EOF
STATUS=$?
echo "screencapture used: $SHOT" >&2
cat "$OUT/screencapture.log" >&2 2>/dev/null || true
tail -20 "$OUT/app.log" >&2 2>/dev/null || true
exit $STATUS
