# Build, sign and release scripts

| Script | What it does | Runs on |
|---|---|---|
| `build_python_runtime.sh` | Downloads pinned python-build-standalone, verifies SHA-256, installs `engine/requirements.lock` (hash-checked, wheels only) and the engine wheel, strips, precompiles, smoke-tests. Output `build/python-runtime/`. Cross mode (default when the target OS differs from the build machine's) never runs the target interpreter: `uv pip install --target --python-platform`, bytecode from a same-version CPython, smoke tests left to `smoke_runtime.sh`. | dev container (`make runtime`, cross), macOS with uv (CI, native), Linux (`TARGET=x86_64-unknown-linux-gnu`, CI smoke only) |
| `smoke_runtime.sh` | Imports + engine start/stop using `build/python-runtime`'s own interpreter in isolated mode; fails if bytecode was written. | macOS |
| `tools.sh` | Runs a Python/uv/Node command directly when uv is on PATH, otherwise in the dev container (`Containerfile`) via Apple's `container` CLI. | macOS, Linux |
| `build_app.sh` | `xcodebuild archive` (unsigned), copies the app out, embeds the runtime, records its location in Info.plist (`CardboardPythonHome`), smoke-tests the engine from inside the bundle. | macOS |
| `sign.sh` | Inside-out Hardened Runtime signing of every Mach-O (detected with `file`), interpreter last with `python.entitlements`, then the app. No `--deep` when signing. | macOS |
| `notarize.sh` | `ditto` zip, `notarytool submit --wait`; prints `notarytool log` on failure; staples on success. Works on `.app` or `.dmg`. | macOS |
| `make_dmg.sh` | `hdiutil` UDZO DMG with an `/Applications` link; signs, notarizes, staples it. | macOS |
| `verify_bundle.sh` | `codesign --verify --deep --strict`, `spctl` for app and DMG, `otool -L` path audit, hardened-runtime and entitlement checks. `LOCAL_ONLY=1` skips Gatekeeper. | macOS |
| `release.sh` | All of the above in order. | macOS |
| `smoke_engine.py` | Starts an interpreter's engine with the app's flags and clean env, checks `/health`, `/version`, `/api/cut_estimate`, SIGTERM exit 0. | any |
| `demo_hello.py` | Hello-world demo against a running engine using the Hops protocol. | any |
| `verify.sh` | Local gate used by git hooks: pytest, Swift tests, test floors, proof seal. Without uv on PATH the pytest/floor/seal half runs in the dev container. | any (Swift on macOS) |
| `ci/` | CI helpers: test floors, end-to-end app launch, layout probe. | |

Settings shared by all scripts are in `config.sh` (app name, bundle ID,
Team ID, notary profile, runtime layout). Everything can be overridden from
the environment.

## Pinned interpreter

CPython **3.12.13**, python-build-standalone release **20260718**,
`install_only` variant. SHA-256 values are in `build_python_runtime.sh`,
taken from that release's `SHA256SUMS`:

| Target | SHA-256 |
|---|---|
| aarch64-apple-darwin | `62aeee6161d57303a71a138b75fd5cc6fb8c89c4b1d9c7f0a052d89fa0b6652b` |
| x86_64-apple-darwin | `10b47148de86f9d87ba6e96a3db606ced90a206a3454d7d6d8fa68536a05d81f` |

To bump: change `PBS_TAG`, `PY_VERSION` and every SHA together.

## What `build_python_runtime.sh` strips

Logged per build to `build/python-runtime-removed.txt`:

- stdlib: `idlelib`, `tkinter`, `turtledemo`, `turtle.py`, `ensurepip`, `lib2to3`, `pydoc_data`
- Tcl/Tk: `lib/tcl*`, `lib/tk*`, `lib/itcl*`, `lib/thread*`, `libtcl*`, `libtk*`, `_tkinter*.so`
- tests: `test/` and `tests/` directories in the stdlib and site-packages
- `include/`, `share/`, `lib/pkgconfig`, all `*.a`
- `bin/` launchers other than `python`, `python3`, `python3.12` (they embed
  the build machine's path in their shebang), `python*-config`, `pip`
- all `__pycache__` (regenerated with `compileall --invalidation-mode unchecked-hash`)

## Bundle layout

```
CardboardCAD.app/Contents/
├── MacOS/CardboardCAD
├── Info.plist                  <- CardboardPythonHome = Contents/Resources/python
└── Resources/
    ├── Assets.car
    └── python/                 <- runtime (default since CI run 45736582336)
        ├── bin/python3.12
        └── lib/python3.12/...
```

`build_app.sh` writes the chosen location to Info.plist and the app reads it,
so switching layouts is `PYTHON_LAYOUT=frameworks|helpers|resources
scripts/build_app.sh` with no code change. Every Mach-O inside the runtime is
still signed individually by `sign.sh`, wherever it lives.

### Why not `Contents/Frameworks/python` (SPEC 8.1)

The first macOS CI run (Verify run 45736582336, 2026-11-26) built the app with
the runtime in `Contents/Frameworks/python`. Signing the libraries and the
interpreter worked, but signing the app failed:

```
CardboardCAD.app: bundle format unrecognized, invalid, or unsuitable
In subcomponent: .../Contents/Frameworks/python/lib/python3.12
```

codesign treats every directory under `Contents/Frameworks` as a nested bundle
to seal, and `lib/python3.12` (plain `.py` files) is not one. That is a
structural rejection, so no entitlement or flag fixes it. The CI
`Layout probe` step re-runs all three layouts on every build and prints the
result in the job log and summary.

| Layout | codesign --strict (ad-hoc, CI) | Notarization | Notes |
|---|---|---|---|
| `Contents/Frameworks/python` | **fails** (above) | not attempted | SPEC first choice |
| `Contents/Helpers/python` | **fails** (`code object is not signed at all`, Verify run 45737713477) | not attempted | SPEC fallback |
| `Contents/Resources/python` | passes (sign, `--verify --deep --strict`, engine and app e2e; Verify run 45737713477) | **not run yet** | default |

**Still NOT confirmed by notarization.** Record the first real
`notarytool` result here.

## Runtime copy: build_app.sh, not an Xcode build phase

The runtime is embedded by `build_app.sh` after `xcodebuild archive` and
before signing. Reasons: the Xcode project stays free of build-machine
paths and script sandboxing exceptions, Debug runs do not copy ~70 MB on
every build (they use `CARDBOARD_DEV_RUNTIME`), and `sign.sh` always sees the
final bundle.

## Entitlements

`app/CardboardCAD/CardboardCAD.entitlements` and `scripts/python.entitlements`
are both **empty**: Hardened Runtime with no exceptions. Every library in the
runtime is re-signed with our identity, so library validation should pass.

| Exception added | Binary | Failing test that justified it |
|---|---|---|
| none | | |

Never add `com.apple.security.cs.disable-library-validation` without a row
here; `verify_bundle.sh` fails if it is present.

## Interpreter flags

The app runs `python3.12 -I -B -u -m cardboard_engine.server`. `-I`
(isolated) ignores every `PYTHON*` environment variable, so
`PYTHONDONTWRITEBYTECODE` and `PYTHONUNBUFFERED` alone would do nothing;
`-B` and `-u` do the same job as flags. The variables are still set for
non-isolated debugging runs.

## Placeholders still to fill in

| Setting | Where | Current |
|---|---|---|
| Bundle ID | `scripts/config.sh`, Xcode project | `com.example.cardboardcad` |
| Apple Team ID | `TEAM_ID` env / `scripts/config.sh` | unset |
| notarytool profile | `NOTARY_PROFILE` env / `scripts/config.sh` | `cardboardcad-notary` |
