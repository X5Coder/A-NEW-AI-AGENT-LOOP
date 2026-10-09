"""
Media helpers for universal model tools (vision / speech).

Small, single-responsibility file I/O: resolve an image or audio file from an
id or a path, validate size/type, and hand back bytes or base64 for the
engine variables ({{image_base64}}, {{audio_base64}}). No model code here.
"""
from __future__ import annotations

import base64
from pathlib import Path

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
AUDIO_EXTS = (".wav", ".mp3", ".m4a", ".ogg", ".flac", ".webm")
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_AUDIO_BYTES = 25 * 1024 * 1024

_MIME_BY_EXT = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".bmp": "image/bmp",
    ".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
    ".ogg": "audio/ogg", ".flac": "audio/flac", ".webm": "audio/webm",
}


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _image_dirs() -> list[Path]:
    dirs: list[Path] = []
    try:
        import sys as _sys
        if str(_project_root()) not in _sys.path:
            _sys.path.insert(0, str(_project_root()))
        from core.paths import data_subdir as _ds
        dirs.append((_ds("files", "images")).resolve())
    except Exception:
        pass
    dirs.append((_project_root() / "storage" / "files" / "images").resolve())
    return dirs


def resolve_image(image_id: str | None = None, image_path: str | None = None) -> Path:
    """Resolve an image file. Raises ValueError/FileNotFoundError. Never trusts input."""
    if image_path:
        if not isinstance(image_path, str) or not image_path.strip():
            raise ValueError("image_path must be a non-empty string")
        p = Path(image_path)
        if not p.exists():
            alt = (_project_root() / image_path).resolve()
            if alt.exists():
                p = alt
            else:
                raise FileNotFoundError(f"Image not found: {image_path}")
        return _checked(p, IMAGE_EXTS, MAX_IMAGE_BYTES, "Image")
    if not image_id or not isinstance(image_id, str):
        raise ValueError("At least one of image_id or image_path is required")
    if "/" in image_id or "\\" in image_id or ".." in image_id:
        raise ValueError("Invalid image_id")
    for allowed in _image_dirs():
        try:
            cand = (allowed / image_id).resolve()
            try:
                cand.relative_to(allowed)
            except ValueError:
                continue
            if cand.exists():
                return _checked(cand, IMAGE_EXTS, MAX_IMAGE_BYTES, "Image")
        except (ValueError, OSError):
            continue
    # Fall back to the stored path of the image_memory entry.
    try:
        import sys as _sys
        if str(_project_root()) not in _sys.path:
            _sys.path.insert(0, str(_project_root()))
        from core.memory.image_memory.service import get_image
        entry = get_image(image_id)
        entry_path = (entry or {}).get("path") if isinstance(entry, dict) else None
        if entry_path and Path(entry_path).exists():
            return _checked(Path(entry_path), IMAGE_EXTS, MAX_IMAGE_BYTES, "Image")
    except Exception:
        pass
    raise FileNotFoundError(f"Image id not found: {image_id}")


def _checked(p: Path, exts: tuple, cap: int, label: str) -> Path:
    if p.stat().st_size > cap:
        raise ValueError(f"{label} too large (max {cap // (1024 * 1024)}MB)")
    if p.suffix.lower() not in exts:
        raise ValueError(f"Unsupported {label.lower()} type: {p.suffix}")
    return p


def image_to_base64(p: Path) -> tuple[str, str]:
    """Returns (base64_text, mime)."""
    raw = p.read_bytes()
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("Image too large (max 20MB)")
    mime = _MIME_BY_EXT.get(p.suffix.lower(), "image/png")
    return base64.b64encode(raw).decode("ascii"), mime


def read_audio(audio_path: str | Path) -> tuple[bytes, str]:
    """Read an audio file (cap 25MB). Returns (bytes, mime)."""
    p = Path(audio_path)
    if not p.exists():
        alt = (_project_root() / str(audio_path)).resolve()
        if alt.exists():
            p = alt
        else:
            raise FileNotFoundError(f"Audio not found: {audio_path}")
    if p.stat().st_size > MAX_AUDIO_BYTES:
        raise ValueError("Audio too large (max 25MB)")
    return p.read_bytes(), _MIME_BY_EXT.get(p.suffix.lower(), "audio/wav")
