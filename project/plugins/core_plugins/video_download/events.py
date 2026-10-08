"""Event for video_download"""
from urllib.parse import urlparse

SHOW_ARROW = True


def format_event(url: str = "", **_) -> str:
    name = "video"
    try:
        if isinstance(url, str) and url.strip():
            host = urlparse(url.strip()).netloc or url.strip()
            name = host.replace("www.", "")
    except Exception:
        name = "video"
    if len(name) > 30:
        name = name[:27] + "..."
    return f"Downloading video · {name}"