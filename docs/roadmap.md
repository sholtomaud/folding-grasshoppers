# Roadmap: from design tool to Fold Theory's quoting studio

Status: proposal, nothing here is built yet. Owner of the finished app: Tobias
(Fold Theory). Written 2026-11-26.

Today CardboardCAD can take a request in plain words ("a cylinder stool 450 mm
tall as a waffle from 6 mm board"), build it with the engine, show it in 3D and
as cut sheets, nest it and estimate cut time and cost. This roadmap covers what
it takes to make it the place where Tobias pitches a tradeshow stand to a
client: a brief with hard limits, three or four concepts side by side, each
with its own quote and renders, a library of proven modules, inspiration
images from the client, Google Drive or OneDrive storage with live budget
sheets, and an assistant that designs the way Tobias does.

Saving projects and live sync with Rhino are being built separately (the
"project saving and Rhino live sync" work). Everything below builds on the
project folder that work introduces, and on its two
Grasshopper components (one that follows the app's design live, one that sends
a Rhino mesh into the app).

## Contents

1. [Where things stand](#1-where-things-stand)
2. [The project folder everything builds on](#2-the-project-folder-everything-builds-on)
3. [Phase 1: brief, constraints, concepts and quotes](#3-phase-1-brief-constraints-concepts-and-quotes)
4. [Phase 2: module library](#4-phase-2-module-library)
5. [Phase 3: inspiration board](#5-phase-3-inspiration-board)
6. [Phase 4: Google Drive, OneDrive and live budget sheets](#6-phase-4-google-drive-onedrive-and-live-budget-sheets)
7. [Phase 5: learning Tobias's style](#7-phase-5-learning-tobiass-style)
8. [Recommended order](#8-recommended-order)
9. [Running costs](#9-running-costs)
10. [What we need from SM and Tobias](#10-what-we-need-from-sm-and-tobias)
11. [Open questions](#11-open-questions)

## 1. Where things stand

| Have | Missing for pitching |
|---|---|
| Design Assistant chat, Claude picks engine steps | A brief: size limits, loads, seating, budget |
| One model per session (create, import STL/OBJ, scale) | Several objects in one stand, and several concepts |
| Waffle, contour stack, unfold, nest, DXF/SVG export | Saved projects and versions (in progress elsewhere) |
| Cut time and machine/material cost | A full quote: labour, print, freight, margin |
| Hops endpoints for Grasshopper | Live link back to Rhino (in progress elsewhere) |
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

## 3. Phase 1: brief, constraints, concepts and quotes

**Recommended first.** It is the core of a pitch, needs no accounts or outside
services, and every later phase reads its files.

### 3.1 The brief

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

### 3.2 Concepts and versions

- A concept strip above the preview: **A, B, C, D**, each with a name, a
  thumbnail and its quote total. Click to switch; the chat and preview follow.
- "Make 3 concepts" is a normal request: Claude builds each one in turn, in
  different construction styles (waffle, folded, stacked) so the client sees
  real alternatives, not three sizes of the same thing.
- **Freeze version** (and automatically when a pitch is exported) copies the
  concept to the next `vN` folder.
- **Compare** shows the concepts side by side: render, footprint, sheets,
  build hours, price, brief checks.

### 3.3 Structural checks (honest scope)

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

### 3.4 Quotes

Extend the cut estimate into a full quote per concept, using a **rate card**
saved in Settings (and in the project, so old quotes keep their rates):

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

### 3.5 Phase 1 build steps

Each is a separate PR, test first as usual.

1. Engine: scene with several named objects, each with its own parts; nest the
   whole scene. (Unblocks everything else.)
2. Engine: brief schema, `set_brief`, `check_brief` with the bounding box,
   sheet, pack and budget checks.
3. Engine: rate card and quote lines; `quote.json`/`.csv` export.
4. Engine: indicative structural check, once board data is in hand.
5. App: brief panel and check chips.
6. App: concepts strip, freeze version, compare view.
7. App: pitch PDF (renders from the existing preview).

## 4. Phase 2: module library

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

## 5. Phase 3: inspiration board

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

## 6. Phase 4: Google Drive, OneDrive and live budget sheets

### 6.1 Two stages

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

### 6.2 Live budget sheet

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

### 6.3 Accounts and approvals to set up

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

## 7. Phase 5: learning Tobias's style

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

## 8. Recommended order

| # | Phase | Why this position | Depends on | Size |
|---|---|---|---|---|
| 0 | Project saving + Rhino live sync | In progress elsewhere | | |
| 1 | Brief, constraints, concepts, quotes, pitch PDF, plus Stage A cloud folder | Core of every pitch, no accounts needed, all later phases read its files | Project folder | Largest: 7 PRs |
| 2 | Module library | Turns each finished design into a reusable asset; makes concepts faster and more reliable | Scene (Phase 1 step 1) | 3–4 PRs |
| 3 | Inspiration board | Big "wow" for the client meeting, mostly app work since Claude already reads images | Phase 1 concepts | 2–3 PRs |
| 4 | Sign-in + live budget sheet | Needs Fold Theory's Google project and Tobias's sheet; Stage A already covers file export | Phase 1 quotes | 3 PRs + account setup |
| 5 | Tobias's style | Best done once Tobias is using the app and correcting it | Library, past project files | Playbook any time; rest 3 PRs |

The playbook interview in Phase 5 costs almost nothing and can happen any
time; doing it early improves every later phase.

## 9. Running costs

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

## 10. What we need from SM and Tobias

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

- A copy of Tobias's **budget Google Sheet** (Phase 4).
- Whether Fold Theory uses **Google Workspace** (a company domain) or personal
  Gmail, and whether anyone needs Microsoft 365 (Phase 4).
- Someone at Fold Theory to own the **Google Cloud project** and, if needed,
  the **Entra app registration**; we can walk through the setup (Phase 4).
- **5–10 past projects** as Rhino files or DXFs, with photos and final prices,
  and an hour with Tobias for the playbook interview (Phase 5).
- Which past designs count as **tested** modules for the library (Phase 2).

## 11. Open questions

- Will anyone other than Tobias use the app (staff, a second Mac)? That
  decides whether the library needs to live in a shared Drive folder from day
  one.
- Should clients ever see something live (a share link to a concept), or is
  the PDF and the Drive folder enough?
- Does Fold Theory print on the board before cutting, and with whom? That
  shapes the print-area export in Phase 3.
- Currency and tax for quotes (AUD and GST assumed in examples).
