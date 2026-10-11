"""The `x5agent` command (installed on PATH via pip install -e .).

Starts the console directly: no update check, no prompts, no install steps.
Public link on by default (like install --run); pass --no-public for local only.
Restart semantics: the console port is always freed first (ANY stale holder
is stopped graceful-then-forced, suspended ones resumed first), so exactly
one server ends up running.
Usage from anywhere:  x5agent [--no-public] [--port 7777]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORT = 7777


def _port_answers(port: int) -> bool:
    """True when something already listens on 127.0.0.1:port."""
    try:
        import socket as _socket

        probe = _socket.create_connection(("127.0.0.1", port), timeout=2.0)
        try:
            probe.close()
        except Exception:
            pass
        return True
    except Exception:
        return False


def _port_pids(port: int) -> list:
    """PIDs currently listening on port (loopback or all interfaces).

    Lookup order: `ss -tlnp` (Linux) -> `lsof` (Mac) -> /proc scan
    (Linux stdlib-only fallback). Never raises, never empty-raises, and
    never includes our own PID or PID 1 (both are never signalled).
    """
    found: list = []

    def _add(pid) -> None:
        try:
            pid = int(pid)
        except Exception:
            return
        if pid <= 1 or pid == os.getpid():
            return
        if pid not in found:
            found.append(pid)

    try:
        import re as _re
        import shutil as _sh
        import subprocess as _sp
        if _sh.which("ss") is not None:
            proc = _sp.run(["ss", "-tlnp"], capture_output=True,
                           text=True, timeout=15)
            for line in (proc.stdout or "").splitlines():
                if f":{port}" not in line:
                    continue
                for m in _re.finditer(r"pid=(\d+)", line):
                    _add(m.group(1))
            if found:
                return found
    except Exception:
        pass
    try:
        import shutil as _sh2
        import subprocess as _sp2
        if _sh2.which("lsof") is not None:
            proc = _sp2.run(["lsof", "-ti", f":{port}", "-sTCP:LISTEN"],
                            capture_output=True, text=True, timeout=15)
            for token in (proc.stdout or "").replace(",", " ").split():
                _add(token)
            if found:
                return found
    except Exception:
        pass
    try:
        # /proc fallback: LISTEN sockets for this port -> inode -> owner PID.
        want = f"{int(port):04X}"
        inodes: set = set()
        for table in ("/proc/net/tcp", "/proc/net/tcp6"):
            try:
                with open(table, "r", encoding="utf-8") as fh:
                    lines = fh.read().splitlines()[1:]
            except Exception:
                continue
            for line in lines:
                parts = line.split()
                if len(parts) < 10:
                    continue
                try:
                    local, state, inode = parts[1], parts[3], parts[9]
                    if state != "0A" or not local.upper().endswith(f":{want}"):
                        continue
                    inodes.add(str(int(inode)))
                except Exception:
                    continue
        if inodes:
            for pid_dir in Path("/proc").iterdir():
                if not pid_dir.name.isdigit():
                    continue
                try:
                    if int(pid_dir.name) == os.getpid():
                        continue
                    fds = pid_dir / "fd"
                    if not fds.is_dir():
                        continue
                    for fd in fds.iterdir():
                        try:
                            target = os.readlink(fd)
                        except Exception:
                            continue
                        if target.startswith("socket:[") and target[8:-1] in inodes:
                            _add(pid_dir.name)
                            break
                except Exception:
                    continue
    except Exception:
        pass
    return found


def _stop_server(port: int = DEFAULT_PORT, wait_sec: int = 15) -> bool:
    """Free the console port so exactly one server ends up running.

    ANY process holding the port is stopped (old/suspended/stale server or
    anything else squatting our documented console port): SIGCONT first
    (a suspended T-state process, e.g. after Ctrl+Z, cannot receive TERM
    until resumed), then SIGINT (the server's clean shutdown path), then
    SIGKILL. The port is verified free afterwards. PID 1 and our own PID
    are never signalled. Posix only.
    Returns True when the port is free.
    """
    import platform as _plat
    import shutil as _sh
    import signal as _sig
    import subprocess as _sp
    import time as _time

    def _free() -> bool:
        try:
            return not _port_answers(port)
        except Exception:
            return False

    if _plat.system() == "Windows":
        print("  automatic restart needs Linux/Mac - "
              "stop the old server manually and re-run")
        return False
    if _free():
        return True
    if (_sh.which("ss") is None and _sh.which("lsof") is None
            and not Path("/proc/net/tcp").exists()):
        print("  cannot list port holders (no ss/lsof//proc) - "
              f"free {port} manually and re-run")
        return False
    for sig, label, pause in ((_sig.SIGINT, "graceful", wait_sec),
                              (_sig.SIGKILL, "forced", 5)):
        holders = _port_pids(port)
        if not holders and not _free():
            # Port answers but no holder visible (permissions): fall back to
            # stopping our own servers by name rather than leaving two running.
            try:
                _sp.run(["pkill", "-INT" if sig == _sig.SIGINT else "-9",
                         "-f", "web.server.server"],
                        capture_output=True, timeout=15)
            except Exception:
                pass
        for pid in holders:
            for wake in (_sig.SIGCONT, sig):
                try:
                    os.kill(pid, wake)
                except Exception:
                    pass
        print(f"  freeing port {port} ({label}, pid(s) "
              f"{', '.join(map(str, holders)) or 'name-sweep'}) ...")
        deadline = _time.time() + pause
        while _time.time() < deadline:
            if _free():
                print("  port is free - exactly one server will run now")
                return True
            _time.sleep(1)
    print(f"  port {port} is still busy - free it manually and re-run")
    return False


def main(argv=None) -> int:
    """Serve now on loopback, restarting a running server first."""
    import argparse

    p = argparse.ArgumentParser(
        prog="x5agent",
        description="X5Agent console - serve now (no update check)")
    p.add_argument("--public", action="store_true",
                   help="Also expose via Tailscale Funnel (permanent public https URL)")
    p.add_argument("--no-public", action="store_true",
                   help="Local console only (skip the funnel)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT,
                   help="Port to bind (default 7777)")
    args = p.parse_args(argv)
    os.chdir(PROJECT_ROOT)
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    if _port_answers(args.port):
        print(f"  port {args.port} is busy - freeing it so exactly one server runs ...")
        if not _stop_server(args.port):
            print("  start aborted (see above).", file=sys.stderr)
            return 1
    from web.server.server import run

    # Loopback only: remote access goes through the Tailscale funnel
    # (which proxies to 127.0.0.1 and always requires the owner login),
    # never a public bind. Public link on unless explicitly disabled.
    return run(host="127.0.0.1", port=args.port,
               public=(args.public or not args.no_public))


if __name__ == "__main__":
    raise SystemExit(main())
