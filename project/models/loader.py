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


def _project_models_root() -> Path:
    # Heavy weights live in project/models/<type>/<name> (first download lands here).
    return _project_root() / "models"


def _legacy_candidates(model_type: str, dirname: str) -> list[Path]:
    # Old layout project/models/<type>/<dirname> (+ flat variant). Read-only fallback.
    root = _project_root() / "models"
    cands = [root / model_type / dirname, root / dirname]
    # Historical nested names (embedding wrapper dir, etc.)
    try:
        if root.exists():
            for child in root.iterdir():
                if child.is_dir():
                    cands.append(child / dirname)
    except Exception:
        pass
    return cands


def _is_complete(path: Path) -> bool:
    try:
        return path.exists() and (path / MARKER).exists() and any(path.iterdir())
    except Exception:
        return False


def get_model_path(model_id: str) -> Path | None:
    """
    Return local path for model_id.
    Primary: manifest local_dir (project/models/<type>/<name>).
    Fallback: sibling copies under project/models (legacy X5Agent/Models layout).
    Prefers whichever location is actually complete; else returns primary (download target).
    """
    manifest = _manifest_path()
    if not manifest.exists():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return None
    for m in data.get("models", []):
        if m.get("id") == model_id:
            primary = (_project_root() / str(m["local_dir"])).resolve()
            if _is_complete(primary):
                return primary
            try:
                dirname = Path(str(m["local_dir"])).name
                for cand in _legacy_candidates(str(m.get("type", "")), dirname):
                    if _is_complete(cand.resolve()):
                        return cand.resolve()
            except Exception:
                pass
            return primary
    return None


def is_model_ready(model_id: str) -> bool:
    # Ready if the manifest path OR a merged copy is complete - never re-downloads existing weights.
    manifest = _manifest_path()
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            for m in data.get("models", []):
                if m.get("id") == model_id:
                    primary = (_project_root() / str(m["local_dir"])).resolve()
                    if _is_complete(primary):
                        return True
                    try:
                        dirname = Path(str(m["local_dir"])).name
                        for cand in _legacy_candidates(str(m.get("type", "")), dirname):
                            if _is_complete(cand.resolve()):
                                return True
                    except Exception:
                        pass
                    return False
        except Exception:
            pass
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


def _guess_model_type(dirname: str) -> str:
    name = dirname.lower()
    if "embed" in name:
        return "embedding"
    if "whisper" in name or "stt" in name:
        return "stt"
    if "vlm" in name or "vision" in name or "clip" in name:
        return "vision"
    if "tts" in name or "supertonic" in name or "speech" in name:
        return "tts"
    return "custom"


def _local_models_index_path() -> Path:
    return _project_root() / "models" / "local_models" / "models.json"


def scan_local_models() -> list[dict]:
    """Scan local models: index entries + auto-discovered folders.

    Reads models/local_models/models.json, then discovers real weight folders
    under project/models (type dirs, their subdirs, and local_models/).
    Code packages (service.py/loader.py/manifest.json) are never models, and a
    folder matching an indexed entry (same path, folder name, or model id)
    merges into it instead of duplicating. Add/remove a folder under
    project/models/local_models/ and it appears/disappears on the next scan.
    Each item: {id,name,description,type,local_dir,used_by,plugins,status}.
    status: ready (marker + files) | found (dir exists) | missing.
    Runs on every call - cheap (one small JSON + directory listing).
    """
    import os

    root = _project_root()
    entries: dict[str, dict] = {}

    try:
        idx_path = _local_models_index_path()
        if idx_path.exists():
            data = json.loads(idx_path.read_text(encoding="utf-8"))
            items = data.get("models", []) if isinstance(data, dict) else []
            for m in items:
                if isinstance(m, dict) and m.get("id") and m.get("local_dir"):
                    entries[str(m["local_dir"])] = {
                        "id": str(m["id"]),
                        "name": str(m.get("name") or m["id"]),
                        "description": str(m.get("description") or ""),
                        "type": str(m.get("type") or "custom"),
                        "local_dir": str(m["local_dir"]),
                        "used_by": str(m.get("used_by") or ""),
                        "plugins": [p for p in (m.get("plugins") or []) if isinstance(p, str)],
                        "paths": [str(m["local_dir"])],
                    }
    except Exception:
        pass

    def _resolve(rel: str) -> Path | None:
        try:
            return (root / rel).resolve()
        except Exception:
            return None

    by_resolved: dict[Path, str] = {}
    by_base: dict[str, str] = {}
    by_id: dict[str, str] = {}
    for rel in entries:
        rp = _resolve(rel)
        if rp is not None:
            by_resolved.setdefault(rp, rel)
        by_base.setdefault(Path(rel).name.lower(), rel)
        by_id.setdefault(str(entries[rel].get("id", "")).lower(), rel)

    def _rel_of(p: Path) -> str | None:
        try:
            return Path(os.path.relpath(p, root)).as_posix()
        except Exception:
            return None

    def _has_real_files(d: Path) -> bool:
        try:
            return any(p.is_file() and p.name != ".gitkeep" for p in d.iterdir())
        except Exception:
            return False

    def _is_code_dir(d: Path) -> bool:
        # Service/loader code packages are never model weights.
        try:
            names = {p.name for p in d.iterdir() if p.is_file()}
        except Exception:
            return False
        return bool(names & {"service.py", "loader.py", "manifest.json"})

    candidates: list[Path] = []
    try:
        mroot = _project_models_root()
        if mroot.exists():
            for child in mroot.iterdir():
                if not child.is_dir() or child.name == "__pycache__":
                    continue
                # local_models/ holds user-dropped folders; type dirs hold
                # weights one level deeper (e.g. models/embedding/<name>).
                if child.name == "local_models":
                    try:
                        for sub in child.iterdir():
                            if sub.is_dir() and sub.name != "__pycache__":
                                candidates.append(sub)
                    except Exception:
                        pass
                    continue
                candidates.append(child)
                try:
                    for sub in child.iterdir():
                        if sub.is_dir() and sub.name != "__pycache__" and not _is_code_dir(sub):
                            candidates.append(sub)
                except Exception:
                    pass
    except Exception:
        pass

    for cand in candidates:
        try:
            if _is_code_dir(cand):
                continue
            rp = cand.resolve()
            rel = _rel_of(cand)
            if rel is None:
                continue
            target = by_resolved.get(rp) or (entries.get(rel) and rel)
            if target is None:
                # Same folder name (legacy copy) or same model id (renamed folder).
                target = by_base.get(cand.name.lower()) or by_id.get(cand.name.lower())
            if target is not None:
                # Same weights as an indexed entry (primary or legacy copy):
                # merge instead of duplicating.
                if rel not in entries[target]["paths"]:
                    entries[target]["paths"].append(rel)
                by_resolved.setdefault(rp, target)
                continue
            # Skip dirs holding only .gitkeep
            if not _has_real_files(cand):
                subs = []
                try:
                    subs = [s for s in cand.iterdir() if s.is_dir() and s.name != "__pycache__"]
                except Exception:
                    pass
                if len(subs) == 1 and _has_real_files(subs[0]) and not _is_code_dir(subs[0]):
                    cand = subs[0]
                    rp = cand.resolve()
                    rel = _rel_of(cand)
                    if rel is None:
                        continue
                    target = by_resolved.get(rp) or by_base.get(cand.name.lower()) or by_id.get(cand.name.lower())
                    if target is not None:
                        if rel not in entries[target]["paths"]:
                            entries[target]["paths"].append(rel)
                        by_resolved.setdefault(rp, target)
                        continue
                else:
                    continue
            entries[rel] = {
                "id": cand.name.lower().replace(" ", "_").replace("-", "_")[:64],
                "name": cand.name.replace("-", " ").replace("_", " ").title(),
                "description": "Custom local model (auto-discovered)",
                "type": _guess_model_type(cand.name),
                "local_dir": rel,
                "used_by": "",
                "plugins": [],
                "paths": [rel],
            }
            by_resolved.setdefault(rp, rel)
            by_base.setdefault(cand.name.lower(), rel)
        except Exception:
            continue

    out: list[dict] = []
    for m in entries.values():
        status = "missing"
        try:
            for rel in m.get("paths", [m["local_dir"]]):
                p = root / rel
                if p.exists() and _has_real_files(p):
                    if (p / MARKER).exists():
                        status = "ready"
                        break
                    status = "found"
        except Exception:
            pass
        m["status"] = status
        out.append({k: v for k, v in m.items() if k != "paths"})
    out.sort(key=lambda x: x.get("id", ""))
    return out


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
