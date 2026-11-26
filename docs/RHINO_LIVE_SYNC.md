# Rhino live sync

Show the design that is open in CardboardCAD inside Rhino, and keep it up to
date while you (or the Design Assistant) change it. You can also send a mesh
from Rhino to CardboardCAD.

You need: Rhino 8 for Mac (Rhino 7 works too), with Hops installed (type
`PackageManager` in Rhino's command line, search **Hops**, **Install**,
restart Rhino), and CardboardCAD running with the **green** engine dot.

## How it works

Hops only asks the engine for an answer when one of its inputs changes. So
the live link has two Grasshopper pieces:

```
 CardboardCAD app                         Grasshopper
 ───────────────                          ───────────
 design changes ──► version 12            [CardboardCAD Live listener]  (Python script component)
                    /api/design/version ◄── asks every 0.5 s, in the background
                                              │ Version = 12 (only re-runs when it changes)
                                              ▼
 /live_design   ◄──────────────────────── [Hops: LiveDesign]  V input
                ──────────────────────────► Model, Parts 3D, Cut, Score, Sheets, Labels, Parts, Summary
```

1. **CardboardCAD Live listener**: a short Python script for a Grasshopper
   script component. It asks the app for its *design version*, a number that
   goes up every time the design changes. It does this on a background
   thread, and only re-runs the Grasshopper definition when the number
   changes, so nothing is recomputed while the design stays the same.
2. **LiveDesign** (Hops, `http://127.0.0.1:5001/live_design`): give it the
   version, and it returns the app's current design as Rhino geometry.

And the other way:

3. **SendToCardboardCAD** (Hops, `http://127.0.0.1:5001/send_to_cardboardcad`):
   a mesh from Rhino becomes the app's model, ready for the Design Assistant
   to slice, nest and cost ("make this a waffle from 6 mm board").

Everything stays on your own Mac (`127.0.0.1`). Nothing goes over the network.

## Set it up (once per Grasshopper file)

### 1. Add the listener

1. In CardboardCAD, choose **Design > Copy Rhino Live Listener Script**. The
   script is now on the clipboard. (It is also at
   `http://127.0.0.1:5001/rhino/live_listener.py`, and in this repository at
   `engine/cardboard_engine/assets/rhino_live_listener.py`.)
2. In Rhino, type `Grasshopper` and press Return.
3. Add a Python script component:
   - Rhino 8: tab **Maths**, group **Script**, **Python 3 Script**
     (or double-click the canvas, type `Python 3 Script`, press Return).
   - Rhino 7: tab **Maths**, group **Script**, **GhPython Script**.
4. Double-click the component to open its editor. Select all the text in it
   (⌘A), paste (⌘V), and close the editor (Rhino 8: the **Save and close**
   button or ⌘S then close the window; Rhino 7: **OK**).
5. Set its inputs and outputs. Zoom in on the component until you see small
   **+** and **−** signs next to the inputs and outputs. Use them, then
   right-click each one and **Rename** it, so the component has exactly:

   | Inputs | Type (right-click > Type hint) | Optional? |
   |---|---|---|
   | `On` | bool | yes, default on |
   | `Interval` | int | yes, default 500 (milliseconds) |
   | `Port` | int | yes, default 5001 |

   | Outputs |
   |---|
   | `Version` |
   | `Status` |

   In Rhino 8, the default script component has inputs `x`, `y` and output
   `a`: rename `x` to `On`, `y` to `Interval`, add a third input `Port`,
   rename `a` to `Version` and add a second output `Status`. (Rhino 8 may also
   show an `out` output; leave it.)
6. Connect a **Panel** to **Status**. It should say
   `Live: CardboardCAD design version N ...`. If it says
   `Waiting for CardboardCAD on port 5001`, start the app (or check the port
   in CardboardCAD **Settings**).

### 2. Add LiveDesign

1. Tab **Params**, group **Util**, drag **Hops** onto the canvas.
2. Right-click it, **Path…**, paste `http://127.0.0.1:5001/live_design`,
   **OK**. It reshapes into **LiveDesign** with the Fold Theory icon.
3. Connect the listener's **Version** to LiveDesign's **V**.
4. Right-click the Hops component and make sure **Cache In Memory** is ticked
   (the default). It then only asks the app again when the version changes.

LiveDesign's outputs:

| Output | What |
|---|---|
| **V** | the design version these outputs show |
| **M** | the 3D model (mesh, mm) |
| **R3** | the ribs or layers in place (outline curves of the assembly) |
| **Cut** | cut outlines, on the XY plane, sheets laid side by side (100 mm apart) |
| **Sc** | score and etch lines on the sheets |
| **Sh** | one rectangle per sheet of board |
| **L** | part labels, sheet by sheet |
| **P** | the parts document: connect it to the `/nest` or `/export` Hops components |
| **S** | a text summary: size, method, sheets, cost |

Now change the design in CardboardCAD (for example ask the assistant "use 6
ribs each way"). Within half a second, Grasshopper updates.

To keep a copy in Rhino, right-click an output (for example **M** or
**Cut**) and choose **Bake…**. Baked geometry does not change afterwards.

Turn live updates off by connecting a **Boolean Toggle** set to False to the
listener's **On** input.

### 3. Send a Rhino model to CardboardCAD (optional)

1. Another Hops component with path `http://127.0.0.1:5001/send_to_cardboardcad`.
2. Connect a closed mesh to **M** (for a Brep or a Rhino object, use
   **Params > Geometry > Brep**, right-click **Set one Brep**, then
   **Mesh > Util > Mesh Brep** in front of **M**). Model in millimetres.
3. Type a name into **N** with a **Panel**, for example `Stand v2`.
4. Connect a **Button** (Params > Input > Button) to **S**. Click it to send.
   The app's preview shows the mesh, and the assistant can work on it.

Sending replaces the app's model and clears its parts. Do **not** connect
LiveDesign's **M** into SendToCardboardCAD's **M**: that would send the
design back to itself.

## Troubleshooting

| What you see | What to do |
|---|---|
| Status: `Waiting for CardboardCAD on port 5001` | Start CardboardCAD. Wait for the green dot. If you changed the port in Settings, connect a Panel with that number to **Port**. |
| LiveDesign is orange with "Unable to connect" | Same as above; and check the Hops path is `http://127.0.0.1:5001/live_design` (not `localhost`). |
| Grasshopper does not update | Is **On** True (or unconnected)? Does Status show a version number that changes when you change the design? If the number changes but LiveDesign does not, check the wire from **Version** to **V**. |
| The listener stopped after editing the script | Recompute: Grasshopper menu **Solution > Recompute** (F5). |
| `M` is empty, Summary says "No design" | The app has no model yet. Make one in the Design Assistant, or open a project (**File > Open Project…**). |

## For developers

- `GET /api/design/version` → `{"version": N}`.
- `GET /api/design/live?since=N` → `{"version": N, "changed": false}` when
  nothing changed, otherwise the whole design as plain JSON (model vertices
  and faces, parts in 3D, sheets with cut/score/etch polylines, the parts
  document). Usable from any script that cannot run Hops.
- The listener's polling logic (`poll_version`, `VersionWatcher`) is tested
  in `engine/tests/test_live_sync.py` without Rhino.
