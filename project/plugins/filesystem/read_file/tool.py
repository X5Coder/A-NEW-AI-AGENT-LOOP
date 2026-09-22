"""
read_file - read file contents safely.
Validates path, prevents traversal outside project, handles missing/invalid, bounded size.
"""
from __future__ import annotations

from pathlib import Path

from .events import format_event

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
ALLOWED_ROOTS: list[Path] | None = None


def _get_allowed_roots() -> list[Path]:
    project_root = Path(__file__).resolve().parents[3]
    # Allow project root and storage
    return [project_root.resolve(), (project_root / "storage").resolve()]


def _validate_path(p: str) -> Path:
    if not isinstance(p, str):
        raise TypeError("path must be string")
    s = p.strip()
    if not s:
        raise ValueError("path must be non-empty")
    if len(s) > 500:
        raise ValueError("path too long")
    # Resolve relative to project root if not absolute
    project_root = Path(__file__).resolve().parents[3]
    cand = Path(s)
    if not cand.is_absolute():
        cand = (project_root / s).resolve()
    else:
        cand = cand.resolve()
    # Must be inside project root (prevent arbitrary read)
    allowed = False
    for root in _get_allowed_roots():
        try:
            cand.relative_to(root)
            allowed = True
            break
        except ValueError:
            continue
    if not allowed:
        raise ValueError(f"path must be inside project directory: {project_root}")
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
