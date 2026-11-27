"""Report for scripts/ci/window_probe.sh: the result table (stdout, for the step
summary) and small JPEGs of the screenshots (stderr, base64 between markers, so
they can be read back from the job log). Exits 1 when any check failed.

    python3 scripts/ci/window_probe_report.py build/window-probe
"""
import base64
import glob
import json
import os
import subprocess
import sys

out = sys.argv[1]

# Screenshots first, so the table ends up at the end of the log.
for png in sorted(glob.glob(os.path.join(out, "probe-*.png"))):
    if png.endswith("-cache.png") and os.path.exists(png.replace("-cache.png", ".png")):
        continue
    jpg = png[:-4] + ".jpg"
    subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "50", "-Z", "900", png, "--out", jpg],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.path.exists(jpg):
        sys.stderr.write(f"=====BEGIN {os.path.basename(jpg)}=====\n")
        sys.stderr.write(base64.encodebytes(open(jpg, "rb").read()).decode())
        sys.stderr.write(f"=====END {os.path.basename(jpg)}=====\n")
sys.stderr.flush()

files = sorted(glob.glob(os.path.join(out, "probe-*.json")))
failed = not files
lines = ["### Window probe: chat message box", "",
         "| Window | Content | Message box (y, h, visible h) | Status bar top | Result |",
         "|---|---|---|---|---|"]
trees = []
for f in files:
    d = json.load(open(f))
    if "requested" not in d:
        lines.append(f"| {os.path.basename(f)} | | | | {d} |")
        failed = True
        continue
    r, c, lay = d["requested"], d.get("composer") or {}, d["contentLayoutRect"]
    box = f'{c.get("y", 0):.0f}, {c.get("h", 0):.0f}, {c.get("visibleH", 0):.0f}' if c else "not found"
    res = "ok" if d["ok"] else "FAIL: " + "; ".join(d["problems"])
    lines.append(f'| {r["w"]:.0f} x {r["h"]:.0f} | {lay["w"]:.0f} x {lay["h"]:.0f} | {box} '
                 f'| {d["statusBarTop"]:.0f} | {res} |')
    failed = failed or not d["ok"]
    if not d["ok"]:
        trees.append(f"<details><summary>{os.path.basename(f)} view tree</summary>\n\n```\n"
                     + "\n".join(d.get("tree", [])[:300]) + "\n```\n</details>\n")

quit_file = os.path.join(out, "quit.txt")
quit_result = open(quit_file).read().strip() if os.path.exists(quit_file) else "not run"
failed = failed or quit_result != "ok"
version = subprocess.run(["sw_vers", "-productVersion"], capture_output=True, text=True).stdout.strip()

print("\n".join(trees))
print("\n".join(lines))
print()
print(f"Cmd+Q (menu item, then app and engine gone): {quit_result}")
print(f"macOS {version}")
sys.exit(1 if failed else 0)
