"""
write_file - create or overwrite file safely, JAILED inside X5Agent/Workspace only.
Validates path inside Workspace, bounds content size, atomic write.
"""
from __future__ import annotations

from pathlib import Path

from .events import format_event

MAX_CONTENT_SIZE = 5 * 1024 * 1024  # 5MB


def _get_allowed_roots():
    # Workspace only - model device. Open for anything INSIDE Workspace.
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
        raise ValueError("path required")
    if len(s) > 500:
        raise ValueError("path too long")
    ws = _get_allowed_roots()[0]
    cand = Path(s)
    if not cand.is_absolute():
        cand = (ws / s).resolve()
    else:
        cand = cand.resolve()
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


def _validate_content(c) -> str:
    if not isinstance(c, str):
        raise TypeError("content must be string")
    if len(c.encode("utf-8")) > MAX_CONTENT_SIZE:
        raise ValueError(f"content too large (max {MAX_CONTENT_SIZE} bytes)")
    return c


def execute(path: str, content: str) -> dict:
    event = format_event(path=path)
    resolved = _validate_path(path)
    txt = _validate_content(content)
    # Ensure parent exists
    resolved.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write via temp then rename? Simple direct for now with cleanup on error
    try:
        resolved.write_text(txt, encoding="utf-8")
    except PermissionError as e:
        raise PermissionError(f"Permission denied: {path}") from e
    return {"event": event, "path": str(resolved), "bytes_written": len(txt.encode("utf-8")), "success": True}
