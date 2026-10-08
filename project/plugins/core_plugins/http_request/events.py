"""Event for http_request"""
from urllib.parse import urlparse

SHOW_ARROW = True


def format_event(method: str = "GET", url: str = "", **_) -> str:
    host = "url"
    try:
        if isinstance(url, str) and url.strip():
            host = urlparse(url.strip()).netloc or url.strip()
            host = host.replace("www.", "")
    except Exception:
        host = "url"
    if len(host) > 30:
        host = host[:27] + "..."
    verb = "Fetching"
    try:
        if isinstance(method, str):
            m = method.strip().upper()
            if m in ("POST", "PUT", "PATCH", "DELETE"):
                verb = "Posting" if m == "POST" else f"Sending {m}"
    except Exception:
        pass
    return f"{verb} · {host}"