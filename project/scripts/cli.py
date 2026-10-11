"""The `x5agent` command (installed on PATH via pip install -e .).

Starts the console directly: no update check, no prompts, no install steps.
Restart semantics: a running server is stopped (graceful, then forced)
before the fresh one binds.
Usage from anywhere:  x5agent [--public] [--port 7777]
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


def _stop_server(port: int = DEFAULT_PORT, wait_sec: int = 15) -> bool:
    """Stop our console server so a fresh one can bind (restart semantics).

    SIGINT first (the server's clean KeyboardInterrupt shutdown path),
    SIGKILL fallback, then verify the port is actually free. Only processes
    whose command line is ours are ever signalled; a foreign port holder is
    reported, never killed. Posix only (needs pkill/pgrep).
    Returns True when the port is free.
    """
    import platform as _plat
    import shutil as _sh
    import subprocess as _sp
    import time as _time

    def _free() -> bool:
        try:
            return not _port_answers(port)
        except Exception:
            return False

    def _ours() -> bool:
        try:
            proc = _sp.run(["pgrep", "-f", "web.server.server"],
                           capture_output=True, text=True, timeout=15)
            return proc.returncode == 0 and bool((proc.stdout or "").strip())
        except Exception:
            return False

    if _plat.system() == "Windows" or _sh.which("pkill") is None or _sh.which("pgrep") is None:
        print("  automatic restart needs Linux/Mac with pkill+pgrep - "
              "stop the old server manually and re-run")
        return False
    if _free():
        return True
    if not _ours():
        print(f"  port {port} is busy but no X5Agent server found - "
              f"not touching it (free {port} manually and re-run)")
        return False
    for sig, pause in (("-INT", wait_sec), ("-9", 5)):
        try:
            _sp.run(["pkill", sig, "-f", "web.server.server"],
                    capture_output=True, timeout=15)
        except Exception:
            pass
        print(f"  stopping old server ({'graceful' if sig == '-INT' else 'forced'}) ...")
        deadline = _time.time() + pause
        while _time.time() < deadline:
            if _free():
                print("  old server stopped - port is free")
                return True
            _time.sleep(1)
    print(f"  port {port} is still busy - free it manually and re-run")
    return False


def main(argv=None) -> int:
    """Serve now on 0.0.0.0, restarting a running server first."""
    import argparse

    p = argparse.ArgumentParser(
        prog="x5agent",
        description="X5Agent console - serve now (no update check)")
    p.add_argument("--public", action="store_true",
                   help="Also expose via Tailscale Funnel (permanent public https URL)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT,
                   help="Port to bind (default 7777)")
    args = p.parse_args(argv)
    os.chdir(PROJECT_ROOT)
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    if _port_answers(args.port):
        print("  server already running - stopping it for a fresh start ...")
        if not _stop_server(args.port):
            print("  start aborted (see above).", file=sys.stderr)
            return 1
    from web.server.server import run

    return run(host="0.0.0.0", port=args.port, public=args.public)


if __name__ == "__main__":
    raise SystemExit(main())
