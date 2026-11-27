# Roadmap: from design tool to Fold Theory's quoting studio

Status: proposal. Owner of the finished app: Tobias (Fold Theory). Written
2026-11-26, extended 2026-11-27 with the foundation (deterministic numbers,
project settings, configurable views, workflows, proposals) and two-way Rhino.
Every feature has a GitHub issue: see [section 14](#14-issues).

Today CardboardCAD can take a request in plain words ("a cylinder stool 450 mm
tall as a waffle from 6 mm board"), build it with the engine, show it in 3D and
as cut sheets, nest it and estimate cut time and cost. This roadmap covers what
it takes to make it the place where Tobias pitches a tradeshow stand to a
client: a brief with hard limits, three or four concepts side by side, each
with its own quote and renders, a library of proven modules, inspiration
images from the client, Google Drive or OneDrive storage with live budget
sheets, and an assistant that designs the way Tobias does.

Saving projects, Grasshopper live sync (`docs/RHINO_LIVE_SYNC.md`) and Design
Assistant workflows with saved values are now built. Everything below builds on
those. The app rename to Fold Theory and the new icon are handled separately.

## Contents

1. [Where things stand](#1-where-things-stand)
2. [The project folder everything builds on](#2-the-project-folder-everything-builds-on)
3. [Foundation: deterministic numbers and a configurable app](#3-foundation-deterministic-numbers-and-a-configurable-app)
4. [Two-way Rhino](#4-two-way-rhino)
5. [Phase 1: brief, constraints, concepts and quotes](#5-phase-1-brief-constraints-concepts-and-quotes)
6. [Phase 2: module library](#6-phase-2-module-library)
7. [Phase 3: inspiration board](#7-phase-3-inspiration-board)
8. [Phase 4: Google Drive, OneDrive and live budget sheets](#8-phase-4-google-drive-onedrive-and-live-budget-sheets)
9. [Phase 5: learning Tobias's style](#9-phase-5-learning-tobiass-style)
10. [Recommended order](#10-recommended-order)
11. [Running costs](#11-running-costs)
12. [What we need from SM and Tobias](#12-what-we-need-from-sm-and-tobias)
13. [Open questions](#13-open-questions)
14. [Issues](#14-issues)

## 1. Where things stand

| Have | Missing for pitching |
|---|---|
| Design Assistant chat, Claude picks engine steps | A brief: size limits, loads, seating, budget |
| One model per session (create, import STL/OBJ, scale) | Several objects in one stand, and several concepts |
| Waffle, contour stack, unfold, nest, DXF/SVG export | Versions of a saved project (saving itself is built) |
| Cut time and machine/material cost | A full quote: labour, print, freight, margin |
| Hops endpoints for Grasshopper | Edits made in Rhino flowing back (today the link is Grasshopper, mostly one way) |
| Rates typed in chat, not held in project settings | Project settings, input forms, project view, quote PDF |
| | Library, inspiration images, cloud storage, house style |

One engine limit matters for several phases: the design session holds a single
model. A stand is a counter plus stools plus a back wall, so Phase 1 adds a
**scene** of named objects, each with its own position and parts. The library
(Phase 2) depends on it.

## 2. The project folder everything builds on

Every feature here reads and writes plain files in one folder per job, so the
folder works without CardboardCAD, syncs with any cloud drive, and Tobias can
open the cut files and renders directly. The project saving work defines the
base: `~/Documents/CardboardCAD/Projects/<Project name>/` with `project.json`
(schema `cardboardcad.project/1`: the design recipe the engine replays on
reopen), `chat.json`, `model/`, `parts/`, `cut-files/`, `estimate/` and
`preview/`. Until concepts exist, concepts A, B and C are simply separate
projects ("Acme - Expo stand - A").

The phases below add folders around that base. Each concept version is one
saved design in exactly the base layout, so reopening and Rhino sync work on it
unchanged:

```
Acme - Expo stand/
├── project.json            # client, job, dates, current concept (base schema + concepts list)
├── brief.json              # constraints (Phase 1), human-readable copy in brief.md
├── inspiration/            # client images, sketches, logos (Phase 3)
│   └── notes.json          # what Claude took from each image
├── concepts/
│   ├── A Cylinder seating/
│   │   ├── v1/             # a saved design in the base layout:
│   │   │   ├── project.json, chat.json
│   │   │   ├── model/ parts/ cut-files/ preview/
│   │   │   ├── estimate/   # quote.json + quote.csv join estimate.json here
│   │   │   └── renders/    # PNGs from the 3D preview for the pitch
│   │   └── v2/ …
│   └── B Folded counter/ …
├── pitch/
│   └── Acme pitch 2026-12-03.pdf
└── budget.link             # URL of the live Google Sheet / Excel file (Phase 4)
```

A **version** is a frozen copy: once `v1` is shown to a client it is never
edited, changes go into `v2`. That gives "what did we quote on the 3rd?" for
free, and comparing versions is a file diff.

## 3. Foundation: deterministic numbers and a configurable app

This section answers SM's second round of asks: Claude must not do the
calculations, values belong in project settings entered through forms, there
should be more ways to look at a project, workflows should be editable with
manual steps, and the app's screens should be configurable without rebuilding
the app. It comes before most of Phase 1, because quotes, the brief panel and
the pitch are all built on it.

### 3.1 Claude never calculates

Claude chooses what to do and with which inputs. Every number that matters
(sheets, cut time, board area, line totals, margin, tax, the quote total) is
worked out by the engine, deterministically and under test.

- Today the engine already works out cut time and cost (`estimate_cost`), but
  prices reach it as numbers Claude typed into the tool call or saved in a
  workflow, and Claude restates totals in its own words. Both go.
- Prices and rates come only from project settings (3.2). A missing value is
  an engine error that tells Claude to ask with a form, never to guess.
- Totals are shown by the app from engine output (project view, quote PDF),
  not by Claude's reply. Claude can refer to them, but the number on screen
  comes from the engine.
- A guard checks Claude's replies: a money amount or quantity that does not
  appear in this turn's engine results is marked **unverified** in the chat.

### 3.2 Project settings and input forms

- **Settings** are typed values with a label, unit, type, allowed range and a
  record of who set them and when: board price per sheet, sheet size, machine
  rate, labour rates, margin, GST, company details for invoices.
- They come in layers, most specific wins: **Fold Theory defaults** (the rate
  card) → **workflow** → **project** → **concept**. The saved values in the
  current Design Assistant workflows become the workflow layer.
- Stored as JSON: defaults in `~/Documents/CardboardCAD/Settings/`, project
  values in the project folder (`settings.json`), so they travel with the
  project, sync with Drive and can be read by the engine.
- A **`request_inputs`** tool lets Claude ask for values with a small form in
  the chat: "What does a sheet of 10 mm honeycomb cost?" with a currency field,
  the unit, and a choice of "this project" or "all projects". Submitting the
  form writes the setting; Claude sees only that it was set.

### 3.3 Formulas and fields that Claude can add

- The quote is a set of **fields** (inputs: settings and engine measurements)
  and **formulas** (outputs), kept in JSON (`formulas.json`) and evaluated by
  the engine.
- Formulas use a small, safe expression language: arithmetic, `min`, `max`,
  `round`, `ceil`, `if`, sums over parts or sheets, with units checked
  (mm² cannot be added to minutes). No Python `eval`, no loops, no file or
  network access.
- Claude can add a field or formula ("glue: 0.15 per joint", "install crew
  hours = parts / 40, rounded up") through a proposal (3.6). Each new formula
  must come with a worked example (inputs and the expected result), which the
  engine checks before it can be accepted, and which then stays as a test.
- Every quote records the formula versions it was made with, so an old quote
  can always be reproduced.

### 3.4 Configurable views (the Hydstra idea)

Yes, this makes sense, and it is the right split for this app. Screens become
JSON documents drawn by a fixed set of native building blocks, so changing a
screen means changing a file, not rebuilding and re-signing the app.

- Building blocks (written once in SwiftUI): tabs, sections, forms, fields,
  tables, totals, charts, images, the 3D preview, cut sheets, PDF preview,
  checklists, and buttons that call a **registered action** (for example
  "export cut files", "freeze version").
- A view (`views/*.json`) is checked against the **view schema** before it is
  used: unknown building blocks, fields that do not exist and actions that are
  not registered are rejected with a message Claude can act on. A valid view
  is previewed, accepted, saved with its history, and can be undone in one
  click.
- Fold Theory's views live with the app defaults; a project can add its own.
- What this buys: new tabs, forms, tables and layouts in seconds, by Claude or
  by hand. What it does not: a new kind of building block (say a Gantt chart)
  is still a code change and a new release.

### 3.5 The locked core and the configurable layer

| Locked core (code, changed by PR) | Configurable layer (data, changed in the app) |
|---|---|
| Engine and its tools, geometry, the formula evaluator | Views |
| The schemas for views, formulas, settings, workflows (versioned, e.g. `cardboardcad.view/1`) | Formulas and fields |
| Security: files only inside the home folder, engine on `127.0.0.1`, keys in the Keychain, which hosts the app talks to | Workflows |
| The list of registered actions | Settings and rate card |
| | Library entries |

The core changes the way it does now: tests first, PR, proof seal. A schema
change is a core change. The configurable layer is validated against the core
schemas, so it cannot run code, reach the network, read files outside the
project or see keys. Every change to it is kept in a history (with who
accepted it), which is how we track what Claude and people changed.

### 3.6 Proposals

One **`propose`** tool covers every change to the configurable layer, and
design changes too: a new view or tab, a formula, a field, a workflow step, a
setting, a library entry, "use 7 ribs instead of 5".

- Shows in the chat as a card with what changes (a diff or preview) and why,
  with **Accept**, **Edit** and **Reject**.
- Claude can propose on its own when it notices something ("you have added
  freight by hand on three projects; shall I add a freight formula?").
- Nothing in the configurable layer changes without acceptance. Accepted and
  rejected proposals are kept in the project's `proposals/` folder.

### 3.7 Project workflows, manual steps and status

The current workflows guide the Design Assistant through design steps. Project
workflows extend this to the whole job:

```
Lead → Brief → Concepts → Pitched → Approved → Production files → Cut → Assembled → Delivered → Invoiced → Paid
```

- Each step is a **Claude step** (tools it runs), a **manual step** (a
  checklist item someone ticks: "client approved by email", "board ordered"),
  or a **gate** (checks that must pass: brief checks green, quote accepted).
- The current step is the project's **status**, shown in the project view and
  the project list.
- Claude can create or change workflows through proposals, when asked or when
  the real work keeps differing from the workflow.

### 3.8 More ways to see a project

Next to **3D model** and **Cut sheets**:

- **Project**: client, brief, status, settings (with where each value came
  from), concepts at a glance. Built as a JSON view.
- **Quote / invoice (PDF)**: made only from engine numbers. Quote number,
  validity date, terms, GST, company details and logo; an invoice made from an
  accepted quote version, with deposit and progress invoices. Exports CSV for
  the accounting package; connecting to one (Xero, MYOB) is a later choice.
- **Workflow**: the steps, who did what, what is next.

### 3.9 Storage: files, a native database, or both?

Recommendation: **the project folder's JSON files are the single source of
truth**, and a native SwiftData database is only an **index** built from them.

- Two places that can both be edited would drift apart: exactly the
  disconnect to avoid. Files win because Drive, OneDrive, Rhino, Grasshopper,
  the Python engine and people can all read them; the engine cannot read a
  SwiftData store.
- The index makes the project list, search, filters by status and the library
  browser fast. It is rebuilt from the files whenever they change, and can be
  deleted at any time.
- Backup comes from where the folder lives: iCloud Drive, Google Drive or
  OneDrive (Phase 4, stage A), plus Time Machine.
- CloudKit is worth adding only if app state (not design files) must follow a
  person across Macs or onto an iPad. It would sync the index and settings,
  never replace the files. (A Developer ID app needs the iCloud entitlement
  and a provisioning profile for this; check when we get there.)

### 3.10 Diagnostics Claude can read

Grasshopper's LiveDesign listener and Hops components, the Rhino plugin, the
engine, and validation of forms, views, formulas and workflows all write
structured entries (source, level, time, design version, message) into one
rolling log on the Mac. A **Diagnostics** panel shows them. The Design
Assistant reads them with a `read_diagnostics` tool, so "why is Rhino not
updating?" or "why was my view rejected?" is answered from the real logs, and
Claude can propose the fix. Keys and tokens are redacted. Logs stay on the
Mac, and only the entries Claude reads go to the API.

## 4. Two-way Rhino

Designers work in Rhino, and small changes are faster with a mouse than with a
prompt. Today the link is Grasshopper: **LiveDesign** pulls the app's design
into Grasshopper, and **SendToCardboardCAD** pushes a mesh into the app by hand.
Edits made in Rhino do not flow back on their own, and nothing works without a
Grasshopper definition open.

The plan is a **Fold Theory plugin for Rhino 8** (Mac first):

- **The design as real Rhino objects.** The plugin keeps a layer tree
  (`Fold Theory / Concept A / Model`, `Parts`, `Sheets`) in step with the app.
  Each object carries user text with its id and design version, so the plugin
  knows what is what. Updates replace objects in place.
- **Edits flow back.** The plugin listens to Rhino's document events (object
  changed, added, deleted on those layers) and, a moment after the designer
  stops, sends the change to the engine:
  - Edit the **model** (control points, SubD, scale): the app's model is
    replaced and the recipe runs again (slice, nest, cost), and the new parts
    come back into Rhino.
  - Edit a **flat part** (move a slot, reshape an outline): it becomes a
    manual override, built on the planned `/parts_from_curves`, kept through
    later re-runs while it still fits, and flagged if the model changes under
    it.
  - **Move** an object in the stand: its position updates.
- **Conflicts.** Each object has a version. If both sides changed it, the
  Rhino edit wins for that object and the app says so.
- **Claude knows.** Rhino edits appear in the chat's context ("Tobias moved
  slot 3 on rib X3 in Rhino"), so Claude builds on them instead of undoing
  them.
- **Rhino as the render view.** A plugin command saves named Rhino views as
  renders into the concept's `renders/`, for the pitch PDF.

Build path: a prototype as a Rhino 8 Python 3 script (Script Editor,
RhinoCommon events) to settle the behaviour quickly, then a packaged plugin
installed from Rhino's Package Manager. The Grasshopper components stay for
parametric work. Everything stays on `127.0.0.1`.

Testing: the engine side (merging edits, overrides, versions) is tested in CI
like the rest. The plugin itself needs Rhino, so each release gets a short
manual test script run on a Mac with Rhino.

## 5. Phase 1: brief, constraints, concepts and quotes

**Recommended first.** It is the core of a pitch, needs no accounts or outside
services, and every later phase reads its files.

### 5.1 The brief

A **Brief** panel (and a `set_brief` tool so Claude can fill it in from a
conversation such as "10 × 3 m booth, counter and 6 stools, budget 8k"):

| Field | Example | Used for |
|---|---|---|
| Stand footprint W × D, max height | 6000 × 3000, 2400 mm | Hard limit on the scene's bounding box |
| Venue limits | max height 2500, no overhead rigging | Hard limit |
| Items wanted | 1 counter, 6 stools, 1 back wall, 2 plinths | What Claude builds |
| Loads | stools: seated adult; counter: 30 kg on top; shelf: 10 kg | Structural check (3.3) |
| Seating | 6 seats, height 450 mm | Items + loads |
| Board | 10 mm honeycomb, 2500 × 1250 sheets, fire-rated | Engine thickness, nesting sheet size |
| Shipping | flat-packs onto 1 pallet, 1200 × 1000 | Pack volume check |
| Assembly | tool-free, under 2 h for 2 people | Prefer slot joints, report part count |
| Budget | 8000 ex GST | Quote check |
| Branding | logo on counter front, client colours | Print areas (Phase 3) |

Claude sees the brief on every turn. After each change the engine runs
`check_brief` and shows pass/warn/fail chips ("height 2380 of 2400 ✓",
"quote 8650 of 8000 ✗"), and Claude has to address a failure before calling a
concept done.

### 5.2 Concepts and versions

- A concept strip above the preview: **A, B, C, D**, each with a name, a
  thumbnail and its quote total. Click to switch; the chat and preview follow.
- "Make 3 concepts" is a normal request: Claude builds each one in turn, in
  different construction styles (waffle, folded, stacked) so the client sees
  real alternatives, not three sizes of the same thing.
- **Freeze version** (and automatically when a pitch is exported) copies the
  concept to the next `vN` folder.
- **Compare** shows the concepts side by side: render, footprint, sheets,
  build hours, price, brief checks.

### 5.3 Structural checks (honest scope)

The engine can give an **indicative** check, not an engineering sign-off:

- Vertical capacity of ribs and walls from the board's edge crush strength
  (supplier's ECT, kN/m) times the loaded edge length, with a safety factor
  (start at 3 for seats, which take impact loads).
- Slenderness warning for tall thin ribs that would buckle before crushing.
- Shelf and counter-top span warning from a simple beam check.

Results show as "seat: about 4× the design load ✓". The numbers are only as
good as the board data, so Tobias's supplier sheets are needed (section 10),
and the first build of any new seat design should still be load-tested. The
check exists to stop obviously weak proposals early, and to let Claude choose
rib count and spacing for a load instead of guessing.

### 5.4 Quotes

Extend the cut estimate into a full quote per concept. The rates are project
settings (3.2) and each line is a formula (3.3), so the quote is worked out by
the engine and old quotes keep their rates and formulas:

| Line | From |
|---|---|
| Board | sheets × price per sheet grade |
| Cutting | machine time × machine rate (exists today) |
| Print | printed area m² × rate (Phase 3 adds print areas) |
| Assembly / install | parts and joints × minutes each, or Tobias's own hours |
| Packing and freight | pack volume, pallets × rate |
| Design time | hours × rate |
| Margin, tax | percentages |

`quote.json` holds the lines; `quote.csv` opens in any spreadsheet. A **Pitch
PDF** export puts each concept on a page: render, short description written by
Claude, key sizes, brief checks and price.

### 5.5 Phase 1 build steps

Each is a separate PR, test first as usual.

1. Engine: scene with several named objects, each with its own parts; nest the
   whole scene. (Unblocks everything else.)
2. Engine: brief schema, `set_brief`, `check_brief` with the bounding box,
   sheet, pack and budget checks.
3. Engine: quote lines as formulas over settings and measurements;
   `quote.json`/`.csv` export. (Needs the foundation, section 3.)
4. Engine: indicative structural check, once board data is in hand.
5. App: brief panel and check chips, built as a JSON view.
6. App: concepts strip, freeze version, compare view.
7. App: pitch PDF (renders from the existing preview).

## 6. Phase 2: module library

A shared library of proven pieces Claude can reuse: the cylinder stool, a
counter, a plinth, a shelf unit.

### What a module is

A folder in the library (which can sit in a shared Drive folder, see Phase 4):

```
Library/
└── Cylinder stool waffle/
    ├── module.json      # name, tags, parameters with ranges, ratings
    ├── recipe.json      # the engine steps that build it, with parameters
    ├── thumbnail.png
    ├── model.obj
    └── cut/             # reference cut files at the default size
```

The important part is `recipe.json`. Because Claude builds everything through
engine steps, a module is saved as its steps ("cylinder W × H, waffle with N
ribs, board T"), so it can be rebuilt at a new size or board instead of being
a frozen shape. `module.json` records what has been learned about it: tested
load, seats, typical cost, cut time, and which parameter ranges are proven.

### What it adds

- **Save to library** on any object in a concept (Claude suggests name, tags
  and which numbers should be parameters).
- A **Library** sidebar: grid of thumbnails, filter by tag (seating, counter,
  display), search, drag a module into the scene.
- Claude tools: `library_search` (by tags, size, load), `library_place`
  (build a module with given parameters at a position), `save_to_library`.
- Claude's habit: prefer a proven module when it fits the brief, propose a
  variant when it nearly fits ("the stool at 600 mm tall as a bar stool, 7
  ribs"), and say when it is designing something new and untested.
- Library items marked **tested** (by Tobias) rank above untested ones.

## 7. Phase 3: inspiration board

Drag images onto the chat or a **Inspiration** tray: the client's logo, photos
of their products or past stands, sketches, mood boards, PDFs of their brand
guide.

- Images are copied into `inspiration/` and sent to Claude as image input.
  Claude can read shapes, colours, materials and the feel of an image, read a
  hand sketch with dimensions, and pick up brand colours and typefaces from a
  guide.
- Claude writes a short note per image ("curved, layered, warm timber feel;
  brand colours #0B3D2E and #F2C94C") into `notes.json`, so later turns and
  later sessions use the notes rather than re-reading every image.
- "Design three concepts from this board" then works as in Phase 1, with each
  concept saying which images it drew on.
- **Logos and artwork**: a logo can become an etch layer (traced to vectors)
  or a **print area** on a panel. Print areas carry the artwork placement and
  size into the quote (print m²) and into a print-ready export per panel
  later. Cardboard stands are usually printed flat before cutting, so the
  print export lines artwork up with the cut lines.
- Limits to know: Claude does not paint photoreal renders; renders come from
  the 3D preview (and Rhino, once the live link exists). Client images stay in
  the project folder and are only sent to the Claude API.

## 8. Phase 4: Google Drive, OneDrive and live budget sheets

### 8.1 Two stages

**Stage A, no sign-in needed (can ship with Phase 1).** Google Drive for
desktop and the OneDrive app already put a synced folder on the Mac. If the
project folder and library live there, every file (cut files, renders,
`quote.csv`, pitch PDF) is in Drive or OneDrive within seconds, with Drive's
own version history and sharing. CardboardCAD only needs a "Projects location"
setting. This covers "export everything into a standard project directory"
with no accounts, no API keys and nothing to approve.

**Stage B, sign in from the app.** Needed for what a synced folder can't do:
a **live budget sheet** that updates as the design changes, opening a client
folder shared with Tobias by someone else, and working on a Mac without the
sync app.

- **Sign in with Google** and **Sign in with Microsoft** in Settings. The app
  opens the system sign-in sheet, the person approves, and the tokens go into
  the Keychain like the Claude key does now. There is no CardboardCAD account
  or server: the app talks to Google and Microsoft directly.
- The app creates (or links) the project's folder in Drive/OneDrive and keeps
  it in step.

### 8.2 Live budget sheet

The app owns one tab, Tobias owns the rest:

- The app writes a **CardboardCAD** tab: one block per concept and version
  with the quantities and quote lines (sheets, board m², cut minutes, parts,
  print m², pack volume, line totals). It rewrites only that tab.
- Tobias's existing budget tabs pull from it with ordinary formulas
  (`=CardboardCAD!C12`), so his layout, markups and formatting stay his.
- Updates are pushed when a step finishes (not on every keystroke), with a
  "last synced" stamp in the sheet.
- Frozen versions get their own block, so the sheet can show A v1, A v2, B v1
  side by side for the client meeting.

We start from a copy of Tobias's current budget sheet so the tab fits his
formulas, rather than inventing a new layout.

### 8.3 Accounts and approvals to set up

| Google | Microsoft |
|---|---|
| A Google Cloud project (free), owned by Fold Theory | An app registration in Microsoft Entra (free), owned by Fold Theory |
| OAuth consent screen + a "Desktop app" client ID | "Public client" with a redirect for the Mac app |
| Drive API and Sheets API switched on | Microsoft Graph, Files.ReadWrite + offline_access |
| Scope `drive.file`: the app only sees files it created or the person picked. Google classes this as non-sensitive, so no security assessment | Excel updates through Graph's workbook API (works on OneDrive for Business / SharePoint; check personal OneDrive before relying on it) |
| If Fold Theory uses Google Workspace, mark the app **Internal**: no Google review, no 100-user cap, no 7-day token expiry. With personal Gmail accounts it has to be published (light brand check) or people re-sign-in weekly while it is in testing | Single tenant if everyone is in Fold Theory's Microsoft 365; multi-tenant only if clients sign in too |

Recommendation: do Google first, since Tobias already budgets in Google
Sheets. Add Microsoft only if a client or staff member actually needs
OneDrive or Excel; Stage A already covers "save into OneDrive".

The Apple Developer account (needed anyway to sign and notarize the app) is
the only other account involved.

## 9. Phase 5: learning Tobias's style

**Plain answer on fine-tuning:** training a custom Claude model on Tobias's
files isn't available to us for these models, and with tens of past projects
it wouldn't be the right tool anyway. What works, and what we'd build, is
giving Claude Tobias's knowledge at the moment it designs:

1. **House playbook.** A written Fold Theory guide that goes into every
   conversation: preferred board and joints, slot and tab sizes, rib spacing,
   how he handles seat loads, what he avoids, how he prices, how he names
   layers in cut files. We draft it by interviewing Tobias (a one-hour chat
   with Claude taking notes) and refine it as he corrects designs. This is the
   cheapest, biggest win.
2. **Reference projects.** Import past jobs (Rhino `.3dm` or DXF cut files,
   photos, final price, what the client said) into a **Reference** library.
   For a new brief the app finds the two or three closest past jobs by type,
   size and budget, and shows them to Claude as worked examples. Claude can
   say "this is close to the Expo 2025 counter, here is what I changed".
3. **Measured defaults.** The engine reads his cut files and measures what he
   actually uses (slot clearance, kerf, tab widths, rib spacing, board) and
   sets those as the defaults, so even quick designs come out in his
   tolerances.
4. **Corrections loop.** When Tobias edits a design or rejects a concept, the
   reason is saved and added to the playbook, so the same mistake isn't made
   twice.

Reading `.3dm` files needs the `rhino3dm` library in the engine (check it has
a macOS arm64 build when we get there); DXF import builds on the planned
`/parts_from_curves`.

## 10. Recommended order

| # | Step | Why this position | Depends on |
|---|---|---|---|
| done | Project saving, Grasshopper live sync, Design Assistant workflows | Built | |
| 1 | **Foundation A**: project settings, input forms in chat, "Claude never calculates", diagnostics, formula engine, view renderer with the Project view as its first JSON view | Quotes, the brief panel and the pitch all stand on it; building screens as JSON views from the start avoids building them twice | |
| 2 | **Phase 1**: scene, brief and checks, concepts and versions, quote and invoice PDF, pitch PDF, projects in a synced Drive folder | Core of every pitch | Foundation A |
| 3 | **Two-way Rhino**: prototype script, then plugin | What makes the app usable by designers day to day. Separate code area, so it can run alongside step 2 | Scene (for multi-object layers) |
| 4 | **Foundation B**: proposals, project workflows with manual steps and status, Claude editing views and formulas | Turns the app into something Claude and Tobias keep reshaping without releases | Foundation A |
| 5 | Phase 2: module library | Reuse of proven designs | Scene, proposals |
| 6 | Phase 3: inspiration board | Client-meeting "wow" | Concepts |
| 7 | Phase 4: sign-in and live budget sheet | Needs Fold Theory's Google project and Tobias's sheet | Quotes |
| 8 | Phase 5: Tobias's style | Best once Tobias is using it and correcting it | Library, past projects |

The playbook interview in Phase 5 costs almost nothing and can happen any
time; doing it early improves every later step.

## 11. Running costs

| Item | Cost |
|---|---|
| Claude API, normal design chat | A few cents per message, as today |
| Claude API, a 3–4 concept pitch with 10 inspiration images | Roughly 1–4 USD per pitch on Claude Opus 5.5 (4 USD per million input tokens, 20 per million output; an image is about 1,500 input tokens). About half that on Sonnet 5.5 |
| Google Drive / Sheets APIs | Free at this volume |
| Microsoft Graph | Free at this volume |
| Google Cloud project, Entra app registration | Free |
| Apple Developer Program | 99 USD/year (already needed for signing) |
| Servers | None: everything runs on the Mac and in Fold Theory's own Drive |

Storage is whatever Fold Theory's Google Workspace or Microsoft 365 plan
already includes.

## 12. What we need from SM and Tobias

For Phase 1:

- **Rate card**: board price per sheet for each grade they use, sheet sizes,
  machine rate, print rate per m², assembly and install rates, freight, design
  hourly rate, usual margin.
- **Board data** from the supplier: thickness, flute or honeycomb type, edge
  crush (ECT) or compression values, fire rating.
- **The cutter**: make and model (Zünd, Kongsberg, laser…), the file format and
  layer names it expects, real cut and crease speeds.
- **Two or three past briefs** with the final design and price, to test that
  the brief and quote match how Fold Theory really works.

For later phases:

- **Rhino version** on Tobias's Mac (7 or 8) and a typical project file, to
  build the Rhino plugin against (two-way Rhino).
- **Company details for invoices**: legal name, ABN, address, bank details,
  quote terms and validity, invoice numbering (quote and invoice PDF).
- The **job stages** Fold Theory actually uses, from lead to paid (project
  workflows).
- A copy of Tobias's **budget Google Sheet** (Phase 4).
- Whether Fold Theory uses **Google Workspace** (a company domain) or personal
  Gmail, and whether anyone needs Microsoft 365 (Phase 4).
- Someone at Fold Theory to own the **Google Cloud project** and, if needed,
  the **Entra app registration**; we can walk through the setup (Phase 4).
- **5–10 past projects** as Rhino files or DXFs, with photos and final prices,
  and an hour with Tobias for the playbook interview (Phase 5).
- Which past designs count as **tested** modules for the library (Phase 2).

## 13. Open questions

- Will anyone other than Tobias use the app (staff, a second Mac)? That
  decides whether the library needs to live in a shared Drive folder from day
  one.
- Should clients ever see something live (a share link to a concept), or is
  the PDF and the Drive folder enough?
- Does Fold Theory print on the board before cutting, and with whom? That
  shapes the print-area export in Phase 3.
- Currency and tax for quotes (AUD and GST assumed in examples).
- Which accounting package Fold Theory uses (Xero, MYOB, none), if invoices
  should go there later.
- Will anyone need the app on an iPad or a second Mac? Only then is CloudKit
  sync worth it (3.9).

## 14. Issues

Each feature has a GitHub issue, grouped under one parent issue per phase.

| Parent | Issues |
|---|---|
| #19 Foundation | #26 project settings · #27 input forms in chat · #28 Claude never calculates · #29 formula engine · #30 JSON views · #31 locked core and change history · #32 proposals · #33 project workflows and status · #34 project view · #35 workflow view · #36 storage (files + SwiftData index) · #61 diagnostics Claude can read |
| #20 Two-way Rhino | #37 design as tagged Rhino objects · #38 model edits flow back · #39 part edits as overrides · #40 conflicts and telling Claude · #41 Rhino renders for the pitch · #42 packaged plugin |
| #21 Phase 1 | #43 scene · #44 brief and checks · #45 structural check · #46 concepts and versions · #47 quote and invoice PDF · #48 pitch PDF · #49 projects location (Drive/OneDrive/iCloud) |
| #22 Phase 2 | #50 library · #51 Claude uses the library |
| #23 Phase 3 | #52 inspiration tray · #53 logos and print areas |
| #24 Phase 4 | #54 Google sign-in and Drive · #55 live budget sheet · #56 Microsoft (only if needed) |
| #25 Phase 5 | #57 house playbook · #58 reference projects · #59 measured defaults · #60 corrections loop |

Suggested first issues, in order: #26, #27, #28, #61, #29, #30, #34, then #43.

