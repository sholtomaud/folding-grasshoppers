# Geometry endpoints (milestone 2)

The cardboard work for Fold Theory: turn a Rhino/Grasshopper model into flat
parts, lay them on sheets, and write cut files. This is the design for
SPEC.md §14 "Geometry endpoints". Each endpoint ships in its own PR.

| Endpoint | Status | PR |
|---|---|---|
| `/waffle_slice` | done | 1 |
| `/export` (DXF + SVG) | done | 1 |
| `/contour_stack` | planned | 2 |
| `/nest` (multi-sheet) | planned | 3 |
| `/unfold` | planned | 4 |
| `/parts_from_curves` (hand-drawn parts into the pipeline) | planned | 3 |

## How the pieces fit

```
 Mesh ──► /waffle_slice ──┐
 Mesh ──► /contour_stack ─┼─► P (parts) ──► /nest ──► P (placed) ──► /export ──► .dxf / .svg
 Mesh ──► /unfold ────────┘                  ▲
 Curves ► /parts_from_curves ────────────────┘
```

Every generator outputs **P**, a `cardboardcad.parts/1` JSON text
(`components/parts.py`). It keeps what Grasshopper curves alone lose: which
lines are cuts, scores or etches, the part labels, and where nesting placed
each part. Generators also output ordinary Grasshopper curves so the result
can be previewed and used in other definitions.

```json
{"schema": "cardboardcad.parts/1", "units": "mm", "sheet_size": [1200, 800],
 "parts": [{"id": "X1", "label": "X1", "label_at": [30, 12],
            "cut":   [[[0, 0], [60, 0], [60, 40], [0, 40]]],
            "score": [], "etch": [],
            "placement": {"sheet": 0, "x": 10, "y": 10, "rotation": 90}}]}
```

- `cut`: closed loops (outline and holes). `score`: fold creases.
  `etch`: surface marks (alignment outlines). Each part is in its own frame.
- `placement`: rotate about the part's origin, then move the rotated
  bounding box's lower-left corner to (x, y) on sheet `sheet`.
- Parts without a placement are laid in a row on sheet 0.

## Rules every endpoint follows

- Pure function in `engine/cardboard_engine/components/`, Hops wrapper in
  `hops_geometry.py`, plain-JSON twin at `/api/<name>` for the app.
- Units are mm. Inputs are validated; a bad input raises and Hops shows it
  as a red component error. Never a silent empty result.
- Fold Theory icon, category `Fold Theory`, subcategory `CardboardCAD`.
- Geometry comes in as `rhino3dm.Mesh` (Hops `Mesh` param). rhino3dm cannot
  mesh or intersect Breps, so the engine slices meshes itself. In
  Grasshopper, put a **Mesh Brep** component in front for Breps.
- New native dependency: **pyclipper** (polygon booleans and offsets: slots,
  kerf, nesting). One ~270 KB universal2 `.so`, no dependencies, signed by
  `sign.sh` with everything else. No numpy, no shapely/GEOS.

## `/waffle_slice` (done)

Interlocking grid of ribs: X ribs slotted from the top, Y ribs from the
bottom, each slot half the height of the crossing.

| In | Nick | Type | Default |
|---|---|---|---|
| Mesh | M | Mesh | |
| X ribs | Nx | Integer | 5 |
| Y ribs | Ny | Integer | 5 |
| Thickness (mm) | T | Number | 3.0 |
| Slot clearance (mm) | C | Number | 0.2 |
| Kerf (mm) | K | Number | 0.0 |

| Out | Nick | |
|---|---|---|
| Parts | P | parts document |
| Cut curves | Cut | flat outlines in a row on XY |
| Ribs 3D | R3 | slotted ribs in place (assembly preview) |
| Labels | L | `X1..Xn`, `Y1..Yn`; a rib that falls apart gets `X2a`, `X2b` |

Ribs are evenly spaced inside the bounding box. Slot width is `T + C`; kerf
grows each outline by `K/2` (so slots narrow by `K`). Errors: no ribs,
negative values, rib spacing not wider than the slot, a mesh no rib cuts.

## `/export` (done)

| In | Nick | Type | Default |
|---|---|---|---|
| Parts | P | Text | |
| Format | F | Text | `dxf` (or `svg`) |
| Name | N | Text | `cardboardcad` |
| Folder | D | Text | empty = `~/Documents/CardboardCAD` |
| Write | W | Boolean | False |

Outputs `Paths` (files written) and `Text` (file contents, one per sheet).
One file per sheet: `<name>-sheet<n>.<ext>`. DXF is ASCII R12 with layers
`CUT` (red), `SCORE` (blue), `ETCH` (green), `LABEL` (text), `SHEET` (sheet
outline, once nested). SVG is in real mm with the same layers as groups.

Any local process can reach the engine, so the folder must be inside the
home folder, the name is sanitised, and the extension is forced.

## `/contour_stack` (planned, PR 2)

Stacked horizontal layers (the classic "sliced model" in board).

Inputs: Mesh M; Layer thickness T (3.0); Kerf K (0); Etch next layer E
(True); Axis A (`z`). Outputs: P, Cut curves, Layers 3D, Labels (`L01..`).

Layer k is the section at the middle of its band. With E on, each layer gets
the outline of the layer above etched on it, for registration. A layer that
falls apart gets `L05a`, `L05b`. Later: dowel holes through common area.

## `/nest` (planned, PR 3)

Multi-sheet nesting of a parts document.

Inputs: Parts P; Sheet width W (1200); Sheet height H (800); Gap G (5);
Margin E (10); Rotations R (`0,90,180,270`); Grain lock (False).
Outputs: P (with placements and `sheet_size`), Sheets (sheet outlines),
Placed curves (sheets side by side on XY), Sheet count, Use % per sheet,
Unplaced (labels that did not fit).

v1 is true-shape bottom-left fill: for each part (largest first) and each
allowed rotation, candidate positions come from the no-fit polygon against
placed parts (pyclipper Minkowski sums of the gap-offset outlines), and the
lowest-then-leftmost feasible spot wins; a new sheet opens when nothing
fits. A part larger than the sheet is an error, not a silent drop.
`/parts_from_curves` (same PR) turns Grasshopper curves into parts (Cut,
Score, Etch lists plus a label) so hand-drawn pieces can be nested too.

## `/unfold` (planned, PR 4)

Unfold a polyhedral mesh (planar triangles or quads) into flat nets with
fold lines and glue tabs.

Inputs: Mesh M; Tab width (mm) Tw (8, 0 = no tabs); Tab angle Ta (45°);
Flat angle (°) Fa (1, folds flatter than this are not scored).
Outputs: P, Cut curves, Score curves, Labels, Net count.

Faces are joined across edges along a spanning tree (flattest folds first);
each face is placed by rotating it about its shared edge. A face that would
overlap the net starts a new net. Cut edges get matching numbers on both
sides and a tab on one side. Mountain and valley folds are written to
`score` with the fold angle kept for a later "score from the back" option.

## Bundle size and signing

| Added | Size | Native |
|---|---|---|
| pyclipper 1.4.0 | ~270 KB wheel | 1 `.so` (universal2) |

Nothing else is planned. rhino3dm was already in the runtime (ghhops_server
depends on it). ezdxf is a dev-only test dependency (independent DXF reader
in the tests) and is not in `requirements.lock`, so it never ships.
