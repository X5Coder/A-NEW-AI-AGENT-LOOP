"""Container start: refresh source from GitHub, seed volumes, start console.

Flow (exactly, nothing else):
  1. `git pull --ff-only` — source code only. Named volumes (Data, models)
     are never touched, so no local data can be lost by an update.
     Offline or no .git: warn and continue with the baked code.
  2. Pin data onto the manager binds (/app/data, /data) via symlinks so
     single-room updates (fresh container layers) never wipe user data.
  3. Seed the models volume when empty (tracked configs from the image;
     the 1.1GB weights download once via the embedding bootstrap, then persist).
  4. Ensure X5Agent dirs exist inside the data volume (idempotent).
  4. Browser engine check: headless chromium smoke test (bounded, warn-only —
     proves the X5Browser engine can run on this headless server).
  5. exec() the console server on 0.0.0.0:7777 (port mapping needs a
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
BROWSER_TIMEOUT = 90
# Same lookup order as the x5browser library's own launcher.
BROWSER_BINARIES = ("google-chrome", "google-chrome-stable", "chromium",
                    "chromium-browser", "chrome")


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


def check_browser_engine() -> None:
    """Headless smoke test: proves a browser engine runs on this server.

    Runs `<binary> --headless --dump-dom about:blank` (bounded, read-only).
    Warn-only: a missing/broken engine never blocks the console — the agent's
    browser tools fail clearly at use time instead.
    """
    binary = next((b for b in BROWSER_BINARIES if shutil.which(b)), None)
    if binary is None:
        _log("browser engine MISSING (no chrome/chromium on PATH) — "
             "browser tools will fail until one is installed")
        return
    try:
        proc = subprocess.run(
            [binary, "--headless=new", "--no-sandbox", "--disable-gpu",
             "--dump-dom", "about:blank"],
            capture_output=True, text=True, timeout=BROWSER_TIMEOUT,
        )
        if proc.returncode == 0 and "<html" in (proc.stdout or "").lower():
            _log(f"browser engine OK ({binary} headless)")
        else:
            _log(f"browser engine FAILED ({binary} exit {proc.returncode}) — "
                 "browser tools will fail until it is fixed")
    except Exception as e:
        _log(f"browser engine check skipped ({str(e)[:120]})")


def _move_into(src_dir: str, store_dir: str) -> int:
    """Move baked entries into the persistent store. Returns moved count."""
    try:
        entries = os.listdir(src_dir)
    except Exception:
        return 0
    moved = 0
    for name in entries:
        src = os.path.join(src_dir, name)
        dst = os.path.join(store_dir, name)
        if os.path.lexists(dst):
            continue
        try:
            shutil.move(src, dst)
            moved += 1
        except Exception:
            pass
    return moved


def _copy_seed_into(store_dir: str) -> None:
    """Copy (never move) the baked seed configs onto the persistent store."""
    try:
        if os.path.isdir(SEED_DIR) and not os.path.islink(SEED_DIR):
            shutil.copytree(SEED_DIR, store_dir, dirs_exist_ok=True)
    except Exception as e:
        _log(f"seed copy skipped ({str(e)[:100]})")


def persist_data() -> None:
    """Pin local data onto the manager's bind mounts so updates never wipe it.

    Single rooms have no named volumes; the manager binds the FRESH container
    dirs `/app/data` and `/data` to the host (survives rebuilds, but only for
    paths BAKED into the image). So persistence works file-by-file:
      store = /app/data/x5store  (all live data: owner, chat, embeddings,
               weights — files written here survive; symlinks do NOT)
    First start (fresh binds): the manager copies the image's /app/data and
    /data content into the NEW binds, so whatever we baked for them persists.
    Every start: seed copies + baked moves land here, and the bootstrap reads
    the store FIRST (env override) before the bundled path.
    """
    primary = "/app/data" if os.path.isdir("/app/data") else None
    if primary is None:
        _log("no manager binds found (/app/data) — data stays ephemeral")
        return
    store = os.path.join(primary, "x5store")
    try:
        os.makedirs(store, exist_ok=True)
    except Exception:
        pass
    if not os.path.isdir(store):
        _log("store unavailable — data stays ephemeral")
        return
    _log(f"persistent store: {store}")
    try:  # claim marker first: even an empty claim routes reads here
        marker = os.path.join(store, ".x5store")
        if not os.path.isfile(marker):
            with open(marker, "w", encoding="utf-8") as f:
                f.write("x5agent persistent store")
    except Exception:
        pass
    _copy_seed_into(store)  # baked configs persist via the fresh-bind copy
    moved = 0
    for baked in (os.path.join(APP_ROOT, "X5Agent", "Data"), MODELS_DIR):
        try:
            if os.path.isdir(baked) and not os.path.islink(baked):
                moved += _move_into(baked, store)
        except Exception:
            pass
    if moved:
        _log(f"moved {moved} baked entries into the store")


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
    persist_data()
    seed_models_volume()
    ensure_dirs()
    check_browser_engine()
    os.chdir(PROJECT_ROOT)
    cmd = [sys.executable, "-m", "web.server.server",
           "--host", "0.0.0.0", "--port", "7777"]
    if os.environ.get("X5_PUBLIC") == "1":
        cmd.append("--public")
    else:
        # Explicit skip note: funnel needs an interactive logged-in tailscale
        # client, which a container does not have — the console stays reachable
        # directly (local port / VPS IP) instead of failing silently.
        _log("public link off (X5_PUBLIC=0) — Tailscale funnel skipped")
    _log("console: http://localhost:7777/ (exec server, signals pass through)")
    os.execv(sys.executable, cmd)


if __name__ == "__main__":
    main()
