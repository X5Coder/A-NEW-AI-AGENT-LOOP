"""Event labels for image_search (DISCOVERABLE, always expandable)."""

SHOW_ARROW = True


def _short(value: str, limit: int = 40) -> str:
    s = value.strip() if isinstance(value, str) else ""
    if len(s) > limit:
        return s[: limit - 3] + "..."
    return s or "images"


def format_event(query: str = "", **_) -> str:
    return f"Searching images · {_short(query)}"
