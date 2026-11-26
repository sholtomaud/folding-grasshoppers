"""Start the engine with a given interpreter the way the app does, check it, stop it.

    <python> -I -B -u scripts/smoke_engine.py <interpreter> [--port N]

Uses only the stdlib. Exits non-zero on any failure.
"""

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    interpreter = os.path.abspath(sys.argv[1])
    port = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[2] == "--port" else free_port()
    tmp = tempfile.mkdtemp(prefix="engine-smoke-")
    env = {  # explicit, like EngineManager: nothing inherited from the shell
        "PATH": "/usr/bin:/bin",
        "HOME": tmp,
        "TMPDIR": tmp,
        "ENGINE_SHUTDOWN_TOKEN": "smoke",
        "ENGINE_DATA_DIR": os.path.join(tmp, "data"),
        "ENGINE_CACHE_DIR": os.path.join(tmp, "cache"),
    }
    cmd = [interpreter, "-I", "-B", "-u", "-m", "cardboard_engine.server",
           "--port", str(port), "--parent-pid", str(os.getpid())]
    t0 = time.monotonic()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, cwd=tmp, text=True)
    try:
        ready = None
        while time.monotonic() - t0 < 20:
            line = proc.stdout.readline()
            if not line:
                break
            if line.startswith("ENGINE_READY "):
                ready = json.loads(line.split(" ", 1)[1])
                break
        if ready is None:
            print("FAIL: no ENGINE_READY; stderr:\n" + proc.stderr.read(), file=sys.stderr)
            return 1
        print(f"ENGINE_READY after {time.monotonic() - t0:.2f}s: {ready}")

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as r:
            body = json.loads(r.read())
        assert body == {"status": "healthy"}, body
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/version", timeout=3) as r:
            print("version:", json.loads(r.read()))
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/cut_estimate", method="POST",
            data=json.dumps({"cut_length": 1200}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as r:
            est = json.loads(r.read())
        assert abs(est["time_min"] - 1.15) < 1e-9, est
        print("cut_estimate ok:", est)

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/hello", timeout=3) as r:
            meta = json.loads(r.read())
        assert meta.get("Icon"), "Hops /hello has no icon (assets missing from the bundle?)"
        print("hops /hello metadata ok (icon present)")

        proc.send_signal(signal.SIGTERM)
        rc = proc.wait(timeout=5)
        if rc != 0:
            print(f"FAIL: exit code {rc} after SIGTERM", file=sys.stderr)
            return 1
        print("SIGTERM -> exit 0")
        return 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


if __name__ == "__main__":
    sys.exit(main())
