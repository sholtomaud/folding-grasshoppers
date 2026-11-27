# Fold Theory

Fold Theory is the Mac app of the [Fold Theory](https://foldtheory.com)
studio. It used to be called CardboardCAD (see
[Coming from CardboardCAD](#coming-from-cardboardcad)).
Inside it is a small calculation engine written in Python. Grasshopper (the
visual programming tool in Rhino 8) talks to that engine through its **Hops**
component, so the app and your Grasshopper definitions use the same
calculations.

- Nothing else needs installing: the app carries its own copy of Python.
- The engine only listens on your own Mac, at `http://127.0.0.1:5001`.
  Nothing is reachable from the network.
- In Grasshopper, the components appear under **Fold Theory > Cardboard**
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
6. [Where Fold Theory keeps its files](#6-where-fold-theory-keeps-its-files)
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
`~/src/folding-grasshoppers/build/app/Fold Theory.app`, which you copy to
`/Applications`.

### Later: download a release (not available yet)

Skip this until the releases page lists a release.

1. Go to the releases page:
   **https://github.com/sholtomaud/folding-grasshoppers/releases**
2. Under the newest release, open **Assets** and click `Fold Theory.dmg`.
   Your browser saves it to your Downloads folder:
   `/Users/<you>/Downloads/Fold Theory.dmg`.
3. Double-click `Fold Theory.dmg` in Finder. A window opens showing
   **Fold Theory** and an **Applications** folder.
4. Drag **Fold Theory** onto **Applications**. The app is now at
   `/Applications/Fold Theory.app`.
5. Eject the disk image: in the Finder sidebar, click the eject button next to
   **Fold Theory 0.1.0**. You can then delete
   `~/Downloads/Fold Theory.dmg`.

## 2. First launch

1. Open Finder > **Applications** and double-click **Fold Theory**.
2. A release download shows the usual macOS prompt: *"Fold Theory" is an app
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
solves, so this is the way to check that Grasshopper can reach Fold Theory.

| Grasshopper dot (bottom right) | Meaning |
|---|---|
| grey, "no calls yet" | no Hops component has reached the engine since it started |
| green, "last call 12 s ago" | Hops called within the last 2 minutes |
| grey, "last call 10 min ago" | Hops reached it before, but not recently |
| red, "failed (HTTP 500)" | the last call reached the engine but the component reported an error |

Quitting Fold Theory (**Fold Theory > Quit**, or ⌘Q) also stops the engine.

### Settings

Open them with **Fold Theory > Settings…** (⌘,).

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
2. Click **Create Key**, name it `Fold Theory`, and click **Add**.
3. Click **Copy**. The key starts with `sk-ant-`. Keep this browser tab open
   until step 2 is done: the console only shows the key once.

### Step 2: put the key into Fold Theory (once)

1. Open Fold Theory (Finder > **Applications** > **Fold Theory**).
2. Open **Fold Theory > Settings…** (⌘,).
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

macOS may ask *"Fold Theory wants to use your confidential information
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
| export DXF or SVG | writes one file per sheet into `~/Documents/Fold Theory/` (or a folder you name inside your home folder) |

- **New Design** (the pencil-and-square button at the top of the chat, or
  **Design > New Design**, ⇧⌘N) clears the chat and the design.
- The safari-compass button at the top right of the preview opens the same
  preview in your web browser: `http://127.0.0.1:5001/preview`.
- The old forms (Hello world and the cut-estimate calculator) are under
  **Design > Manual Tools** (⇧⌘T).

### Workflows: nesting and costing every time

A **workflow** is the list of steps the assistant follows for every design,
plus the values it uses when you don't give them (board size, gap between
parts, prices). The built-in **Standard** workflow is:

1. Model: make or load the model at its real size.
2. Parts: waffle, contour stack or unfold, whichever suits the form.
3. Nest on sheets: 1200 × 800 mm board, 5 mm gap, 10 mm margin.
4. Estimate cost: the engine works out machine time, cost and the quote from
   your **settings**. The first time, the assistant shows a small form in the
   chat for your sheet price and hourly machine rate (see *Prices, settings
   and the Project tab* below).
5. Cut files and 6. Save project: only when you ask.

So "make a cylinder stool 450 mm tall as a waffle from 6 mm board" now ends
with the parts nested on 1200 × 800 sheets and a cost, without asking for it.

**Apply defaults** is the switch in the window's toolbar (top right), also in
**Design > Workflow > Apply Workflow Defaults** (⇧⌘D) and in Settings:

| Apply defaults | What the assistant does |
|---|---|
| On (the default) | Uses the workflow's saved values for anything you didn't say, carries on through the steps, and tells you which values it used ("Used workflow values: sheet 1200 × 800…") |
| Off | Before each step, asks you for the values the workflow covers ("What board size? The workflow says 1200 × 800."), and waits for your answer |

Prices and rates are not workflow values any more: they are settings, typed
by you in a form, never by the assistant.

**Make your own workflow from a conversation.** Talk the assistant through
how you work, then say "save this as a workflow", for example:

> For honeycomb jobs: always waffle from 10 mm board, nest on 2400 × 1200
> sheets with an 8 mm gap, cost it, and ask me who the client is first.
> Save that as a workflow called Honeycomb.

It saves the workflow and switches to it. Pick a workflow in the toolbar,
in **Design > Workflow**, or in Settings, or ask "use the Honeycomb
workflow". To change one saved value, say for example "remember an 8 mm
gap". Each workflow is a plain JSON file in
`~/Documents/Fold Theory/Workflows/` (**Design > Workflow > Show Workflows
Folder**), so you can copy it to another Mac or edit it by hand; then choose
**Reload Workflows**. Delete a file there to remove that workflow; delete
`Standard.json` to get the built-in Standard back. Saved projects record
which workflow made them (`"workflow"` in `project.json`).

### Prices, settings and the Project tab

The assistant never does the sums. Every number that matters (sheets,
machine time, board and machine cost, margin, GST, the quote total) is worked
out by the engine from **settings** and **formulas**, the same way every
time. The assistant chooses what to do; the engine does the arithmetic.

**Settings** are your prices and rates: sheet price, machine rate, labour
rate, margin, GST, machine speeds, client and job, company name and ABN.
Each has a label, a unit (for example `AUD/sheet`) and an allowed range, and
remembers who set it and when. They come in layers, and the most specific
wins:

| Layer | Where it is kept | Used for |
|---|---|---|
| Fold Theory defaults (the rate card) | `~/Documents/Fold Theory/Settings/settings.json` | every project |
| Workflow | the `"settings"` in `~/Documents/Fold Theory/Workflows/<name>.json` | jobs that use that workflow |
| Project | `settings.json` in the project folder, e.g. `~/Documents/Fold Theory/Projects/Stool/settings.json` | this project only |
| Concept | (comes with concepts, issue #46) | one concept |

**Forms in the chat.** When a price is missing, the assistant asks with a
form instead of guessing: "What does a sheet of 10 mm honeycomb cost?", with
a field in AUD/sheet and a choice of **This project** or **All projects**.
Type the value and press **Save**. A typo (`abc`) or a negative price shows a
red note under the field and nothing is saved. The assistant is only told
*which* settings you set, never the numbers, and carries on.

**The Project tab** sits next to **3D & Sheets** above the preview. It
shows the job, the workflow steps done so far, the quote lines and total,
and every setting with where its value came from, for example
`38.50 AUD/sheet · project · SM, 27 Nov`. Change a setting there and press
Return: the engine works the quote out again at once. Pick **This project**
or **All projects** next to it to choose where the new value is saved.

**Unverified numbers.** If a reply from the assistant contains an amount or
quantity that no tool gave in that turn (for example "about 500"), the app
marks it **Unverified** in orange under the reply. Trust the Project tab.

**Formulas.** The quote is a list of formulas, each with a worked example
the engine checks: `material = round(sheets * sheet_price, 2)`,
`machine = round(cut_minutes * machine_rate, 2)`, then margin, GST and the
total. Ask the assistant for a new line, for example "add install crew
hours: one hour per 40 parts, rounded up", and it adds
`install_hours = ceil(parts / 40)` with an example (95 parts gives 3), which
the engine checks before saving. Units are checked: adding minutes to an
area is refused. Saved formulas go to
`~/Documents/Fold Theory/Settings/formulas.json` (all projects) or
`formulas.json` in the project folder. Every quote records the formula
versions it used, in `estimate/quote.json`, so an old quote can be worked
out again exactly.

**Tabs are JSON views.** Each tab other than 3D & Sheets and Diagnostics is
a JSON file drawn by the app's own building blocks (sections, fields,
tables, totals, settings, checklists, buttons). Fold Theory's Project tab
ships with the app; ask the assistant for a new tab ("add a Costs tab with
just the quote lines and the total") and it saves one to
`~/Documents/Fold Theory/Views/<id>.json`, or to `views/<id>.json` in the
project folder for one project. The engine checks each view first and
refuses unknown blocks, unknown data and buttons that are not registered,
saying why. **Undo Last Change** at the top of a tab puts the file back
exactly as it was. A new kind of building block still needs a new version
of the app.

**Diagnostics.** The **Diagnostics** tab lists what Grasshopper (LiveDesign
and its listener's On switch, SendToFoldTheory), the engine's tools and the
checks on forms, views and formulas reported, newest first. Filter by
source and level. Ask the assistant "why is Rhino not updating?" or "why was
my view rejected?" and it reads the same log. API keys and tokens are
removed before anything is written. The log is
`~/Library/Application Support/Fold Theory/diagnostics/diagnostics.jsonl`
(1 MB, then it moves to `diagnostics.jsonl.1`).

### Save and open projects

A **project** is a folder that holds everything about one design. Save one
for each job, and one per concept when you want to show a client several
options (for example `Acme Expo - Concept A`, `Acme Expo - Concept B`).

- **File > Save Project** (⌘S) the first time asks for a name, then saves
  into `~/Documents/Fold Theory/Projects/<name>/`. You can pick another
  folder in the dialog, as long as it is inside your home folder and empty.
  After that, ⌘S saves into the same folder again.
- **File > Save Project As…** (⇧⌘S) saves a copy under a new name: the
  quickest way to start Concept B from Concept A.
- **File > Open Project…** (⌘O): choose the project folder (or the
  `project.json` inside it). The design, its parts, the sheets and the chat
  come back exactly as saved, and you can carry on talking to the assistant.
- **File > Show Project in Finder** opens the folder.
- You can also ask the assistant: "save this as Acme Expo - Concept A".
- The window title shows the project's name.

What is in a project folder (example for a project called `Stool`):

| Path inside `~/Documents/Fold Theory/Projects/Stool/` | What |
|---|---|
| `project.json` | name, date, the steps that rebuild the design, and a summary (size, parts, sheets, cost) |
| `chat.json` | the conversation with the Design Assistant |
| `model/model.obj` | the 3D model in mm (opens in Rhino: **File > Import**) |
| `parts/parts.json` | the flat parts and where they sit on the sheets |
| `cut-files/Stool-sheet1.dxf`, `Stool-sheet1.svg`, … | cut files, one per sheet, layers CUT, SCORE, ETCH, LABEL |
| `estimate/estimate.csv` | the cost estimate; opens in Numbers, Excel or Google Sheets |
| `estimate/estimate.json` | the same, for other programs |
| `estimate/quote.json` | the quote: every line, the total, and the settings and formula versions it was made from |
| `settings.json` | this project's settings (only once you set one for this project) |
| `formulas.json` | formulas added for this project only, if any |
| `views/<id>.json` | tabs saved for this project only, if any |
| `preview/preview.png` | a picture of the 3D preview, as it was when you saved |
| `preview/model.svg` | a drawing of the model (opens in any web browser) |

Saving again rewrites these files and removes cut files for sheets that no
longer exist. Anything else you put in the folder (sketches, logos, notes)
is left alone. The folder is ordinary files, so it can live in a Google Drive
or OneDrive folder inside your home folder, or be zipped and emailed.

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
open /Applications/Fold Theory.app
```

`make app` rebuilds the bundled Python with the new engine code and then the
app. To try a change before it is merged, replace `git checkout main` and
`git pull` with `git checkout <branch>` and `git pull origin <branch>`, for
example `claude/project-thread-r4a2ee` for project saving and Rhino live
sync, `claude/project-thread-u3agc6` for workflows, or
`claude/project-thread-87xy59` for settings, forms, the Project tab and
Diagnostics.

## 4. Connect Grasshopper (Hops)

You need Rhino 8 for Mac. Hops ships with Rhino 8. If it is missing, type
`PackageManager` in the Rhino command line, search for **Hops**, click
**Install**, and restart Rhino.

1. Start Fold Theory and wait for the **green** dot.
2. In Fold Theory, open **Settings** (⌘,). Under **Grasshopper Hops**, click
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
| `http://127.0.0.1:5001/live_design` | V (version from the live listener) | V, M, R3, Cut, Sc, Sh, L, P, S: the app's current design |
| `http://127.0.0.1:5001/send_to_cardboardcad` | M (mesh), N (name), S (send) | V, S: sends a Rhino mesh to the app |

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
   `~/Documents/Fold Theory/cardboardcad-sheet1.dxf` (layers CUT, LABEL).
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

### See the app's design live in Rhino

Grasshopper can show the design that is open in Fold Theory and update
whenever it changes (including while the Design Assistant works), and can
send a Rhino mesh to the app. It takes a small listener script (copy it with
**Design > Copy Rhino Live Listener Script**) plus the `/live_design` Hops
component. Step by step: [docs/RHINO_LIVE_SYNC.md](docs/RHINO_LIVE_SYNC.md)
(also **Design > Rhino Live Sync Help** in the app).

### Edit the design in plain Rhino (two-way, no Grasshopper)

**Design > Copy Rhino Two-Way Script**, paste it into Rhino 8's Script Editor
(`ScriptEditor` command, new Python 3 script) and press Run. The design appears
on `Fold Theory` layers in Rhino. Edit the model there and the app re-slices,
nests and costs it again; reshape a flat part (move a slot, add a hole) and it
becomes a hand edit the app keeps. The Design Assistant is told what you
changed. Step by step:
[docs/RHINO_TWO_WAY.md](https://github.com/sholtomaud/folding-grasshoppers/blob/main/docs/RHINO_TWO_WAY.md)
(also **Design > Rhino Two-Way Help** in the app). This is a prototype that
has not been tried in Rhino yet.

## 5. Hello world demo

This checks the whole chain, Mac app > engine > Grasshopper, in three ways.
Start Fold Theory and wait for the green dot first.

**A. In the app.** Open **Design > Manual Tools** (⇧⌘T), type a name in the
**Hello world** box and click **Say Hello**. You see:

> Hello, Ada, from Fold Theory

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
{"message":"Hello, Terminal, from Fold Theory"}
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
  Fold Theory > Cardboard > Hello (icon: yes)
  in : Name (Name, Text)
  out: Message (Msg, Text)

Hops solve  -> Hello, Grasshopper, from Fold Theory
App route   -> Hello, Grasshopper, from Fold Theory
```

## 6. Where Fold Theory keeps its files

`~` means your home folder, `/Users/<you>`. In Finder, press ⇧⌘G and paste a
path to jump there.

| What | Path |
|---|---|
| The app | `/Applications/Fold Theory.app` |
| Engine log (newest) | `~/Library/Logs/Fold Theory/engine.log` |
| Older logs (kept: 2, 5 MB each) | `~/Library/Logs/Fold Theory/engine.log.1`, `engine.log.2` |
| Engine data | `~/Library/Application Support/Fold Theory/` |
| Engine cache | `~/Library/Caches/Fold Theory/` |
| Saved port setting | `~/Library/Preferences/com.example.cardboardcad.plist` (read it with `defaults read com.example.cardboardcad enginePort`) |
| Claude API key | login Keychain, item `com.example.cardboardcad.claude` (Keychain Access.app) |
| Assistant model and effort | same preferences file (`defaults read com.example.cardboardcad assistantModel`) |
| Exported cut files | `~/Documents/Fold Theory/` unless you name another folder |
| Saved projects | `~/Documents/Fold Theory/Projects/<project name>/` (see section 3, *Save and open projects*) |
| Workflows | `~/Documents/Fold Theory/Workflows/<workflow name>.json`, and `.settings.json` there for the active workflow and Apply defaults (see section 3, *Workflows*) |
| Settings for all projects (the rate card) | `~/Documents/Fold Theory/Settings/settings.json` |
| Formulas for all projects | `~/Documents/Fold Theory/Settings/formulas.json` |
| Tabs (JSON views) for all projects | `~/Documents/Fold Theory/Views/<id>.json`, earlier versions in `Views/.history/<id>/` |
| Diagnostics log | `~/Library/Application Support/Fold Theory/diagnostics/diagnostics.jsonl` |
| Python inside the app | `/Applications/Fold Theory.app/Contents/Resources/python/` |

### Coming from CardboardCAD

The app was called CardboardCAD. The first time Fold Theory starts it moves
your old folders to their new names, so nothing is lost:

| Old path | New path |
|---|---|
| `~/Documents/CardboardCAD/` | `~/Documents/Fold Theory/` (projects, workflows, exports). The old path stays as a link to the new folder, so file paths saved in Rhino files and Grasshopper inputs still work |
| `~/Library/Logs/CardboardCAD/` | `~/Library/Logs/Fold Theory/` |
| `~/Library/Application Support/CardboardCAD/` | `~/Library/Application Support/Fold Theory/` |
| `~/Library/Caches/CardboardCAD/` | `~/Library/Caches/Fold Theory/` |

If both an old and a new folder already exist, both are kept and the old one
is left alone; move files across in Finder if you need them. Your port,
model and effort settings and the Claude API key in the Keychain carry over
unchanged (the bundle identifier `com.example.cardboardcad` did not change).
`make install` also deletes the old `/Applications/CardboardCAD.app`.

In Grasshopper, the components now appear under **Fold Theory > Cardboard**.
Existing definitions keep working: the Hops URLs did not change (for example
`http://127.0.0.1:5001/send_to_cardboardcad`), only the names shown, such as
**SendToFoldTheory**.

To uninstall, quit the app, drag `/Applications/Fold Theory.app` to the
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

It ends with `==> Built build/app/Fold Theory.app (...)`. The app is now at:

`~/src/folding-grasshoppers/build/app/Fold Theory.app`

The Xcode log is at `~/src/folding-grasshoppers/build/xcodebuild-archive.log`.

### Step 5: sign it for this Mac and install it

macOS refuses to open unsigned apps by double-click. Sign it for your own
Mac only (an "ad-hoc" signature), copy it to `/Applications` and open it:

```bash
cd ~/src/folding-grasshoppers
make sign-adhoc install open
```

If macOS still says it cannot verify the app, open **System Settings >
Privacy & Security**, scroll to the message about Fold Theory and click
**Open Anyway**. An ad-hoc signed app only works on the Mac that built it. To
give it to someone else, make a signed release (section 8).

Run `make help` to list every command.

## 8. Make a signed release (DMG)

This creates the `Fold Theory.dmg` that goes on the releases page. You need:

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

`~/src/folding-grasshoppers/build/Fold Theory.dmg`

To publish it, go to
https://github.com/sholtomaud/folding-grasshoppers/releases/new, choose a tag
such as `v0.1.0`, drag `build/Fold Theory.dmg` into the **Attach binaries**
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
| Red dot: "Engine crashed 4 times within 60 s" | Click **View Logs** and send `~/Library/Logs/Fold Theory/engine.log` to the developers. **Engine > Restart Engine** tries again. |
| Red dot: "Engine did not become ready within 20 s" | Restart the engine once. If it happens again, send the log. |
| Hops component stays grey or empty | Check the dot is green and the port in the URL matches **Settings**. Paste the URL into Safari: it should show text starting with `{"Uri":`. |
| "Fold Theory can't be opened" | You opened an unsigned build. See [section 7, step 5](#step-5-sign-it-for-this-mac-and-install-it). |
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
