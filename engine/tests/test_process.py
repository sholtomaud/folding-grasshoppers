"""End-to-end tests that run the engine as a real child process."""

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

TOKEN = "proc-test-token"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_engine(port, parent_pid=None, token=TOKEN):
    args = [sys.executable, "-I", "-B", "-u", "-m", "cardboard_engine.server", "--port", str(port)]
    if parent_pid is not None:
        args += ["--parent-pid", str(parent_pid)]
    env = {
        "PATH": "/usr/bin:/bin",
        "ENGINE_SHUTDOWN_TOKEN": token,
    }
    return subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True
    )


def wait_ready(proc, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        if line.startswith("ENGINE_READY "):
            return json.loads(line[len("ENGINE_READY "):])
    raise AssertionError(f"engine not ready; rc={proc.poll()} stderr={proc.stderr.read()}")


def get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as r:
        return r.status, json.loads(r.read())


def post(port, path, headers=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", method="POST", data=b"", headers=headers or {}
    )
    try:
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


@pytest.fixture
def engine():
    procs = []

    def _start(*a, **kw):
        p = start_engine(*a, **kw)
        procs.append(p)
        return p

    yield _start
    for p in procs:
        if p.poll() is None:
            p.kill()
        p.wait()
        p.stdout.close()
        p.stderr.close()


def test_ready_line_and_health(engine):
    port = free_port()
    p = engine(port)
    ready = wait_ready(p)
    assert ready["port"] == port
    assert get(port, "/health") == (200, {"status": "healthy"})


def test_binds_loopback_only(engine):
    port = free_port()
    p = engine(port)
    wait_ready(p)
    # Find a non-loopback local address; if none, skip.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # TEST-NET, no packets sent
            addr = s.getsockname()[0]
    except OSError:
        pytest.skip("no non-loopback interface")
    if addr.startswith("127."):
        pytest.skip("no non-loopback interface")
    with socket.socket() as s:
        s.settimeout(1)
        assert s.connect_ex((addr, port)) != 0


def test_port_in_use_exits_98(engine):
    port = free_port()
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("0.0.0.0", port))  # wildcard, like `python3 -m http.server`
    blocker.listen(1)
    try:
        p = engine(port)
        rc = p.wait(timeout=15)
        assert rc == 98
        err_lines = [l for l in p.stderr.read().splitlines() if l.startswith("{")]
        assert json.loads(err_lines[-1]) == {"error": "port_in_use", "port": port}
    finally:
        blocker.close()


def test_sigterm_exits_zero_quickly(engine):
    port = free_port()
    p = engine(port)
    wait_ready(p)
    t0 = time.monotonic()
    p.send_signal(signal.SIGTERM)
    assert p.wait(timeout=5) == 0
    assert time.monotonic() - t0 < 3


def test_shutdown_endpoint(engine):
    port = free_port()
    p = engine(port)
    wait_ready(p)
    assert post(port, "/shutdown", {"X-Engine-Token": "nope"}) == 403
    assert p.poll() is None
    assert post(port, "/shutdown", {"X-Engine-Token": TOKEN}) == 202
    assert p.wait(timeout=5) == 0


def test_watchdog_exits_when_parent_dies(engine):
    fake_parent = subprocess.Popen(["sleep", "60"])
    port = free_port()
    p = engine(port, parent_pid=fake_parent.pid)
    wait_ready(p)
    time.sleep(0.5)
    assert p.poll() is None
    fake_parent.kill()
    fake_parent.wait()  # reap, otherwise the zombie still answers kill(pid, 0)
    assert p.wait(timeout=8) == 0
