"""
video_download - fetch a public video URL via yt-dlp and save mp4 JAILED inside X5Agent/Workspace only.
Returns the saved workspace-relative path + source metadata so video_analyze/video_transcribe can run next.
Single video only (playlists blocked), http(s) URLs only, bounded retries/timeouts/sizes.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from .events import format_event

MAX_URL_LEN = 2000
MAX_FILENAME_LEN = 100
MAX_VIDEO_BYTES = 100 * 1024 * 1024  # 100MB cap (video_analyze accepts 50MB; larger files warn)
ALLOWED_QUALITIES = ("best", "720p", "480p")

_FORMATS = {
    "best": "best[ext=mp4]/best",
    "720p": "best[height<=720][ext=mp4]/best[height<=720]/best",
    "480p": "best[height<=480][ext=mp4]/best[height<=480]/best",
}


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


def _validate_url(url: str) -> str:
    if not isinstance(url, str):
        raise TypeError("url must be string")
    s = url.strip()
    if not s:
        raise ValueError("url must be non-empty")
    if len(s) > MAX_URL_LEN:
        raise ValueError("url too long")
    try:
        parts = urlparse(s)
    except Exception:
        raise ValueError("Invalid url") from None
    if parts.scheme not in ("http", "https"):
        raise ValueError("Blocked: url must start with http:// or https://")
    if not parts.netloc:
        raise ValueError("Invalid url: missing host")
    return s


def _validate_filename(name: str | None) -> str | None:
    if name is None:
        return None
    if not isinstance(name, str):
        raise TypeError("filename must be string")
    s = name.strip().replace("\\", "/").split("/")[-1].strip()
    if not s:
        raise ValueError("filename must be non-empty")
    if len(s) > MAX_FILENAME_LEN:
        raise ValueError(f"filename too long (max {MAX_FILENAME_LEN})")
    if "\x00" in s or s in (".", ".."):
        raise ValueError("Blocked: invalid filename")
    s = re.sub(r'[<>:"|?*\x00-\x1f]', "_", s)
    s = s.strip(". ")
    if not s:
        raise ValueError("Blocked: invalid filename")
    if not s.lower().endswith(".mp4"):
        s += ".mp4"
    return s


def _safe_title(name: str, video_id: str) -> str:
    base = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", (name or "").strip())
    base = re.sub(r"\s+", " ", base).strip("._ ")
    if not base:
        base = f"video_{video_id or 'download'}"
    if len(base) > 80:
        base = base[:80].rstrip("._ ")
    if not base.lower().endswith(".mp4"):
        base += ".mp4"
    return base


def execute(url: str, filename: str | None = None, quality: str | None = None) -> dict:
    event = format_event(url=url if isinstance(url, str) else "")
    clean_url = _validate_url(url)
    custom_name = _validate_filename(filename)
    q = "best"
    if quality is not None:
        if not isinstance(quality, str):
            raise TypeError("quality must be string")
        q = quality.strip().lower() or "best"
        if q not in ALLOWED_QUALITIES:
            raise ValueError(f"quality must be one of {ALLOWED_QUALITIES}")

    try:
        from yt_dlp import YoutubeDL
    except ImportError as e:
        raise RuntimeError("yt-dlp is not installed. Run: pip install -U yt-dlp") from e

    ws = _workspace_root()
    # Reserve target name: custom name wins, else title-based after info fetch.
    # Download to a temp name first to avoid partial-file confusion, then keep final.
    fmt = _FORMATS[q]
    outtmpl = str(ws / "%(title)s [%(id)s].%(ext)s")

    opts = {
        "format": fmt,
        "merge_output_format": "mp4",
        "outtmpl": outtmpl,
        "noplaylist": True,  # single video only: playlist URLs download first item only
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 3,  # bounded per Master Rules (max 3)
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 1,
        "nocheckcertificate": False,
    }

    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(clean_url, download=True)
            try:
                saved = ydl.prepare_filename(info)
            except Exception:
                saved = ""
    except Exception as e:
        msg = str(e)[:500]
        if "Unsupported URL" in msg or "No video" in msg:
            raise ValueError(f"Download failed: unsupported or private video ({msg[:200]})") from None
        raise RuntimeError(f"Download failed: {msg}") from None

    if not saved:
        raise RuntimeError("Download failed: no file produced")
    saved_path = Path(saved)
    if not saved_path.is_absolute():
        saved_path = (ws / saved).resolve()
    else:
        saved_path = saved_path.resolve()
    try:
        saved_path.relative_to(ws)
    except ValueError:
        raise RuntimeError("Download escaped Workspace, blocked") from None
    if not saved_path.exists():
        # ext may differ (mkv/webm merged); search by id stem
        video_id = str((info or {}).get("id", "")) if isinstance(info, dict) else ""
        candidates = sorted(ws.glob(f"*{video_id}*")) if video_id else []
        saved_path = next((c for c in candidates if c.is_file()), saved_path)
        if not saved_path.exists():
            raise RuntimeError("Download failed: file not found after fetch")

    if custom_name:
        dest = (ws / custom_name).resolve()
        try:
            dest.relative_to(ws)
        except ValueError:
            raise ValueError("Blocked: filename escapes Workspace") from None
        try:
            if dest != saved_path:
                if dest.exists():
                    dest.unlink()
                saved_path.rename(dest)
                saved_path = dest
        except Exception as e:
            raise RuntimeError(f"Cannot rename download: {e}") from None

    size = saved_path.stat().st_size
    if size <= 0:
        try:
            saved_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise RuntimeError("Download failed: empty file")
    if size > MAX_VIDEO_BYTES:
        try:
            saved_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise ValueError(f"Video too large ({size} bytes, max {MAX_VIDEO_BYTES}). Retry with quality=480p.")

    info_dict = info if isinstance(info, dict) else {}
    metadata = {
        "title": str(info_dict.get("title", ""))[:300],
        "id": str(info_dict.get("id", ""))[:100],
        "uploader": str(info_dict.get("uploader", "") or info_dict.get("channel", ""))[:200],
        "duration_seconds": info_dict.get("duration", 0) or 0,
        "width": info_dict.get("width", 0) or 0,
        "height": info_dict.get("height", 0) or 0,
        "fps": info_dict.get("fps", 0) or 0,
        "ext": saved_path.suffix.lower().lstrip("."),
        "source_url": clean_url[:500],
        "webpage_url": str(info_dict.get("webpage_url", "") or "")[:500],
        "extractor": str(info_dict.get("extractor", "") or "")[:100],
    }
    rel = str(saved_path.relative_to(ws)).replace("\\", "/")
    result: dict = {
        "event": event,
        "path": rel,
        "video_path": rel,  # alias: pass directly to video_analyze/video_transcribe
        "size": size,
        "metadata": metadata,
        "hint": f"Saved to Workspace as {rel}. Call video_analyze with video_path={rel} to inspect frames via vision.",
    }
    if size > 50 * 1024 * 1024:
        result["warning"] = "File exceeds 50MB: video_analyze may reject it. Re-download with quality=480p if needed."
    return result