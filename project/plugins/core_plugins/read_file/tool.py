"""
read_file - read file contents safely, JAILED inside X5Agent/Workspace only.
Validates path inside Workspace, handles missing/invalid, bounded size.
"""
from __future__ import annotations

from pathlib import Path

from .events import format_event

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
ALLOWED_ROOTS: list[Path] | None = None


def _get_allowed_roots() -> list[Path]:
    # Workspace only - model device. System Core and Data are NOT readable via this tool.
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
        return [ws.resolve()]
    except Exception:
        repo = Path(__file__).resolve().parents[4]
        ws = repo / "X5Agent" / "Workspace"
        try:
            ws.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return [ws.resolve()]


def _validate_path(p: str) -> Path:
    if not isinstance(p, str):
        raise TypeError("path must be string")
    s = p.strip()
    if not s:
        raise ValueError("path must be non-empty")
    if len(s) > 500:
        raise ValueError("path too long")
    # Resolve relative to Workspace (model device), NOT project root.
    ws = _get_allowed_roots()[0]
    cand = Path(s)
    if not cand.is_absolute():
        cand = (ws / s).resolve()
    else:
        cand = cand.resolve()
    # Must be inside Workspace (prevent any escape)
    allowed = False
    for root in _get_allowed_roots():
        try:
            cand.relative_to(root)
            allowed = True
            break
        except ValueError:
            continue
    if not allowed:
        raise ValueError(f"Blocked: path must be inside Workspace: {ws}")
    return cand


def execute(path: str) -> dict:
    event = format_event(path=path)
    resolved = _validate_path(path)
    if not resolved.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if resolved.is_dir():
        raise IsADirectoryError(f"Path is directory, not file: {path}")
    size = resolved.stat().st_size
    if size > MAX_FILE_SIZE:
        raise ValueError(f"File too large ({size} bytes, max {MAX_FILE_SIZE}) - use streaming/chunking")
    try:
        # Detect binary? Try utf-8
        text = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"File is not valid UTF-8 text: {path}")
    except PermissionError as e:
        raise PermissionError(f"Permission denied: {path}") from e
    return {"event": event, "path": str(resolved), "content": text, "size": size}
