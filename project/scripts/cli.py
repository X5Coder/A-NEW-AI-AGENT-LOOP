"""The `x5agent` command (installed on PATH via pip install -e .).

Starts the console directly: no update check, no prompts, no install steps.
Usage from anywhere:  x5agent [--public] [--port 7777]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORT = 7777


def main(argv=None) -> int:
    """Serve the console now on 0.0.0.0 (reachable via VPS public IP)."""
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
    from web.server.server import run

    return run(host="0.0.0.0", port=args.port, public=args.public)


if __name__ == "__main__":
    raise SystemExit(main())
