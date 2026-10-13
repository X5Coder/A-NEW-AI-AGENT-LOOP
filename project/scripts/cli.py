"""The `x5agent` command (installed on PATH via pip install -e .).

Persistent serve (survives SSH close): no update check, no prompts.
Public link on by default (like install --run); pass --no-public for local only.
Restart semantics: the console port is always freed first (ANY stale holder
is stopped graceful-then-forced, suspended ones resumed first), so exactly
one server ends up running on the same port. The server is launched detached
(new session + log file), so closing the terminal/SSH never stops it.
Stop everything with:  x5agent --off
Usage from anywhere:  x5agent [--no-public] [--port 7777] [--foreground]
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
        return _stop_server_windows(port, wait_sec)
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


def _stop_server_windows(port: int = DEFAULT_PORT, wait_sec: int = 15) -> bool:
    """Windows fallback: find LISTEN holders via netstat, taskkill them."""
    import subprocess as _sp
    import time as _time

    def _free() -> bool:
        try:
            return not _port_answers(port)
        except Exception:
            return False

    if _free():
        return True
    try:
        proc = _sp.run(["netstat", "-ano"], capture_output=True,
                       text=True, timeout=15)
        pids: list = []
        for line in (proc.stdout or "").splitlines():
            if "LISTENING" not in line or f":{port}" not in line:
                continue
            tail = line.strip().split()[-1]
            try:
                pid = int(tail)
            except Exception:
                continue
            if pid > 1 and pid != os.getpid() and pid not in pids:
                pids.append(pid)
        for pid in pids:
            print(f"  freeing port {port} (pid {pid}) ...")
            try:
                _sp.run(["taskkill", "/PID", str(pid), "/F"],
                        capture_output=True, timeout=15)
            except Exception:
                pass
        deadline = _time.time() + wait_sec
        while _time.time() < deadline:
            if _free():
                print("  port is free - exactly one server will run now")
                return True
            _time.sleep(1)
    except Exception:
        pass
    print(f"  port {port} is still busy - free it manually and re-run")
    return False


def _console_log_path() -> Path:
    """Runtime log file (Data per Rule 69), fallback to project dir."""
    try:
        from core.paths import data_root, ensure_x5agent
        ensure_x5agent()
        log = data_root() / "logs" / "console.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        return log
    except Exception:
        return PROJECT_ROOT / "console.log"


def launch_detached(port: int = DEFAULT_PORT, public: bool = True):
    """Start the console detached (survives SSH/terminal close).

    New session + stdio redirected to the log file, so SIGHUP or a closed
    pipe can never kill it. Returns (pid, log_path). Never raises.
    """
    import subprocess as _sp
    import time as _time

    log_path = _console_log_path()
    cmd = [sys.executable, "-m", "web.server.server",
           "--host", "127.0.0.1", "--port", str(int(port))]
    if public:
        cmd.append("--public")
    try:
        log_fh = open(str(log_path), "a", encoding="utf-8")
    except Exception:
        log_path = PROJECT_ROOT / "console.log"
        log_fh = open(str(log_path), "a", encoding="utf-8")
    try:
        proc = _sp.Popen(cmd, cwd=str(PROJECT_ROOT),
                         stdin=_sp.DEVNULL, stdout=log_fh, stderr=_sp.STDOUT,
                         start_new_session=True, close_fds=True)
        pid = proc.pid
    finally:
        try:
            log_fh.close()
        except Exception:
            pass
    deadline = _time.time() + 30
    while _time.time() < deadline:
        if _port_answers(port):
            break
        _time.sleep(1)
    return pid, log_path


def main(argv=None) -> int:
    """Serve persistently on loopback, restarting a running server first."""
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
    p.add_argument("--off", "--stop", dest="off", action="store_true",
                   help="Stop all project servers on the port, then exit")
    p.add_argument("--foreground", action="store_true",
                   help="Run in the foreground (debug/systemd); default is detached")
    args = p.parse_args(argv)
    if not isinstance(args.port, int) or not (1 <= args.port <= 65535):
        print(f"[ERROR] Invalid port: {args.port}", file=sys.stderr)
        return 1
    if args.off:
        print(f"  stopping every server on port {args.port} ...")
        if _stop_server(args.port):
            print("  stopped - no project server is running now")
            return 0
        print("  stop failed (see above).", file=sys.stderr)
        return 1
    os.chdir(PROJECT_ROOT)
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    if _port_answers(args.port):
        print(f"  port {args.port} is busy - freeing it so exactly one server runs ...")
        if not _stop_server(args.port):
            print("  start aborted (see above).", file=sys.stderr)
            return 1
    public = bool(args.public or not args.no_public)
    if args.foreground:
        from web.server.server import run

        # Loopback only: remote access goes through the Tailscale funnel
        # (which proxies to 127.0.0.1 and always requires the owner login),
        # never a public bind. Foreground ties the server to this terminal
        # (closing SSH stops it) - for persistent runs omit the flag.
        return run(host="127.0.0.1", port=args.port, public=public)
    # Persistent (default): detached child survives SSH/terminal close.
    # Re-running this same command kills the old server above and starts
    # a fresh one on the same port.
    pid, log_path = launch_detached(port=args.port, public=public)
    if _port_answers(args.port):
        print(f"  console running persistently at http://127.0.0.1:{args.port}/ "
              f"(pid {pid}, log {log_path})")
        print("  safe to close this terminal/SSH now - re-run `x5agent` to "
              "restart, `x5agent --off` to stop")
        return 0
    print(f"  start failed - see log: {log_path}", file=sys.stderr)
    return 1


def off(argv=None) -> int:
    """`x5agent-off` alias: stop all project servers on the port."""
    import argparse

    p = argparse.ArgumentParser(
        prog="x5agent-off",
        description="Stop all X5Agent console servers on the port")
    p.add_argument("--port", type=int, default=DEFAULT_PORT,
                   help="Port to free (default 7777)")
    args = p.parse_args(argv)
    print(f"  stopping every server on port {args.port} ...")
    if _stop_server(args.port):
        print("  stopped - no project server is running now")
        return 0
    print("  stop failed (see above).", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
