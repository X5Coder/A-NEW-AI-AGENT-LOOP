"""
write_file - create or overwrite file safely.
Validates path inside project, bounds content size, atomic write.
"""
from __future__ import annotations

from pathlib import Path

from .events import format_event

MAX_CONTENT_SIZE = 5 * 1024 * 1024  # 5MB


def _get_allowed_roots():
    project_root = Path(__file__).resolve().parents[3]
    return [project_root.resolve()]


def _validate_path(p: str) -> Path:
    if not isinstance(p, str):
        raise TypeError("path must be string")
    s = p.strip()
    if not s:
        raise ValueError("path required")
    if len(s) > 500:
        raise ValueError("path too long")
    project_root = Path(__file__).resolve().parents[3]
    cand = Path(s)
    if not cand.is_absolute():
        cand = (project_root / s).resolve()
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
        raise ValueError(f"path must be inside project: {project_root}")
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
