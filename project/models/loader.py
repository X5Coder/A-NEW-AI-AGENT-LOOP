"""
Lightweight runtime helper to ensure models are present.

Usage:
    from models.loader import ensure_models, is_model_ready, get_model_path

This module is deliberately small, no heavy imports at top level.
It delegates actual downloading to scripts/setup_models.py to keep single responsibility.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

MARKER = ".download_complete"


def _project_root() -> Path:
    # models/loader.py -> project/
    return Path(__file__).resolve().parents[1]


def _manifest_path() -> Path:
    return _project_root() / "models" / "manifest.json"


def get_model_path(model_id: str) -> Path | None:
    """Return local path for model_id per manifest, or None if not found."""
    manifest = _manifest_path()
    if not manifest.exists():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return None
    for m in data.get("models", []):
        if m.get("id") == model_id:
            return (_project_root() / m["local_dir"]).resolve()
    return None


def is_model_ready(model_id: str) -> bool:
    path = get_model_path(model_id)
    if path is None or not path.exists():
        return False
    return (path / MARKER).exists() and any(path.iterdir())


def are_all_models_ready() -> bool:
    manifest = _manifest_path()
    if not manifest.exists():
        return False
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return False
    for m in data.get("models", []):
        if not is_model_ready(m["id"]):
            return False
    return True


def ensure_models(auto_download: bool = True, timeout_seconds: int = 1800) -> bool:
    """
    Ensure all models are ready. If auto_download True and some missing, trigger setup script.

    Returns True if all ready after call, False otherwise.
    No secrets logged, uses subprocess with explicit path validation.
    """
    if are_all_models_ready():
        return True
    if not auto_download:
        return False

    project_root = _project_root()
    script = project_root / "scripts" / "setup_models.py"
    if not script.exists():
        print(f"[models.loader] setup script not found: {script}", file=sys.stderr)
        return False

    print("[models.loader] models missing, running auto-download (first run)...")
    try:
        # Use same python interpreter, bounded timeout
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(project_root),
            timeout=timeout_seconds,
            capture_output=False,
        )
        return result.returncode == 0 and are_all_models_ready()
    except subprocess.TimeoutExpired:
        print("[models.loader] auto-download timed out", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[models.loader] auto-download failed: {e}", file=sys.stderr)
        return False
