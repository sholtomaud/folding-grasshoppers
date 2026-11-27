#!/usr/bin/env bash
# End-to-end checks of the built app on a macOS runner (SPEC.md 12, automatable subset):
#   1. engine turns healthy after launch, bound to 127.0.0.1 only
#   2. kill -9 of the engine -> automatic restart
#   3. quitting the app leaves no engine process
#   4. force-killing the app -> watchdog stops the engine within ~5 s
#
#   scripts/ci/e2e_app.sh "build/app/Fold Theory.app"
set -uo pipefail
APP="${1:?path to .app}"
BIN="$APP/Contents/MacOS/$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$APP/Contents/Info.plist")"
BUNDLE_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$APP/Contents/Info.plist")"
PORT=5001
LOG="$HOME/Library/Logs/Fold Theory/engine.log"
FAILED=0
pass() { printf 'PASS  %s\n' "$*"; }
fail() { printf 'FAIL  %s\n' "$*"; FAILED=1; }

engine_pids() { pgrep -f "cardboard_engine.server" || true; }

wait_healthy() { # seconds
  local i
  for i in $(seq 1 $(( $1 * 4 ))); do
    curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && return 0
    sleep 0.25
  done
  return 1
}

wait_no_engine() { # seconds
  local i
  for i in $(seq 1 $(( $1 * 4 ))); do
    [ -z "$(engine_pids)" ] && return 0
    sleep 0.25
  done
  return 1
}

launch() {
  "$BIN" >> build/e2e-app.log 2>&1 &
  APP_PID=$!
}

defaults write "$BUNDLE_ID" enginePort -int "$PORT"
mkdir -p build
pkill -f cardboard_engine.server 2>/dev/null || true

# 1. launch -> healthy
T0=$(date +%s)
launch
if wait_healthy 30; then
  pass "engine healthy $(( $(date +%s) - T0 )) s after launch"
else
  fail "engine not healthy within 30 s"
fi
LISTEN="$(lsof -nP -iTCP:$PORT -sTCP:LISTEN 2>/dev/null | tail -n +2)"
echo "$LISTEN"
if echo "$LISTEN" | grep -q "127.0.0.1:$PORT" && ! echo "$LISTEN" | grep -Eq "\*:$PORT|0\.0\.0\.0:$PORT"; then
  pass "listening on 127.0.0.1:$PORT only"
else
  fail "unexpected listen sockets"
fi
VERSION_JSON="$(curl -fsS "http://127.0.0.1:$PORT/version" || true)"
echo "version: $VERSION_JSON"
EST="$(curl -fsS -H 'Content-Type: application/json' \
  -d '{"cut_length":2000,"score_length":500,"pierce_count":10,"sheet_count":2,"sheet_price":3.5,"machine_rate":60}' \
  "http://127.0.0.1:$PORT/api/cut_estimate" || true)"
echo "cut_estimate: $EST"
echo "$EST" | grep -q '"total_cost":9.25208' && pass "cut_estimate reference vector" || fail "cut_estimate reference vector"

HELLO="$(curl -fsS -H 'Content-Type: application/json' -d '{"name":"CI"}' "http://127.0.0.1:$PORT/api/hello" || true)"
echo "hello: $HELLO"
echo "$HELLO" | grep -q "Hello, CI, from Fold Theory" && pass "hello world" || fail "hello world"
python3 scripts/demo_hello.py --port "$PORT" && pass "demo_hello.py (Hops protocol)" || fail "demo_hello.py"

# 2. kill -9 the engine -> restart
OLD="$(engine_pids | head -1)"
if [ -n "$OLD" ]; then
  kill -9 "$OLD"
  sleep 0.5
  if wait_healthy 15 && [ -n "$(engine_pids)" ] && [ "$(engine_pids | head -1)" != "$OLD" ]; then
    pass "engine restarted after kill -9"
  else
    fail "engine did not restart after kill -9"
  fi
else
  fail "no engine process found"
fi

# 3. graceful quit
if osascript -e "tell application id \"$BUNDLE_ID\" to quit" >/dev/null 2>&1; then
  for _ in $(seq 1 40); do kill -0 "$APP_PID" 2>/dev/null || break; sleep 0.25; done
  if kill -0 "$APP_PID" 2>/dev/null; then
    fail "app did not quit within 10 s"
    kill -9 "$APP_PID" 2>/dev/null
  fi
  if wait_no_engine 2; then pass "no engine process after quit"; else fail "engine left running after quit: $(engine_pids)"; fi
else
  echo "SKIP  graceful quit (osascript not permitted on this runner)"
  kill -9 "$APP_PID" 2>/dev/null
  wait_no_engine 8
fi

# 4. force-kill the app -> watchdog
launch
if wait_healthy 30; then
  kill -9 "$APP_PID"
  T0=$(date +%s)
  if wait_no_engine 6; then
    pass "watchdog stopped the engine $(( $(date +%s) - T0 )) s after the app was killed"
  else
    fail "engine still running 6 s after the app was killed: $(engine_pids)"
  fi
else
  fail "engine not healthy on relaunch"
  kill -9 "$APP_PID" 2>/dev/null
fi

pkill -f cardboard_engine.server 2>/dev/null || true
if [ "$FAILED" -ne 0 ]; then
  echo "---- engine.log (tail) ----"; tail -80 "$LOG" 2>/dev/null
  echo "---- app stdout/stderr ----"; tail -40 build/e2e-app.log 2>/dev/null
fi
exit "$FAILED"
