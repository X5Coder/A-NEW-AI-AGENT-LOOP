"""X5Agent single install/update command.

Flow (exactly, nothing else):
  1. python install.py
  2. environment check (python, CPU/GPU, disk, dependencies)
  3. project state check (missing / complete / has live data)
  4. source-only update (git pull --ff-only: tracked code only,
     NEVER touches X5Agent/Data, embedding_model/, or any user data)
  5. bundled embedding check (offline) + tool/trigger embeddings ready
  6. email + password prompt (stored as salted hash only, never plaintext)
  7. prints the console URL only

Run from the project/ directory:  python install.py
Start the console after:          python -m web.server.server
"""
from __future__ import annotations

import getpass
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
GIT_ROOT = next((p for p in (PROJECT_ROOT, PROJECT_ROOT.parents[0])
                 if (p / ".git").exists()), None)
REPO_URL = "https://github.com/X5Coder/A-NEW-AI-AGENT-LOOP"
UI_URL = "http://127.0.0.1:5173/"
MIN_PYTHON = (3, 10)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _step(title: str) -> None:
    print(f"\n[install] {title}")


def _ok(msg: str) -> None:
    print(f"  OK: {msg}")


def _warn(msg: str) -> None:
    print(f"  WARN: {msg}")


def _fail(msg: str) -> int:
    print(f"  ERROR: {msg}", file=sys.stderr)
    return 1


def check_environment() -> bool:
    """Python version, CPU/GPU (once), disk, required packages. Returns True when ready."""
    _step("1/5 environment check")
    if sys.version_info < MIN_PYTHON:
        print(f"  ERROR: python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ required, got {sys.version.split()[0]}",
              file=sys.stderr)
        return False
    _ok(f"python {sys.version.split()[0]}")
    device = "cpu"
    try:
        import torch  # type: ignore

        device = "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        device = "cpu (torch not installed)"
    print(f"  device: {device} (GPU used automatically when available)")
    from core.embedding.bootstrap import detect_device  # noqa: E402

    print(f"  embedding device: {detect_device()} (detected once, cached)")
    missing = []
    for pkg in ("sentence_transformers", "numpy"):
        try:
            __import__(pkg)
        except Exception:
            missing.append(pkg)
    if missing:
        print(f"  ERROR: missing packages: {', '.join(missing)}", file=sys.stderr)
        print("  run: pip install -e .  (from project/) then re-run install", file=sys.stderr)
        return False
    _ok("dependencies present")
    try:
        free_gb = shutil.disk_usage(PROJECT_ROOT).free / (1024 ** 3)
        print(f"  disk free: {free_gb:.1f} GB")
    except Exception:
        pass
    return True


def _local_version() -> str:
    try:
        for line in (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("version"):
                return line.split("=", 1)[1].strip().strip("\"'")
    except Exception:
        pass
    return "unknown"


def _latest_version() -> str | None:
    """Latest GitHub release tag (best-effort, 10s timeout, never raises)."""
    try:
        import urllib.request

        req = urllib.request.Request(
            "https://api.github.com/repos/X5Coder/A-NEW-AI-AGENT-LOOP/releases/latest",
            headers={"User-Agent": "x5agent-installer", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            tag = str(json.loads(r.read().decode("utf-8")).get("tag_name") or "").strip()
        return tag.lstrip("v") or None
    except Exception:
        return None


def check_project_state() -> dict:
    """Missing / complete / has live data. Never modifies anything."""
    _step("2/5 project state check")
    sys.path.insert(0, str(PROJECT_ROOT))
    from core.embedding.bootstrap import is_complete as _emb_ok

    embedding = bool(_emb_ok())
    data_dir = PROJECT_ROOT.parents[0] / "X5Agent" / "Data"
    conv = data_dir / "database" / "conversation.json"
    has_data = False
    try:
        if conv.exists() and len(json.loads(conv.read_text(encoding="utf-8"))) > 0:
            has_data = True
    except Exception:
        has_data = conv.exists()
    git = shutil.which("git") is not None and GIT_ROOT is not None
    local = _local_version()
    print(f"  source version: v{local}")
    print(f"  embedding model: {'present' if embedding else 'MISSING'}")
    print(f"  existing user data: {'yes (will be preserved)' if has_data else 'no (fresh)'}")
    print(f"  git updates: {'available' if git else 'unavailable (not a git checkout)'}")
    latest = _latest_version()
    if latest:
        print(f"  latest release: v{latest} "
              f"({'up to date' if latest == local else 'update available'})")
    else:
        _warn("could not reach GitHub for version check (offline?)")
    return {"embedding": embedding, "has_data": has_data, "git": git, "local": local}


def update_source() -> None:
    """Source-only update: tracked code via git fast-forward. Never deletes data."""
    _step("3/5 source update (code only, data untouched)")
    if not (shutil.which("git") and GIT_ROOT is not None):
        _warn("no git checkout - skipping update (download the release zip instead)")
        return
    try:
        proc = subprocess.run(
            ["git", "pull", "--ff-only"], cwd=str(GIT_ROOT),
            capture_output=True, text=True, timeout=300,
        )
        out = (proc.stdout + proc.stderr).strip()
        for line in out.splitlines()[:20]:
            print(f"  git: {line[:160]}")
        if proc.returncode == 0:
            _ok("source is now latest (user data untouched: X5Agent/ + embedding_model/ are untracked)")
        else:
            _warn("git pull did not fast-forward (local changes?) - continuing with current code")
    except Exception as e:
        _warn(f"update failed ({str(e)[:120]}) - continuing with current code")


def ensure_resources() -> bool:
    """Bundled embedding (offline) + prebuilt tool/trigger embeddings."""
    _step("4/5 resources (offline, bundled)")
    sys.path.insert(0, str(PROJECT_ROOT))
    from core.embedding.bootstrap import ensure_embedding_model

    try:
        ensure_embedding_model()
    except RuntimeError as e:
        print(f"  {e}")
        try:
            ans = input("  Download the bundled embedding model now? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            return False
        if ans != "y":
            return False
        os.environ["X5_ALLOW_EMBEDDING_DOWNLOAD"] = "1"
        try:
            ensure_embedding_model()
        except Exception as e2:
            print(f"  ERROR: download failed: {e2}", file=sys.stderr)
            return False
    _ok("embedding model ready")
    try:
        import importlib.util

        def _load(rel, name):
            spec = importlib.util.spec_from_file_location(name, str(PROJECT_ROOT / rel))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore
            return mod

        tools = _load("plugins/tool-discovery/search/store.py", "inst_tools").ensure_embeddings()
        trigs = _load("plugins/trigger-discovery/search/store.py", "inst_trigs").ensure_embeddings()
        _ok(f"tool embeddings ready ({len(tools)} tools, {len(trigs)} triggers)")
    except Exception as e:
        _warn(f"embedding warmup skipped ({str(e)[:120]})")
    return True


def ask_credentials() -> bool:
    """Email + hidden password prompt. Stores salted hash only. Returns True on success."""
    _step("5/5 console owner")
    try:
        email = input("  email: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print(file=sys.stderr)
        return False
    if not EMAIL_RE.match(email):
        print("  ERROR: invalid email format", file=sys.stderr)
        return False
    try:
        pw = getpass.getpass("  password (min 8, hidden): ")
    except (EOFError, KeyboardInterrupt):
        print(file=sys.stderr)
        return False
    if len(pw) < 8:
        print("  ERROR: password too short (min 8)", file=sys.stderr)
        return False
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from core.paths import data_db_file

        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), 200_000).hex()
        del pw
        record = {"email": email, "salt": salt, "hash": digest,
                  "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        path = data_db_file("console_auth.json")
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        _ok(f"owner saved ({email}) - hash only, never plaintext")
        return True
    except Exception as e:
        print(f"  ERROR: could not save owner ({str(e)[:120]})", file=sys.stderr)
        return False


def main() -> int:
    print("=" * 70)
    print("  X5Agent install / update (one command)")
    print("=" * 70)
    if not check_environment():
        return 1
    check_project_state()
    update_source()
    if not ensure_resources():
        return 1
    if not ask_credentials():
        return 1
    print("\n" + "=" * 70)
    print(f"  Console: {UI_URL}")
    print("  Start it: python -m web.server.server  (from project/)")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
