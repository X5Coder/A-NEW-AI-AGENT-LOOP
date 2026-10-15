"""Event labels for studio_read (read-only slide view in studio sessions)."""

SHOW_ARROW = True


def format_event(page_no: int = 0, **_) -> str:
    try:
        n = int(page_no)
    except Exception:
        n = 0
    return f"Reading slide · {n}" if n else "Reading slide"
