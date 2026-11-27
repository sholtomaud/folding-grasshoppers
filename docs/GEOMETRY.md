# Geometry endpoints (milestone 2)

The cardboard work for Fold Theory: turn a Rhino/Grasshopper model into flat
parts, lay them on sheets, and write cut files. This is the design for
SPEC.md §14 "Geometry endpoints". Each endpoint ships in its own PR.

| Endpoint | Status | PR |
|---|---|---|
| `/waffle_slice` | done | 1 |
| `/export` (DXF + SVG) | done | 1 |
| `/contour_stack` | done | 2 |
| `/nest` (multi-sheet) | done | 3 |
| `/unfold` | done | 4 |
| `/parts_from_curves` (hand-drawn parts into the pipeline) | planned | 5 |

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
- Fold Theory icon, category `Fold Theory`, subcategory `Fold Theory`.
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
| Folder | D | Text | empty = `~/Documents/Fold Theory` |
| Write | W | Boolean | False |

Outputs `Paths` (files written) and `Text` (file contents, one per sheet).
One file per sheet: `<name>-sheet<n>.<ext>`. DXF is ASCII R12 with layers
`CUT` (red), `SCORE` (blue), `ETCH` (green), `LABEL` (text), `SHEET` (sheet
outline, once nested). SVG is in real mm with the same layers as groups.

Any local process can reach the engine, so the folder must be inside the
home folder, the name is sanitised, and the extension is forced.

## `/contour_stack` (done)

Stacked layers of board (the classic "sliced model").

| In | Nick | Type | Default |
|---|---|---|---|
| Mesh | M | Mesh | |
| Layer thickness (mm) | T | Number | 3.0 |
| Kerf (mm) | K | Number | 0.0 |
| Etch | E | Boolean | True |
| Axis | A | Text | `z` (or `x`, `y`) |

| Out | Nick | |
|---|---|---|
| Parts | P | parts document |
| Cut curves | Cut | flat layer outlines in a row on XY |
| Etch curves | Etch | alignment lines, same layout |
| Layers 3D | L3 | layer outlines in place (stack preview) |
| Labels | L | `L01`, `L02`... bottom up; a layer that falls apart gets `L05a`, `L05b` |

The model is cut into bands T thick from the bottom of its bounding box;
each layer is the section through the middle of its band (a thinner last
band still gets one). With E on, each layer is etched with the parts of its
neighbours' outlines that land on it, which is where the next layer sits.
Later: dowel holes through the common area.

## `/nest` (done)

| In | Nick | Type | Default |
|---|---|---|---|
| Parts | P | Text | |
| Sheet width (mm) | W | Number | 1200 |
| Sheet height (mm) | H | Number | 800 |
| Gap (mm) | G | Number | 5 |
| Margin (mm) | E | Number | 10 |
| Rotations | R | Text | `0,90` (`0` locks the grain) |

| Out | Nick | |
|---|---|---|
| Parts | P | parts with placements and `sheet_size`: connect to Export |
| Sheets | S | sheet outlines side by side on XY (preview) |
| Placed curves | Cut | cut outlines on their sheets |
| Sheet count | N | |
| Use % | U | part area / sheet area, per sheet |

True-shape bottom-left fill, largest part first, first sheet with room.
For each allowed rotation the feasible positions are the inner-fit rectangle
minus the no-fit polygons (pyclipper Minkowski sums of outlines grown by
G/2) of the parts already on the sheet; the lowest, then leftmost, vertex
wins. Outlines are simplified to at most 48 vertices for nesting and grown
by the simplification tolerance, so the result stays conservative. A part
that fits no sheet in any allowed rotation is an error naming it.

Limits (v1): parts are never placed inside another part's hole; no
"pocket" positions a part could only slide into. A 16-rib waffle on
1000 x 700 mm sheets with four rotations takes about 5 s.

`/parts_from_curves` (PR 5) will turn Grasshopper curves into parts so
hand-drawn pieces can be nested too. It needs care because Hops 1.5.5
cannot send an empty optional input.

## `/unfold` (done)

| In | Nick | Type | Default |
|---|---|---|---|
| Mesh | M | Mesh | flat faces: triangles or planar quads |
| Tab width (mm) | Tw | Number | 8 (0 = no tabs) |
| Tab angle (deg) | Ta | Number | 45 |
| Flat angle (deg) | Fa | Number | 1 (flatter folds are not scored) |

| Out | Nick | |
|---|---|---|
| Parts | P | parts document (nets, with edge numbers as texts) |
| Cut curves | Cut | net outlines including tabs, in a row on XY |
| Score curves | Score | folds and tab folds |
| Labels | L | `N1`, `N2`... largest first |
| Net count | N | |

Vertices sharing a position are welded first (Rhino splits them at
creases); non-planar quads are split into triangles. Nets grow from the
largest free face across edges, flattest fold first, then longest edge; a
face that would overlap its net waits for another edge or starts a new net.
Both sides of every cut edge get the same number (LABEL layer), and the
first side with room gets a trapezoid glue tab (a triangle on short edges)
with a fold at its base. Later: mountain/valley distinction for scoring
from the back.

## Bundle size and signing

| Added | Size | Native |
|---|---|---|
| pyclipper 1.4.0 | ~270 KB wheel | 1 `.so` (universal2) |

Nothing else is planned. rhino3dm was already in the runtime (ghhops_server
depends on it). ezdxf is a dev-only test dependency (independent DXF reader
in the tests) and is not in `requirements.lock`, so it never ships.
