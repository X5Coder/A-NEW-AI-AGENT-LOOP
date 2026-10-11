"""The `x5agent` command (installed on PATH via pip install -e .).

Starts the console directly: no update check, no prompts, no install steps.
When the server is already running, prints the 3-line report instead:
  Web Server (Local):  ...
  Web Server (Public): ...
  Server Running Now
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


def _show_report(port: int) -> None:
    """The final 3-line report for an already-running server. Never raises."""
    try:
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        try:
            from web.server.tailscale_share import status

            snap = status(port)
        except Exception:
            snap = {}
        url = snap.get("public_url") if isinstance(snap, dict) else None
        if not url:
            if not (snap or {}).get("installed", True):
                url = "off (no Tailscale)"
            elif not (snap or {}).get("logged_in", False):
                url = "off (not logged in)"
            else:
                url = "off (funnel not enabled - restart with --public)"
        from web.server.server import _print_final_report

        _print_final_report(port, url)
    except Exception as e:
        print(f"[x5agent] running at http://127.0.0.1:{port}/ ({e})")


def main(argv=None) -> int:
    """Serve now on 0.0.0.0, or report when already running."""
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
        _show_report(args.port)
        return 0
    from web.server.server import run

    return run(host="0.0.0.0", port=args.port, public=args.public)


if __name__ == "__main__":
    raise SystemExit(main())
