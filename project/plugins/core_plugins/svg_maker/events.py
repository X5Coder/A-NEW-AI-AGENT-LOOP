"""Event labels for svg_maker (DISCOVERABLE, always expandable)."""

SHOW_ARROW = True


def _short(value: str, limit: int = 40) -> str:
    s = value.strip() if isinstance(value, str) else ""
    if len(s) > limit:
        return s[: limit - 3] + "..."
    return s or "icon"


def format_event(description: str = "", icon_name: str = "", **_) -> str:
    label = icon_name.strip() if isinstance(icon_name, str) and icon_name.strip() else description
    return f"Creating icon · {_short(label)}"
