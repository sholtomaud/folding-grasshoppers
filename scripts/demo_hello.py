"""Hello-world demo against a running engine, speaking the same protocol as Hops.

    # 1. start an engine (any of these)
    open "/Applications/Fold Theory.app"                                    # the app starts it on :5001
    cd engine && uv run python -m cardboard_engine.server     # dev, no app needed

    # 2. run the demo (stdlib only)
    python3 scripts/demo_hello.py [--port 5001] [--name Grasshopper]

It does exactly what a Grasshopper Hops component does: GET the component URL
for its inputs/outputs, then POST /solve with a data tree. It also calls the
plain JSON route the Swift app uses.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request


def call(url, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5001)
    ap.add_argument("--name", default="Grasshopper")
    args = ap.parse_args()
    base = f"http://127.0.0.1:{args.port}"

    try:
        print("health:", call(f"{base}/health"))
    except (urllib.error.URLError, OSError) as exc:
        print(f"No engine on {base} ({exc}). Start the app or the dev server first.", file=sys.stderr)
        return 1

    meta = call(f"{base}/hello")
    print(f"\nHops component at {base}/hello")
    print(f"  {meta['Category']} > {meta['Subcategory']} > {meta['Name']}"
          f" (icon: {'yes' if meta.get('Icon') else 'no'})")
    for p in meta["Inputs"]:
        print(f"  in : {p['Name']} ({p['Nickname']}, {p['ParamType']})")
    for p in meta["Outputs"]:
        print(f"  out: {p['Name']} ({p['Nickname']}, {p['ParamType']})")

    solve = call(f"{base}/solve", {
        "pointer": "/hello",
        "values": [{
            "ParamName": "Name",
            "InnerTree": {"{0}": [{"type": "System.String", "data": json.dumps(args.name)}]},
        }],
    })
    message = json.loads(solve["values"][0]["InnerTree"]["0"][0]["data"])
    print(f"\nHops solve  -> {message}")
    print(f"App route   -> {call(f'{base}/api/hello', {'name': args.name})['message']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
