"""Event labels for pdf_studio (DISCOVERABLE, always expandable)."""

SHOW_ARROW = True


def _short(value: str, limit: int = 40) -> str:
    s = value.strip() if isinstance(value, str) else ""
    if len(s) > limit:
        return s[: limit - 3] + "..."
    return s or "studio"


def format_event(action: str = "", title: str = "", studio_id: str = "",
                 pages: object = None, **_) -> str:
    label = studio_id.strip() if isinstance(studio_id, str) and studio_id.strip() else title.strip()
    act = str(action or "").strip().lower()
    if act == "list":
        return "Listing Studios"
    if act == "get":
        return f"Viewing Studio · {_short(label or 'pdf')}"
    if act == "rename":
        return f"Renaming Studio · {_short(label or 'pdf')}"
    if str(action or "").strip().lower() == "open":
        return f"Opening Studio · {_short(label or 'pdf')}"
    try:
        n = len(pages) if isinstance(pages, (list, tuple)) else 0
    except Exception:
        n = 0
    if n > 0:
        return f"Preparing Studio · {_short(label or 'pdf')} ({min(n, 10)} pages)"
    return f"Preparing Studio · {_short(label or 'pdf')}"
