# CardboardCAD

CardboardCAD is a Mac app built for [Fold Theory](https://foldtheory.com).
Inside it is a small calculation engine written in Python. Grasshopper (the
visual programming tool in Rhino 8) talks to that engine through its **Hops**
component, so the app and your Grasshopper definitions use the same
calculations.

- Nothing else needs installing: the app carries its own copy of Python.
- The engine only listens on your own Mac, at `http://127.0.0.1:5001`.
  Nothing is reachable from the network.
- In Grasshopper, the components appear under **Fold Theory > CardboardCAD**
  with the Fold Theory icon.

> **Status (milestone 1):** there is **no downloadable release yet**. Until
> the first signed release is published, you get the app by building it on a
> Mac (section 7, about 10 minutes). Section 1 describes the download that
> will exist once a release is made.

Contents:

1. [Install the app](#1-install-the-app)
2. [First launch](#2-first-launch)
3. [Design with the Design Assistant](#3-design-with-the-design-assistant)
4. [Connect Grasshopper (Hops)](#4-connect-grasshopper-hops)
5. [Hello world demo](#5-hello-world-demo)
6. [Where CardboardCAD keeps its files](#6-where-cardboardcad-keeps-its-files)
7. [Build the app yourself](#7-build-the-app-yourself)
8. [Make a signed release (DMG)](#8-make-a-signed-release-dmg)
9. [Troubleshooting](#9-troubleshooting)
10. [`cut_estimate` reference values](#10-cut_estimate-reference-values)
11. [For developers](#11-for-developers)

---

## 1. Install the app

You need a Mac with Apple silicon (M1 or newer) running macOS 14 Sonoma or
later. To check, open the Apple menu  > **About This Mac**: "Chip" should
say Apple M-something, and "macOS" should be 14 or higher.

### Today: build it yourself

There is no download yet: the releases page
(https://github.com/sholtomaud/folding-grasshoppers/releases) is empty until
the first signed release is published. Follow
[section 7](#7-build-the-app-yourself) (about 10 minutes). It produces
`~/src/folding-grasshoppers/build/app/CardboardCAD.app`, which you copy to
`/Applications`.

### Later: download a release (not available yet)

Skip this until the releases page lists a release.

1. Go to the releases page:
   **https://github.com/sholtomaud/folding-grasshoppers/releases**
2. Under the newest release, open **Assets** and click `CardboardCAD.dmg`.
   Your browser saves it to your Downloads folder:
   `/Users/<you>/Downloads/CardboardCAD.dmg`.
3. Double-click `CardboardCAD.dmg` in Finder. A window opens showing
   **CardboardCAD** and an **Applications** folder.
4. Drag **CardboardCAD** onto **Applications**. The app is now at
   `/Applications/CardboardCAD.app`.
5. Eject the disk image: in the Finder sidebar, click the eject button next to
   **CardboardCAD 0.1.0**. You can then delete
   `~/Downloads/CardboardCAD.dmg`.

## 2. First launch

1. Open Finder > **Applications** and double-click **CardboardCAD**.
2. A release download shows the usual macOS prompt: *"CardboardCAD" is an app
   downloaded from the Internet. Are you sure you want to open it?* Click
   **Open**. You only see this once.
   (Your own unsigned build is blocked instead: see
   [section 7](#7-build-the-app-yourself).)
3. Look at the status bar along the bottom of the window. On the left, the
   engine dot turns **green** and says "Engine running · 127.0.0.1:5001",
   within about 20 seconds the first time and a few seconds after that.

| Engine dot (bottom left) | Meaning |
|---|---|
| grey | engine stopped |
| amber | starting, or restarting after a problem |
| green | running. Hover over it to see the engine version. |
| red | failed. The text says why, with an **Open Settings…** or **Show Logs** link. |

On the right, the status bar shows when Grasshopper last called the engine.
Hops never stays connected: it sends a request each time a component loads or
solves, so this is the way to check that Grasshopper can reach CardboardCAD.

| Grasshopper dot (bottom right) | Meaning |
|---|---|
| grey, "no calls yet" | no Hops component has reached the engine since it started |
| green, "last call 12 s ago" | Hops called within the last 2 minutes |
| grey, "last call 10 min ago" | Hops reached it before, but not recently |
| red, "failed (HTTP 500)" | the last call reached the engine but the component reported an error |

Quitting CardboardCAD (**CardboardCAD > Quit**, or ⌘Q) also stops the engine.

### Settings

Open them with **CardboardCAD > Settings…** (⌘,).

- **Port**: `5001` unless you change it. Type a number between 1024 and 65535
  and click **Apply**. The engine restarts on the new port, and every Hops URL
  changes to match.
- **Grasshopper Hops**: one row per component, with a **Copy Hops URL** button.
- **Restart Engine** and **Open Logs Folder**.

The **Engine** menu in the menu bar has **Restart Engine** (⇧⌘R) and
**Show Logs**.

## 3. Design with the Design Assistant

The main window has two halves:

- **Left: the Design Assistant**, a chat with Claude. You describe what you
  want to make in plain words. Claude decides which engine steps to run and
  with which numbers, then runs them.
- **Right: the preview**, which updates after every step:
  - **3D model** tab: the model, with the cardboard parts assembled on it
    (waffle ribs, stacked layers, or the folded net). Drag to turn it, scroll
    to zoom, double-click to reset the view.
  - **Cut sheets** tab: the flat parts as they will be cut. Red is cut,
    blue is score/fold, green is etch. After nesting there is one picture per
    sheet, with how much of the board is used.

Claude never draws geometry itself. It chooses the step and the numbers, and
the engine does the CAD work: the same engine Grasshopper uses through Hops.

### Step 1: get a Claude API key (once)

The assistant calls Claude over the internet with your own API key, and
Anthropic bills that key for each message (typically a few cents).

1. In your web browser, go to **https://console.anthropic.com/settings/keys**
   and sign in (or create an account and add billing under **Plans & Billing**).
2. Click **Create Key**, name it `CardboardCAD`, and click **Add**.
3. Click **Copy**. The key starts with `sk-ant-`. Keep this browser tab open
   until step 2 is done: the console only shows the key once.

### Step 2: put the key into CardboardCAD (once)

1. Open CardboardCAD (Finder > **Applications** > **CardboardCAD**).
2. Open **CardboardCAD > Settings…** (⌘,).
3. In the **Design Assistant (Claude)** section, click in the field that says
   *Paste your Claude API key*, press ⌘V, then click **Save**.
4. The row now shows `sk-ant-…` followed by the last four characters and
   "saved in Keychain". The key is stored in your login Keychain (open
   `/Applications/Utilities/Keychain Access.app` and search for
   `com.example.cardboardcad.claude` to see it). It is not stored anywhere else
   and is only sent to `api.anthropic.com`.
5. Optional: **Model** picks which Claude does the work. **Claude Opus 5.5** is
   the default and gives the best results. **Claude Sonnet 5.5** is faster and
   costs about half as much. **Thinking effort** trades speed and cost for
   care: Medium is a good default.
6. Close the Settings window.

macOS may ask *"CardboardCAD wants to use your confidential information
stored in "com.example.cardboardcad.claude" in your keychain."* the first time
after each new build. Type your Mac login password and click **Always Allow**.
This happens because your own builds are signed ad hoc, so macOS treats each
build as a new app.

### Step 3: make something

Wait for the green engine dot in the bottom-left corner. Then type in the box
at the bottom of the chat and press Return. For example:

> Make a cylinder stool 450 mm tall and 350 mm wide as a waffle from 6 mm
> board, then nest it on 1000 × 700 mm sheets and estimate the cost at 6 per
> sheet and 45 per hour.

While it works, the chat shows each step it runs (for example **Waffle
slice**, **Nest sheets**), and the preview changes after each one. Then ask
for changes in plain words, for example "use 8 ribs across instead", "make
it 600 mm tall", or "try a contour stack of 10 mm layers instead".

What the assistant can do:

| Ask for | What happens |
|---|---|
| a box, cylinder, cone, sphere or pyramid of a given size | creates that solid (mm, sitting on the ground) |
| your own model | click the **paperclip** next to the message box and choose an `.stl` or `.obj` file inside your home folder (in Rhino: select the object, **File > Export Selected**, choose STL or OBJ, export as a mesh in millimetres) |
| a size change ("make it 600 mm tall") | scales the model evenly |
| waffle / ribs | interlocking slotted ribs |
| contour stack / layers | stacked, glued layers with alignment etching |
| unfold / net / papercraft | folding nets with glue tabs (needs a faceted model, for example a cone with 8 sides) |
| nest on sheets of W × H mm | lays the parts on as few sheets as possible |
| cost / time | measures cut length, score length and pierces, and works out machine time and cost |
| export DXF or SVG | writes one file per sheet into `~/Documents/CardboardCAD/` (or a folder you name inside your home folder) |

- **New Design** (the pencil-and-square button at the top of the chat, or
  **Design > New Design**, ⇧⌘N) clears the chat and the design.
- The safari-compass button at the top right of the preview opens the same
  preview in your web browser: `http://127.0.0.1:5001/preview`.
- The old forms (Hello world and the cut-estimate calculator) are under
  **Design > Manual Tools** (⇧⌘T).

If the chat says *No Claude API key yet*, do step 2 again. If it says
*Claude rejected the API key*, the key was mistyped or deleted in the console:
make a new one and save it in Settings.

### Updating your build to get this

From Terminal (`/Applications/Utilities/Terminal.app`), with your checkout at
`~/Development/folding-grasshoppers`:

```bash
cd ~/Development/folding-grasshoppers
git fetch origin
git checkout main
git pull
make app sign-adhoc install
open /Applications/CardboardCAD.app
```

`make app` rebuilds the bundled Python with the new engine code and then the
app. To try this change before it is merged, replace `git checkout main` and
`git pull` with `git checkout claude/project-thread-urn9yz` and
`git pull origin claude/project-thread-urn9yz`.

## 4. Connect Grasshopper (Hops)

You need Rhino 8 for Mac. Hops ships with Rhino 8. If it is missing, type
`PackageManager` in the Rhino command line, search for **Hops**, click
**Install**, and restart Rhino.

1. Start CardboardCAD and wait for the **green** dot.
2. In CardboardCAD, open **Settings** (⌘,). Under **Grasshopper Hops**, click
   **Copy Hops URL** next to the component you want. For example, the hello
   world one is `http://127.0.0.1:5001/hello`.
3. In Rhino, type `Grasshopper` and press Return to open the Grasshopper window.
4. In Grasshopper's top tabs, click **Params**. In the **Util** group, drag the
   **Hops** component onto the canvas. (Or double-click empty canvas, type
   `Hops` and press Return.)
5. Right-click the Hops component, choose **Path…**, paste the URL (⌘V) and
   click **OK**.
6. The component reshapes itself with the inputs and outputs below and shows
   the Fold Theory icon.

| URL to paste | Inputs | Outputs |
|---|---|---|
| `http://127.0.0.1:5001/hello` | Name (text) | Msg (text) |
| `http://127.0.0.1:5001/cut_estimate` | Lc, Ls, N, S, Vc, Vs, Tp, k, Ps, R (numbers) | T, Cm, Cmach, C |
| `http://127.0.0.1:5001/echo` | N (number) | N (the same number) |
| `http://127.0.0.1:5001/waffle_slice` | M (mesh), Nx, Ny, T, C, K | P (parts), Cut, R3, L |
| `http://127.0.0.1:5001/contour_stack` | M (mesh), T, K, E, A | P (parts), Cut, Etch, L3, L |
| `http://127.0.0.1:5001/nest` | P, W, H, G, E, R | P (placed), S, Cut, N, U |
| `http://127.0.0.1:5001/unfold` | M (mesh), Tw, Ta, Fa | P (nets), Cut, Score, L, N |
| `http://127.0.0.1:5001/export` | P, F (`dxf`/`svg`), N, D, W | Paths, Text |

Use your own port number if you changed it in Settings. Use `127.0.0.1`,
not `localhost`: the engine listens on the IPv4 loopback address only.

After you paste a path, the status bar's Grasshopper dot should say "last call
just now". If it still says "no calls yet", the request never reached the
engine: check the address and port.

If an input makes no sense, for example a negative length or a zero speed,
the component turns red and says why. It never returns zeros silently.

### Waffle slicing to a DXF

1. Hops with `/waffle_slice`. Connect a closed mesh to **M** (for a Brep, put
   **Mesh Brep** in front). Set **Nx**, **Ny** (rib counts) and **T** (board
   thickness, mm). **Cut** previews the flat ribs; **R3** shows them in place.
2. Hops with `/export`. Connect the waffle's **P** output to its **P** input,
   and a **Boolean Toggle** to **W**. Set it to True to write
   `~/Documents/CardboardCAD/cardboardcad-sheet1.dxf` (layers CUT, LABEL).
   Type `svg` into **F** for SVG instead.

The other geometry components chain the same way, through their **P**
output:

- `/contour_stack` slices the mesh into stacked layers and adds an ETCH
  layer for lining them up.
- `/unfold` turns a mesh with flat faces into nets with fold lines, glue
  tabs and matching edge numbers.
- `/nest` goes between any of them and Export to lay the parts on real
  sheets: one file per sheet, with the sheet outline.

See [docs/GEOMETRY.md](docs/GEOMETRY.md) for every input and output.

## 5. Hello world demo

This checks the whole chain, Mac app > engine > Grasshopper, in three ways.
Start CardboardCAD and wait for the green dot first.

**A. In the app.** Open **Design > Manual Tools** (⇧⌘T), type a name in the
**Hello world** box and click **Say Hello**. You see:

> Hello, Ada, from CardboardCAD by Fold Theory

**B. In Grasshopper.**

1. Add a Hops component with the path `http://127.0.0.1:5001/hello`
   (section 4).
2. Params > Input > **Panel**: add a panel, double-click it, type a name, and
   wire it into the Hops **Name** input.
3. Add a second Panel and wire the Hops **Msg** output into it. It shows the
   greeting.

**C. In Terminal (no Grasshopper needed).** Open
`/Applications/Utilities/Terminal.app` and paste:

```bash
curl http://127.0.0.1:5001/health
curl -X POST -H 'Content-Type: application/json' -d '{"name":"Terminal"}' http://127.0.0.1:5001/api/hello
```

Expected output:

```text
{"status":"healthy"}
{"message":"Hello, Terminal, from CardboardCAD by Fold Theory"}
```

If you have the source code checked out and the app built (section 7), this
script does exactly what a Hops component does: it fetches the component
description, then asks it to solve. It runs with the app's own bundled
Python, so nothing else needs installing:

```bash
cd ~/src/folding-grasshoppers
build/python-runtime/bin/python3.12 -I scripts/demo_hello.py --name Grasshopper
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

## 6. Where CardboardCAD keeps its files

`~` means your home folder, `/Users/<you>`. In Finder, press ⇧⌘G and paste a
path to jump there.

| What | Path |
|---|---|
| The app | `/Applications/CardboardCAD.app` |
| Engine log (newest) | `~/Library/Logs/CardboardCAD/engine.log` |
| Older logs (kept: 2, 5 MB each) | `~/Library/Logs/CardboardCAD/engine.log.1`, `engine.log.2` |
| Engine data | `~/Library/Application Support/CardboardCAD/` |
| Engine cache | `~/Library/Caches/CardboardCAD/` |
| Saved port setting | `~/Library/Preferences/com.example.cardboardcad.plist` (read it with `defaults read com.example.cardboardcad enginePort`) |
| Claude API key | login Keychain, item `com.example.cardboardcad.claude` (Keychain Access.app) |
| Assistant model and effort | same preferences file (`defaults read com.example.cardboardcad assistantModel`) |
| Exported cut files | `~/Documents/CardboardCAD/` unless you name another folder |
| Python inside the app | `/Applications/CardboardCAD.app/Contents/Resources/python/` |

To uninstall, quit the app, drag `/Applications/CardboardCAD.app` to the
Trash, delete the four `~/Library/...` paths above, and delete the Keychain
item `com.example.cardboardcad.claude` in Keychain Access.

## 7. Build the app yourself

This produces an unsigned app for your own Mac. Allow about 10 minutes the
first time.

### Step 1: install the tools (once)

Python, uv and Node.js are **not** installed on your Mac. Every step that
needs them runs inside a Linux container, using Apple's `container` tool,
and a `Makefile` runs the right command in the right place. You install
three things.

Open `/Applications/Utilities/Terminal.app` and run these one at a time.

1. **Xcode 16 or newer**: install it from the Mac App Store, open it once, and
   accept the licence. Then:
   ```bash
   sudo xcode-select --switch /Applications/Xcode.app
   xcodebuild -version        # should print Xcode 16.x or newer
   ```
   This also gives you **git** and **make**. Check with `git --version` and
   `make --version`.
2. **Apple's container tool**: download the signed installer (`.pkg`) from
   https://github.com/apple/container/releases and double-click it. That page
   lists the macOS versions it supports. Then start it, once after installing
   and again after each restart of your Mac:
   ```bash
   container system start
   container --version
   ```
   The first `container system start` may offer to download a Linux kernel;
   answer **Y**.
3. Nothing else. The first `make` command that needs the container builds
   the dev image from `~/src/folding-grasshoppers/Containerfile`. That image
   holds uv 0.11.32, Python 3.12.13 and Node.js 22.23.5, all pinned, and the
   first build takes a few minutes.

### Step 2: get the source code

The rest of this README assumes the code lives in
`~/src/folding-grasshoppers`.

```bash
mkdir -p ~/src
cd ~/src
git clone https://github.com/sholtomaud/folding-grasshoppers.git
cd ~/src/folding-grasshoppers
```

### Step 3: build the bundled Python

```bash
cd ~/src/folding-grasshoppers
make runtime
```

This happens in two parts:

- **In the container:** it downloads a pinned Python 3.12 for macOS, checked
  against a fixed SHA-256, and installs the engine and its locked
  dependencies into it. The container never runs that macOS Python. It only
  unpacks it and copies files into it.
- **On your Mac:** it tests the result by starting the engine with that
  bundled Python. This is the same thing the app does when it opens.

It writes:

- `~/src/folding-grasshoppers/build/python-runtime/`: the Python that goes inside the app
- `~/src/folding-grasshoppers/build/cache/`: downloads, reused next time

It ends with `==> Runtime smoke tests passed`.

### Step 4: build the app

```bash
cd ~/src/folding-grasshoppers
make app
```

It ends with `==> Built build/app/CardboardCAD.app (...)`. The app is now at:

`~/src/folding-grasshoppers/build/app/CardboardCAD.app`

The Xcode log is at `~/src/folding-grasshoppers/build/xcodebuild-archive.log`.

### Step 5: sign it for this Mac and install it

macOS refuses to open unsigned apps by double-click. Sign it for your own
Mac only (an "ad-hoc" signature), copy it to `/Applications` and open it:

```bash
cd ~/src/folding-grasshoppers
make sign-adhoc install open
```

If macOS still says it cannot verify the app, open **System Settings >
Privacy & Security**, scroll to the message about CardboardCAD and click
**Open Anyway**. An ad-hoc signed app only works on the Mac that built it. To
give it to someone else, make a signed release (section 8).

Run `make help` to list every command.

## 8. Make a signed release (DMG)

This creates the `CardboardCAD.dmg` that goes on the releases page. You need:

- the build tools from [section 7, step 1](#step-1-install-the-tools-once)
- a paid Apple Developer account, and its **Team ID** (10 characters, shown at
  https://developer.apple.com/account under Membership details)
- a **Developer ID Application** certificate installed in your login keychain.
  Create it in Xcode > Settings > Accounts > *your team* > **Manage
  Certificates…** > **+** > **Developer ID Application**.
- an **app-specific password** for your Apple ID, made at
  https://account.apple.com > Sign-In and Security > App-Specific Passwords

**Once**, save the notarization credentials in your keychain under the name
`cardboardcad-notary`:

```bash
xcrun notarytool store-credentials cardboardcad-notary \
  --apple-id you@example.com \
  --team-id ABCDE12345 \
  --password abcd-efgh-ijkl-mnop
```

**Each release:**

```bash
cd ~/src/folding-grasshoppers
TEAM_ID=ABCDE12345 NOTARY_PROFILE=cardboardcad-notary make release
```

This rebuilds Python (in the container) and the app, signs everything, sends the app and then the
DMG to Apple for notarization (usually 2 to 15 minutes each), staples the
approval, and runs the final checks. The result is:

`~/src/folding-grasshoppers/build/CardboardCAD.dmg`

To publish it, go to
https://github.com/sholtomaud/folding-grasshoppers/releases/new, choose a tag
such as `v0.1.0`, drag `build/CardboardCAD.dmg` into the **Attach binaries**
box, and click **Publish release**.

The bundle ID is `com.example.cardboardcad` until a real one is chosen. Change
it by adding `BUNDLE_ID=com.foldtheory.cardboardcad` (for example) before
`scripts/release.sh`. All release settings are in
`~/src/folding-grasshoppers/scripts/config.sh`, and the scripts are explained
in `~/src/folding-grasshoppers/scripts/README.md`.

## 9. Troubleshooting

| Symptom | What to do |
|---|---|
| Red dot: "Port 5001 is in use" | Another program is using port 5001. In Terminal, `lsof -nP -iTCP:5001 -sTCP:LISTEN` shows which. Quit it, or choose another port in **Settings**, click **Apply**, and copy the Hops URLs again. |
| Red dot: "Engine crashed 4 times within 60 s" | Click **View Logs** and send `~/Library/Logs/CardboardCAD/engine.log` to the developers. **Engine > Restart Engine** tries again. |
| Red dot: "Engine did not become ready within 20 s" | Restart the engine once. If it happens again, send the log. |
| Hops component stays grey or empty | Check the dot is green and the port in the URL matches **Settings**. Paste the URL into Safari: it should show text starting with `{"Uri":`. |
| "CardboardCAD can't be opened" | You opened an unsigned build. See [section 7, step 5](#step-5-sign-it-for-this-mac-and-install-it). |
| Chat says "No Claude API key yet" | Do [section 3, step 2](#step-2-put-the-key-into-cardboardcad-once). |
| Chat says "Claude rejected the API key" | The key is wrong or was deleted. Make a new one at console.anthropic.com and save it in **Settings**. |
| Chat says "Claude is rate limiting this key" or mentions credit | Wait a minute, or check billing at console.anthropic.com > **Plans & Billing**. |
| Preview stays blank | Check the engine dot is green, then open `http://127.0.0.1:5001/preview` in Safari. If Safari shows it, use **Engine > Restart Engine**. |
| "Model files must be inside your home folder" | Move the `.stl` / `.obj` into a folder under `/Users/<you>` (for example `~/Documents`) and attach it again. |
| A `python3.12` process is still running after quitting | It stops by itself within about 5 seconds. If not, `pkill -f cardboard_engine` stops it. Please report it. |

## 10. `cut_estimate` reference values

Use these to check the app form, Grasshopper and the engine agree.

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

## 11. For developers

Read [AGENTS.md](./AGENTS.md) first. Work is test-first, and the tests are
sealed by [agentic-uplift](https://github.com/sholtomaud/agentic-uplift).

All commands below run from `~/src/folding-grasshoppers`.

Python, uv and Node run only in the dev container. `make` and
`scripts/verify.sh` start it for you through `scripts/tools.sh`. On a machine
that already has uv on its PATH, such as GitHub Actions, the same commands
run directly instead.

```bash
git config core.hooksPath .githooks   # once: run the test gate on commit and push

make test         # engine tests, pytest (59)         [container]
make swift-test   # Swift tests (52)                  [Mac]
make verify       # everything CI checks              [container + Mac]
make shell        # a bash shell in the dev container, repo at /work
make lock         # after editing engine/pyproject.toml dependencies
```

Run the engine on its own, without the app, while working on Python. Run it
on the Mac with the bundled Python, because a server inside the container
listens on the container's own 127.0.0.1, which the Mac can't reach:

```bash
cd ~/src/folding-grasshoppers
make runtime      # after each engine change
build/python-runtime/bin/python3.12 -I -B -u -m cardboard_engine.server --port 5001
```

Run the app from Xcode: `open ~/src/folding-grasshoppers/app/CardboardCAD.xcodeproj`,
pick the **CardboardCAD** scheme and press ⌘R. Debug runs use
`~/src/folding-grasshoppers/build/python-runtime/`, so run `make runtime`
first.

| Path (in `~/src/folding-grasshoppers/`) | What |
|---|---|
| `engine/` | Python engine: Flask + ghhops_server, components, pytest tests |
| `engine/cardboard_engine/components/` | the calculations (pure functions) |
| `engine/cardboard_engine/assets/` | Fold Theory icons served to Hops |
| `app/CardboardCADKit/` | Swift package: engine process manager, HTTP client, logs, XCTest tests |
| `app/CardboardCAD/` | SwiftUI app: windows, settings, Info.plist |
| `app/CardboardCAD.xcodeproj` | Xcode project |
| `scripts/` | build, sign, notarize, DMG and check scripts ([scripts/README.md](./scripts/README.md)) |
| `build/` | everything the scripts produce (not in git) |
| `Makefile` | every command above; `make help` lists them |
| `Containerfile` | the dev image: pinned uv, Python 3.12.13, Node.js |
| `SPEC.md` | the milestone specification |
| `.github/workflows/verify.yml` | CI: tests, macOS build, end-to-end app test, proof gate |
| `.github/workflows/uplift.yml` | publishes the agentic-uplift readiness cockpit |
