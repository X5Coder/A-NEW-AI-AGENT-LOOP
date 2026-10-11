"""Restart semantics: the console port is always freed first (ANY stale holder
is stopped graceful-then-forced), so exactly one server ends up running."""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.cli import _port_answers, _port_pids, _stop_server


def _has_port_tools() -> bool:
    if os.name != "posix":
        return False
    if shutil.which("ss") is not None or shutil.which("lsof") is not None:
        return True
    try:
        return Path("/proc/net/tcp").exists()
    except Exception:
        return False


needs_posix = pytest.mark.skipif(
    not _has_port_tools(),
    reason="needs Linux/Mac with ss/lsof (port-holder lookup)")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _hold_port(port: int, pattern: str | None):
    """Spawn a child holding the port (accepts like a real server); argv
    contains pattern only when given."""
    code = ("import socket, threading, time; s = socket.socket(); "
            "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); "
            f"s.bind(('127.0.0.1', {port})); s.listen(50); "
            "threading.Thread(target=lambda: [s.accept() for _ in iter(int, 1)], "
            "daemon=True).start(); time.sleep(60)")
    args = [sys.executable, "-c", code]
    if pattern:
        args.append(pattern)
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _wait_answers(port: int, timeout: float = 10.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if _port_answers(port):
            return True
        time.sleep(0.3)
    return False


@needs_posix
def test_stop_ours_frees_port():
    port = _free_port()
    proc = _hold_port(port, "web.server.server-restart-probe")
    assert _wait_answers(port)
    try:
        assert _stop_server(port, wait_sec=10) is True
        assert _port_answers(port) is False
    finally:
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


@needs_posix
def test_stop_any_holder_frees_port():
    """ANY port holder is stopped (stale server or squatter): the fresh
    server always finds a free port, so exactly one ends up running."""
    port = _free_port()
    proc = _hold_port(port, None)
    assert _wait_answers(port)
    try:
        assert _stop_server(port, wait_sec=10) is True
        assert _port_answers(port) is False
        end = time.time() + 10
        while proc.poll() is None and time.time() < end:
            time.sleep(0.3)
        assert proc.poll() is not None  # holder reaped, port free
    finally:
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


@needs_posix
def test_port_pids_finds_holder():
    """Holder lookup reports the squatter PID (and never our own PID)."""
    port = _free_port()
    proc = _hold_port(port, None)
    assert _wait_answers(port)
    try:
        pids = _port_pids(port)
        assert proc.pid in pids
        assert os.getpid() not in pids
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
