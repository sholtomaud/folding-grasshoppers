# SPEC: macOS SwiftUI app with a bundled Python engine (Hops-compatible)

## 1. Summary

Build a native macOS app (SwiftUI) that ships a **self-contained Python runtime and engine inside its `.app` bundle**. On launch, the app starts the Python engine as a child process. The engine is a Flask + `ghhops_server` HTTP server bound to `127.0.0.1`. The Swift app talks to it over localhost HTTP, and Grasshopper's **Hops** component can call the same endpoints. The app is distributed as a **Developer ID–signed, notarized, stapled DMG** (not the Mac App Store).

This milestone is about **infrastructure**: runtime bundling, process lifecycle, signing, notarization and one real endpoint, proven end to end. The cardboard geometry pipeline and the Claude chat come in later milestones and are out of scope here, except for the stubs listed below.

## 2. Goals

1. A user downloads a DMG, drags the app to /Applications, opens it, and it works with **no Python, Homebrew or pip on their machine**.
2. The engine starts automatically, shows healthy status in the UI, and shuts down cleanly when the app quits (no orphaned Python processes).
3. Grasshopper on the same Mac can use the engine via a Hops component pointed at `http://127.0.0.1:<port>/<endpoint>`.
4. One script builds, signs, notarizes and staples the app and DMG, reproducibly.

## 3. Non-goals (this milestone)

- Mac App Store distribution or App Sandbox.
- Windows or Linux builds.
- Claude chat integration (just leave a placeholder view).
- The geometry pipeline (slicing, nesting, DXF export). Only one numeric endpoint (`/cut_estimate`) is required.
- Auto-update (Sparkle can come later; keep the structure compatible).
- **Do not use PyInstaller, py2app or cx_Freeze.**

## 4. Decisions (defaults; flag if you need to change one)

| Item | Default |
|---|---|
| Min macOS | 14.0 (Sonoma) |
| Architecture | arm64 only for v1 (keep the scripts able to add x86_64 later) |
| Python | CPython 3.12.x from **astral-sh/python-build-standalone**, `install_only` variant, `aarch64-apple-darwin`. Pin the exact release tag **and** the SHA-256. |
| Python deps | Managed with `uv`; locked in `engine/uv.lock` / exported `requirements.lock` with hashes |
| HTTP server | Flask + `ghhops_server` (matches the existing server pattern) |
| Default port | `5001`, user-configurable in Settings. A fixed, known port is required because Hops users paste the URL. |
| Bind address | **`127.0.0.1` only. Never `0.0.0.0`.** |
| Swift networking | `URLSession` async/await, no third-party deps |
| Swift deps | None for this milestone |
| Sandbox | Off. Hardened Runtime **on**. |

Placeholders to fill in (ask the user if they're not in the repo): app name `CardboardCAD`, bundle ID `com.example.cardboardcad`, Apple Team ID, notarytool keychain profile name.

## 5. Repository layout

```
/
├── SPEC.md
├── app/                       # Xcode project (SwiftUI macOS app)
│   ├── CardboardCAD.xcodeproj
│   └── CardboardCAD/
│       ├── App.swift
│       ├── Engine/
│       │   ├── EngineManager.swift     # process lifecycle
│       │   ├── EngineClient.swift      # typed HTTP client
│       │   └── EngineConfig.swift      # port, paths, token
│       ├── Views/
│       │   ├── ContentView.swift       # status + estimate form + chat placeholder
│       │   └── SettingsView.swift      # port, open logs, copy Hops URL
│       └── CardboardCAD.entitlements
├── engine/                    # Python package (source of truth)
│   ├── pyproject.toml
│   ├── uv.lock
│   ├── cardboard_engine/
│   │   ├── __init__.py        # __version__
│   │   ├── server.py          # Flask app + Hops registration + main()
│   │   ├── lifecycle.py       # parent-PID watchdog, shutdown token
│   │   └── components/
│   │       ├── __init__.py
│   │       └── cut_estimate.py
│   └── tests/
│       ├── test_cut_estimate.py
│       └── test_server.py      # Flask test client: /health, Hops GET metadata, POST /solve
├── scripts/
│   ├── build_python_runtime.sh # fetch + verify + install deps + precompile
│   ├── build_app.sh            # xcodebuild archive/export, embed runtime
│   ├── sign.sh                 # inside-out signing of every Mach-O
│   ├── notarize.sh             # notarytool submit --wait, staple
│   ├── make_dmg.sh
│   ├── release.sh              # runs all of the above in order
│   └── verify_bundle.sh        # codesign/spctl/otool checks
└── .github/workflows/release.yml   # optional, see §11
```

## 6. Python engine

### 6.1 Server (`server.py`)

- Flask app with `ghhops_server.Hops(app)`.
- CLI: `python -m cardboard_engine.server --port 5001 --parent-pid <pid>`. The shutdown token comes in via the env var `ENGINE_SHUTDOWN_TOKEN`, not argv, so it doesn't show up in `ps`.
- Bind to `127.0.0.1` only. If the port is in use, exit with **code 98** and print one JSON line to stderr: `{"error":"port_in_use","port":5001}`.
- After the server is listening, print one line to stdout: `ENGINE_READY {"port":5001,"version":"x.y.z"}`. Swift waits for this line *or* a healthy `/health`, whichever comes first.
- Logging: plain text to stdout/stderr, no emoji. The Swift side captures it to a file.

### 6.2 Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | `{"status":"healthy"}` |
| GET | `/version` | `{"engine": __version__, "python": sys.version, "packages": {...}}`, with key package versions |
| POST | `/shutdown` | Requires header `X-Engine-Token` matching `ENGINE_SHUTDOWN_TOKEN`. Returns 202, then shuts down gracefully. 403 otherwise. |
| Hops | `/cut_estimate` | See 6.3. Must work from a Grasshopper Hops component. |
| Hops | `/echo` | Smoke test: number in, the same number out |

### 6.3 `/cut_estimate` component

Inputs (`HopsNumber`, mm and seconds):

| Name | Nick | Default |
|---|---|---|
| Cut length (mm) | `Lc` | — |
| Score length (mm) | `Ls` | 0 |
| Pierce count | `N` | 0 |
| Sheet count | `S` | 1 |
| Cut speed (mm/s) | `Vc` | 20 |
| Score speed (mm/s) | `Vs` | 40 |
| Pierce time (s) | `Tp` | 0.5 |
| Travel overhead (fraction) | `k` | 0.15 |
| Sheet price | `Ps` | 0 |
| Machine rate (per hour) | `R` | 0 |

Outputs: `Time (min)` `T`, `Material cost` `Cm`, `Machine cost` `Cmach`, `Total cost` `C`.

```
t_s   = (Lc / Vc + Ls / Vs + N * Tp) * (1 + k)
T     = t_s / 60
Cm    = S * Ps
Cmach = (t_s / 3600) * R
C     = Cm + Cmach
```

Validation: negative inputs and zero speeds must **raise an error** that Hops shows as a component error. Do not return zeros silently. Put the pure function in `components/cut_estimate.py` with unit tests, separate from the Hops wrapper.

### 6.4 Lifecycle safety (`lifecycle.py`)

- **Parent watchdog:** a daemon thread checks every 2 s whether `--parent-pid` is still alive (`os.kill(pid, 0)`) and whether `os.getppid()` has changed. If the parent is gone, it shuts down. This prevents orphans if the app crashes or is force-quit.
- Handle `SIGTERM` gracefully (finish in-flight requests, exit 0 within 3 s).

### 6.5 Bytecode and writes

- The bundle is signed, so **nothing may write inside it at runtime.** Launch with `PYTHONDONTWRITEBYTECODE=1`, and precompile all `.pyc` at build time (`compileall`) *before* signing.
- Any runtime files (cache, temp) go to `~/Library/Application Support/<AppName>/` or `~/Library/Caches/<AppName>/`, passed in via env vars.

## 7. Python runtime build (`scripts/build_python_runtime.sh`)

1. Download the pinned python-build-standalone `install_only` tarball. Verify the SHA-256 and **fail hard if it doesn't match.** Cache it in `build/cache/`.
2. Extract it to `build/python-runtime/`.
3. Install the engine's locked deps into that runtime's own `site-packages`, using that interpreter (e.g. `uv pip install --python build/python-runtime/bin/python3 --require-hashes -r requirements.lock`), then install `engine/` itself (non-editable).
4. Strip what isn't needed: `test/` and `tests/` directories in the stdlib and packages, `idlelib`, `tkinter` and Tcl/Tk, `ensurepip`, `__pycache__` (regenerated next), headers, and static `.a` libs. Keep a list of what's removed in the script.
5. Run `python -m compileall -q -j0` over the runtime.
6. Print the final size. Warn above 300 MB.
7. Smoke-test: run `build/python-runtime/bin/python3 -I -c "import cardboard_engine, flask, ghhops_server"`.

Always run the interpreter with **`-I`** (isolated mode: ignores `PYTHON*` env vars and the user site, and doesn't add the CWD to the path), and set the env explicitly (§8.2).

## 8. Swift app

### 8.1 Bundle placement

The runtime is copied into the app at build time. Apple's guidance is that code (Mach-O files) belongs in code locations rather than `Contents/Resources`. Use:

```
CardboardCAD.app/Contents/
├── MacOS/CardboardCAD
├── Frameworks/python/          # the whole python-build-standalone tree
│   ├── bin/python3.12
│   └── lib/python3.12/...
└── Resources/ (assets only)
```

If `codesign --verify --strict` or notarization rejects this layout, try `Contents/Helpers/python/` next. Document the layout that works and the reason in `scripts/README.md`. **This must be confirmed by a real notarization pass, not assumed.**

Add the copy as an Xcode "Run Script" build phase (or do it in `build_app.sh` after export, before signing). Pick one and document it.

### 8.2 `EngineManager` (an `@Observable` class on the main actor)

State: `.stopped`, `.starting`, `.running(port)`, `.failed(reason)`, `.restarting(attempt)`.

Start:
1. Resolve the interpreter path from `Bundle.main` (never hard-code `/Applications/...`).
2. Generate a random 32-byte shutdown token for this launch.
3. `Process` with:
   - `executableURL` = bundled `python3.12`
   - `arguments` = `["-I", "-m", "cardboard_engine.server", "--port", "\(port)", "--parent-pid", "\(getpid())"]`
   - `environment` set **explicitly**, without inheriting the user's shell env: `PYTHONDONTWRITEBYTECODE=1`, `PYTHONUNBUFFERED=1`, `ENGINE_SHUTDOWN_TOKEN`, `ENGINE_DATA_DIR`, `ENGINE_CACHE_DIR`, `HOME`, `TMPDIR`, and a minimal `PATH=/usr/bin:/bin`.
   - stdout/stderr through `Pipe`s, appended to `~/Library/Logs/<AppName>/engine.log` (rotated at 5 MB, keep 3 files). Watch stdout for `ENGINE_READY`.
4. Readiness: wait for `ENGINE_READY`, or poll `GET /health` with backoff (100 ms doubling, capped at 1 s). Overall timeout **20 s**, since a cold first launch on a notarized app can be slow.
5. Exit code 98 means `.failed("Port \(port) is in use")` with a button to open Settings.

Crash handling: if the process exits unexpectedly while `.running`, restart automatically with backoff (1 s, 2 s, 4 s), at most 3 times in 60 s. After that, `.failed` with a "View logs" action.

Stop (on `applicationWillTerminate`, and from a Restart menu item):
1. `POST /shutdown` with the token (1 s timeout).
2. If still running after 3 s, `terminate()` (SIGTERM).
3. If still running after 2 more seconds, SIGKILL.

Use `NSApplicationDelegateAdaptor`, return `.terminateLater` from `applicationShouldTerminate`, and reply once shutdown completes (with a 6 s hard cap).

### 8.3 `EngineClient`

- Async methods: `health()`, `version()`, `cutEstimate(_ input: CutEstimateInput) -> CutEstimateResult`.
- For `/cut_estimate`, the Swift app may call a **plain JSON route** that wraps the same pure function (add `POST /api/cut_estimate` taking and returning plain JSON) instead of speaking the Hops data-tree protocol. Hops and the app share the Python function, not the wire format.
- Base URL `http://127.0.0.1:<port>`. Plain HTTP to loopback needs an ATS exception: add `NSAppTransportSecurity` → `NSAllowsLocalNetworking = YES` to Info.plist (nothing broader).

### 8.4 UI (minimal)

- Toolbar status dot: grey (stopped), amber (starting/restarting), green (running), red (failed), with a tooltip showing the engine version.
- Main view: a form for the `cut_estimate` inputs and a results panel. A disabled "Chat (coming soon)" area.
- Settings: port field (restarts the engine on change), a **"Copy Hops URL"** button for each Hops endpoint, "Open logs folder", "Restart engine".
- Menu bar: Engine → Restart / Show logs.

## 9. Signing (`scripts/sign.sh`)

Developer ID Application certificate, Hardened Runtime, secure timestamp. **Sign inside-out, and do not use `codesign --deep` to sign** (only to verify).

1. Find every Mach-O in `Contents/Frameworks/python/` (use `file` to detect Mach-O; don't rely on extensions, because some are extensionless). This covers `.so`, `.dylib` and the `python3.12` binary.
2. Sign each library: `codesign --force --timestamp --options runtime --sign "$IDENTITY" <file>`.
3. Sign the interpreter executable last within the runtime, with `scripts/python.entitlements`.
4. Sign the main app with `app/CardboardCAD/CardboardCAD.entitlements`.

**Entitlements:** start with **no** hardened-runtime exceptions on either the interpreter or the app. Because every library is re-signed with our Team ID, library validation should pass. Only if a specific test fails may you add the narrowest exception (e.g. `com.apple.security.cs.allow-unsigned-executable-memory` on the interpreter only). Record which test failed and why in `scripts/README.md`. Never add `disable-library-validation` without that documented justification.

## 10. Notarization and DMG

- `scripts/notarize.sh`: zip the app with `ditto -c -k --keepParent`, run `xcrun notarytool submit --keychain-profile "$PROFILE" --wait`. On failure, automatically fetch and print `notarytool log <id>`. On success, `xcrun stapler staple` the app.
- `scripts/make_dmg.sh`: `hdiutil create` a compressed DMG containing the app and an `/Applications` symlink. Sign the DMG, notarize it, and staple it.
- `scripts/verify_bundle.sh` must pass:
  - `codesign --verify --deep --strict --verbose=2 CardboardCAD.app`
  - `spctl --assess --type execute -vv CardboardCAD.app` → `accepted, source=Notarized Developer ID`
  - `spctl --assess --type open --context context:primary-signature -vv CardboardCAD.dmg`
  - No absolute paths to the build machine in any Mach-O: `otool -L` over each Mach-O must show only `@rpath`, `@loader_path`, `@executable_path` or `/usr/lib` / `/System` paths. Fail on anything else.

## 11. CI (optional, do after local release works)

A GitHub Actions `macos-14` workflow on tag push. It imports the Developer ID `.p12` into a temporary keychain from secrets, stores notarytool credentials (App Store Connect API key) from secrets, runs `scripts/release.sh`, and uploads the DMG as a release asset. Secrets must never be echoed in logs.

## 12. Tests and acceptance criteria

Automated:
- [ ] `pytest` passes in `engine/` (pure function, Flask test client for `/health`, `/version`, `/shutdown` auth, Hops metadata GET, and a Hops solve POST on `/echo` and `/cut_estimate`).
- [ ] Swift unit tests for `EngineClient` decoding and the `EngineManager` state machine (with the process layer behind a protocol so it can be mocked).

Manual, on a **clean Mac user account with no Python/Homebrew on PATH**, using the notarized DMG downloaded via a browser (so quarantine applies):
- [ ] App opens without Gatekeeper warnings beyond the standard "downloaded from the internet" prompt.
- [ ] Status turns green within 20 s of first launch and within 5 s on later launches.
- [ ] The `cut_estimate` form returns correct values for a known input (put the test vector in the README).
- [ ] In Grasshopper on the same Mac, a Hops component with the copied URL shows the correct inputs/outputs and computes matching results.
- [ ] Quitting the app leaves no `python3.12` process (`pgrep -f cardboard_engine` is empty).
- [ ] Force-quitting the app makes the engine exit within ~5 s (watchdog).
- [ ] Killing the engine with `kill -9` triggers an automatic restart; after 3 kills in 60 s the app shows the failed state with "View logs".
- [ ] Putting something else on port 5001 (`python3 -m http.server 5001`) gives a clear "port in use" message.
- [ ] `lsof -iTCP:5001 -sTCP:LISTEN` shows the engine bound to `127.0.0.1` only.
- [ ] `scripts/verify_bundle.sh` passes.

## 13. Work order

1. `engine/` package, components, tests (runs with a normal dev Python).
2. `build_python_runtime.sh`; smoke-test the bundled interpreter.
3. Swift app with `EngineManager` running the **dev** runtime path (behind a debug flag), UI working.
4. Embed the runtime in the app bundle; run unsigned locally.
5. `sign.sh` + `verify_bundle.sh`; fix layout and entitlements issues.
6. `notarize.sh` + `make_dmg.sh`; run the clean-account manual checklist.
7. CI (optional).

Commit at the end of each step with a short note on anything that differed from this spec.

## 14. Later milestones (for context only; do not build now)

- Claude chat panel (Anthropic Messages API with tool use; tools map to engine endpoints).
- Geometry endpoints: `/waffle_slice`, `/contour_stack`, `/unfold`, multi-sheet `/nest`, DXF/SVG export.
- Designer playbook and project-retrieval layer.
- Optional JAX surrogate models (watch bundle size; may need to ship as a separate download).
