"""
workspace tool - list and create folders JAILED inside X5Agent/Workspace only.
One level per call: folders report item counts, the agent enters with list.
"""
from __future__ import annotations

import time
from pathlib import Path

from .events import format_event

MAX_ENTRIES = 200
MAX_COUNT = 1001
MAX_NAME_LEN = 100
MAX_PATH_LEN = 500


def _workspace_root() -> Path:
    try:
        import sys as _sys
        proj = Path(__file__).resolve().parents[3]
        if str(proj) not in _sys.path:
            _sys.path.insert(0, str(proj))
        from core.paths import workspace_root as _ws
        ws = _ws()
        try:
            ws.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return ws.resolve()
    except Exception:
        repo = Path(__file__).resolve().parents[4]
        ws = repo / "X5Agent" / "Workspace"
        try:
            ws.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return ws.resolve()


def _resolve_dir(path: str | None) -> tuple[Path, Path, str]:
    """Resolve a workspace-relative dir. Returns (ws, target, rel_display)."""
    ws = _workspace_root()
    if path is None:
        path = ""
    if not isinstance(path, str):
        raise TypeError("path must be string")
    s = path.strip().replace("\\", "/")
    if len(s) > MAX_PATH_LEN:
        raise ValueError("path too long")
    target = (ws / s).resolve() if s else ws
    try:
        target.relative_to(ws)
    except ValueError:
        raise ValueError("Blocked: outside Workspace. Workspace only.") from None
    return ws, target, s


def _validate_name(name: str | None) -> str:
    if not isinstance(name, str):
        raise TypeError("name must be string")
    s = name.strip()
    if not s:
        raise ValueError("name must be non-empty")
    if len(s) > MAX_NAME_LEN:
        raise ValueError(f"name too long (max {MAX_NAME_LEN})")
    if "/" in s or "\\" in s or ".." in s or "\x00" in s:
        raise ValueError("Blocked: folder name must be a single name (no paths).")
    return s


def _count_items(folder: Path) -> tuple[int, bool]:
    """Count children up to MAX_COUNT. Returns (count, truncated)."""
    n = 0
    try:
        for _ in folder.iterdir():
            n += 1
            if n >= MAX_COUNT:
                return n, True
    except Exception:
        return 0, False
    return n, False


def _list_dir(target: Path) -> dict:
    if not target.exists():
        raise FileNotFoundError(f"Workspace folder not found: {target.name or 'Workspace'}")
    if not target.is_dir():
        raise ValueError("path is a file, not a folder")
    try:
        children = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except Exception as e:
        raise RuntimeError(f"Cannot list Workspace folder: {e}") from e
    entries: list[dict] = []
    truncated = len(children) > MAX_ENTRIES
    for p in children[:MAX_ENTRIES]:
        try:
            if p.is_dir():
                count, trunc = _count_items(p)
                entries.append({"name": p.name, "type": "folder",
                                "items": count, "items_truncated": trunc})
            elif p.is_file():
                try:
                    st = p.stat()
                    entries.append({"name": p.name, "type": "file",
                                    "size": st.st_size,
                                    "mtime": int(st.st_mtime)})
                except Exception:
                    entries.append({"name": p.name, "type": "file"})
        except Exception:
            continue
    return {"entries": entries, "truncated": truncated}


def _purge_document_orphans() -> None:
    """Best-effort: drop document embeddings whose file is gone. Never raises."""
    try:
        import sys as _sys
        proj = Path(__file__).resolve().parents[3]
        if str(proj) not in _sys.path:
            _sys.path.insert(0, str(proj))
        from core.documents.store import purge_missing
        purge_missing()
    except Exception:
        pass


def _delete_file(ws: Path, target: Path, name: str) -> dict:
    """Delete ONE file jailed in Workspace + its document embeddings."""
    if not isinstance(name, str):
        raise TypeError("name must be string")
    s = name.strip().replace("\\", "/")
    if not s or len(s) > MAX_NAME_LEN:
        raise ValueError("name must be a non-empty file name")
    if "/" in s or ".." in s or "\x00" in s:
        raise ValueError("Blocked: name must be a single file name (no paths).")
    dest = (target / s).resolve()
    try:
        dest.relative_to(ws)
    except ValueError:
        raise ValueError("Blocked: outside Workspace. Workspace only.") from None
    if not dest.exists() or not dest.is_file():
        raise FileNotFoundError(f"Workspace file not found: {s}")
    try:
        dest.unlink()
    except Exception as e:
        raise RuntimeError(f"Cannot delete file: {e}") from e
    try:
        rel = str(dest.relative_to(ws)).replace("\\", "/")
    except ValueError:
        rel = s
    removed_embeddings = False
    try:
        import sys as _sys
        proj = Path(__file__).resolve().parents[3]
        if str(proj) not in _sys.path:
            _sys.path.insert(0, str(proj))
        from core.documents.store import delete_file_embeddings
        # File may live in a subfolder: purge by relative id and by bare name.
        removed_embeddings = bool(delete_file_embeddings(rel)) or bool(delete_file_embeddings(s))
    except Exception:
        pass
    return {"path": rel, "success": True, "embeddings_removed": removed_embeddings}


def execute(action: str = "list", path: str | None = None, name: str | None = None) -> dict:
    if not isinstance(action, str):
        raise TypeError("action must be string")
    act = action.strip()
    if act not in ("list", "create_folder", "delete_file"):
        raise ValueError("action must be list, create_folder or delete_file")
    ws, target, rel = _resolve_dir(path)
    event = format_event(action=act, path=rel, name=name if isinstance(name, str) else "")
    if act == "list":
        out = _list_dir(target)
        _purge_document_orphans()
        return {"event": event, "action": act,
                "path": rel or "/", "success": True, **out}
    if act == "delete_file":
        out = _delete_file(ws, target, name or "")
        return {"event": event, "action": act, **out}
    folder_name = _validate_name(name)
    dest = (target / folder_name).resolve()
    try:
        dest.relative_to(ws)
    except ValueError:
        raise ValueError("Blocked: outside Workspace. Workspace only.") from None
    if dest.exists():
        raise ValueError(f"Folder already exists: {folder_name}")
    try:
        dest.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        raise ValueError(f"Folder already exists: {folder_name}") from None
    except Exception as e:
        raise RuntimeError(f"Cannot create folder: {e}") from e
    try:
        shown = str(dest.relative_to(ws)).replace("\\", "/")
    except ValueError:
        shown = folder_name
    return {"event": event, "action": act, "path": shown, "success": True}
