"""Container start: refresh source from GitHub, seed volumes, start console.

Flow (exactly, nothing else):
  1. `git pull --ff-only` — source code only. Named volumes (Data, models)
     are never touched, so no local data can be lost by an update.
     Offline or no .git: warn and continue with the baked code.
  2. Seed the models volume when empty (tracked configs from the image;
     the 1.1GB weights download once via the embedding bootstrap, then persist).
  3. Ensure X5Agent dirs exist inside the data volume (idempotent).
  4. exec() the console server on 0.0.0.0:5173 (port mapping needs a
     non-loopback bind; the funnel public link stays opt-in via X5_PUBLIC=1).

Stdlib only. Never raises past step 1: the console always boots.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

APP_ROOT = "/app"
PROJECT_ROOT = "/app/project"
MODELS_DIR = "/app/project/embedding_model"
SEED_DIR = "/app/.seed-embedding"
CONFIG_MARKER = "config.json"
PULL_TIMEOUT = 300


def _log(msg: str) -> None:
    print(f"[entrypoint] {msg}", flush=True)


def refresh_source() -> None:
    """Newest code from GitHub, source only. Warn-and-continue on any failure."""
    if not os.path.isdir(os.path.join(APP_ROOT, ".git")):
        _log("no .git — running with baked code (no update check)")
        return
    if shutil.which("git") is None:
        _log("git not found — running with baked code")
        return
    try:
        proc = subprocess.run(
            ["git", "pull", "--ff-only"], cwd=APP_ROOT,
            capture_output=True, text=True, timeout=PULL_TIMEOUT,
        )
        out = (proc.stdout + proc.stderr).strip()
        for line in out.splitlines()[:10]:
            _log(f"git: {line[:160]}")
        if proc.returncode == 0:
            _log("source is now latest (volumes untouched)")
        else:
            _log("git pull did not fast-forward — running with baked code")
    except Exception as e:
        _log(f"update skipped ({str(e)[:120]}) — running with baked code")


def seed_models_volume() -> None:
    """Copy tracked embedding configs into an empty models volume (once)."""
    try:
        marker = os.path.join(MODELS_DIR, CONFIG_MARKER)
        if os.path.isfile(marker):
            return  # volume already seeded (weights persist here too)
        if not os.path.isdir(SEED_DIR):
            _log("no seed configs in image — bootstrap will fetch on first run")
            return
        shutil.copytree(SEED_DIR, MODELS_DIR, dirs_exist_ok=True)
        _log("models volume seeded (weights download once on first run)")
    except Exception as e:
        _log(f"models seed skipped ({str(e)[:120]})")


def ensure_dirs() -> None:
    """Create X5Agent dirs inside the data volume (idempotent)."""
    try:
        sys.path.insert(0, PROJECT_ROOT)
        from core.paths import ensure_x5agent

        ensure_x5agent()
        _log("X5Agent dirs ready (data volume)")
    except Exception as e:
        _log(f"dir ensure skipped ({str(e)[:120]})")


def main() -> None:
    _log("starting (volumes: x5-data -> /app/X5Agent/Data, "
         "x5-models -> /app/project/embedding_model)")
    refresh_source()
    seed_models_volume()
    ensure_dirs()
    os.chdir(PROJECT_ROOT)
    cmd = [sys.executable, "-m", "web.server.server",
           "--host", "0.0.0.0", "--port", "5173"]
    if os.environ.get("X5_PUBLIC") == "1":
        cmd.append("--public")
    _log("console: http://localhost:5173/ (exec server, signals pass through)")
    os.execv(sys.executable, cmd)


if __name__ == "__main__":
    main()
