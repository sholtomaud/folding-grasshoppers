# CardboardCAD

A native macOS app for [Fold Theory](https://foldtheory.com) that ships its
own Python engine. Grasshopper (Rhino 8) talks to the same engine through the
**Hops** component, so the app and your Grasshopper definitions share one
set of calculations.

- Works on a Mac with **no Python, Homebrew or pip**: the app carries its own
  CPython 3.12 inside `CardboardCAD.app`.
- The engine listens on `http://127.0.0.1:5001` (your Mac only, never the network).
- Hops components appear in Grasshopper under **Fold Theory > CardboardCAD**
  with the Fold Theory icon.

Full milestone spec: [SPEC.md](./SPEC.md). Build and release details:
[scripts/README.md](./scripts/README.md). Rules for contributors and AI
agents: [AGENTS.md](./AGENTS.md).

---

## 1. Install the app (users)

Requirements: macOS 14 Sonoma or later on Apple silicon (M1 or newer).

1. Download `CardboardCAD.dmg`.
2. Open it and drag **CardboardCAD** onto **Applications**.
3. Open CardboardCAD from Applications. macOS shows the usual "downloaded
   from the internet" prompt once; click **Open**.
4. Wait for the status dot in the toolbar to turn **green** ("Running on
   5001"). The first launch can take up to 20 seconds; later launches take a
   few seconds.

| Dot | Meaning |
|---|---|
| grey | engine stopped |
| amber | starting or restarting |
| green | running (hover for the engine version) |
| red | failed: the message says why, with **Open Settings** or **View Logs** |

Quitting CardboardCAD stops the engine too.

### Settings (⌘,)

- **Port**: default `5001`. Change it if something else uses 5001; the
  engine restarts on the new port. Hops URLs change with it.
- **Copy Hops URL**: one button per component.
- **Restart Engine** and **Open Logs Folder** (`~/Library/Logs/CardboardCAD/engine.log`).

The **Engine** menu has the same Restart and Show Logs actions.

## 2. Connect Grasshopper (Hops)

1. In Rhino 8, run `PackageManager`, search for **Hops**, install it, and
   restart Rhino. (Rhino 8 usually has it already.)
2. Start CardboardCAD and wait for the green dot.
3. In CardboardCAD **Settings**, click **Copy Hops URL** next to the
   component you want, for example `http://127.0.0.1:5001/hello`.
4. In Grasshopper, place a **Hops** component (Params > Util > Hops),
   right-click it, choose **Path**, and paste the URL.
5. The component rebuilds with the engine's inputs and outputs and shows the
   Fold Theory icon.

Available components:

| URL | Inputs | Outputs |
|---|---|---|
| `/hello` | Name (text) | Message |
| `/cut_estimate` | Lc, Ls, N, S, Vc, Vs, Tp, k, Ps, R | T, Cm, Cmach, C |
| `/echo` | N (number) | N (same number) |

Invalid inputs (negative lengths, zero speeds) turn the Hops component red
with the reason, rather than returning zeros.

## 3. Hello world demo

Three ways to see the engine answer, from easiest to most technical:

**In the app.** Type a name in the **Hello world** box and click **Say hello**:

> Hello, Ada, from CardboardCAD by Fold Theory

**In Grasshopper.** Point a Hops component at `http://127.0.0.1:5001/hello`,
connect a Panel with some text to **Name**, and a Panel to **Msg**.

**From Terminal.** With the app running:

```bash
python3 scripts/demo_hello.py --name Grasshopper
```

```text
health: {'status': 'healthy'}

Hops component at http://127.0.0.1:5001/hello
  Fold Theory > CardboardCAD > Hello (icon: yes)
  in : Name (Name, Text)
  out: Message (Msg, Text)

Hops solve  -> Hello, Grasshopper, from CardboardCAD by Fold Theory
App route   -> Hello, Grasshopper, from CardboardCAD by Fold Theory
```

The script does exactly what Hops does (GET the component, POST `/solve`),
so if it works, Grasshopper will too.

## 4. `cut_estimate` reference vector

Use this to check the app form, Hops and the API agree.

| Input | Value |
|---|---|
| Cut length `Lc` | 2000 mm |
| Score length `Ls` | 500 mm |
| Pierce count `N` | 10 |
| Sheet count `S` | 2 |
| Cut speed `Vc` | 20 mm/s |
| Score speed `Vs` | 40 mm/s |
| Pierce time `Tp` | 0.5 s |
| Travel overhead `k` | 0.15 |
| Sheet price `Ps` | 3.50 |
| Machine rate `R` | 60 per hour |

`t_s = (2000/20 + 500/40 + 10 × 0.5) × 1.15 = 135.125 s`

| Output | Expected |
|---|---|
| Time `T` | 2.2521 min |
| Material cost `Cm` | 7.0000 |
| Machine cost `Cmach` | 2.2521 |
| Total cost `C` | 9.2521 |

## 5. Troubleshooting

| Symptom | Fix |
|---|---|
| Red dot "Port 5001 is in use" | Something else is on 5001 (`lsof -iTCP:5001 -sTCP:LISTEN` shows what). Quit it, or pick another port in Settings and re-copy the Hops URLs. |
| Red dot "Engine crashed 4 times within 60 s" | Click **View Logs** and send `engine.log` to the developers. **Engine > Restart Engine** tries again. |
| Hops component stays empty | Check the dot is green and the URL matches the port in Settings. Open the URL in a browser: it should show JSON. |
| Status stuck on amber | Wait 20 s on first launch. If it turns red, see the logs. |

---

## 6. Developer setup

Requirements: macOS 14+ on Apple silicon, Xcode 15.4 or newer,
[uv](https://docs.astral.sh/uv/) (`brew install uv` or the standalone
installer), and Node.js 22.18+ for the proof checks.

```bash
git clone https://github.com/sholtomaud/folding-grasshoppers
cd folding-grasshoppers
git config core.hooksPath .githooks          # test gates on commit/push

# Engine (Python)
cd engine && uv run pytest && cd ..

# Bundled runtime: downloads pinned CPython, verifies SHA-256, installs locked deps
scripts/build_python_runtime.sh               # -> build/python-runtime/

# Swift package tests
cd app/CardboardCADKit && swift test && cd ../..

# Run the app from Xcode
open app/CardboardCAD.xcodeproj                # scheme CardboardCAD, Run (⌘R)
```

Debug runs from Xcode use `build/python-runtime` through the
`CARDBOARD_DEV_RUNTIME` variable set in the shared scheme, so run
`build_python_runtime.sh` first. Release builds only use the runtime inside
the bundle.

Run the engine on its own (no app) while working on Python:

```bash
cd engine && uv run python -m cardboard_engine.server --port 5001
```

Everything CI checks, locally: `scripts/verify.sh`.

### Building a release

You need a **Developer ID Application** certificate in your keychain and a
notarytool profile:

```bash
xcrun notarytool store-credentials cardboardcad-notary \
  --apple-id you@example.com --team-id ABCDE12345 --password <app-specific-password>

TEAM_ID=ABCDE12345 NOTARY_PROFILE=cardboardcad-notary scripts/release.sh
# -> build/CardboardCAD.dmg (signed, notarized, stapled), then verify_bundle.sh
```

Bundle ID defaults to `com.example.cardboardcad`; set `BUNDLE_ID=...` to
override (see `scripts/config.sh`).

## Project layout

| Path | What |
|---|---|
| `engine/` | Python engine: Flask + ghhops_server, components, pytest |
| `app/CardboardCADKit/` | Swift package: engine process manager, HTTP client, logs (XCTest) |
| `app/CardboardCAD/` | SwiftUI app target |
| `scripts/` | runtime build, app build, sign, notarize, DMG, verify, demo |
| `.github/workflows/` | `verify.yml` (tests, macOS build, e2e, proof gate), `uplift.yml` (cockpit) |
