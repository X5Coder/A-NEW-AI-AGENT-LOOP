"""Event labels for pdf_create (DISCOVERABLE, always expandable)."""

SHOW_ARROW = True


def _short(value: str, limit: int = 40) -> str:
    s = value.strip() if isinstance(value, str) else ""
    if len(s) > limit:
        return s[: limit - 3] + "..."
    return s or "pdf"


def format_event(action: str = "", pdf_name: str = "", task_id: str = "", **_) -> str:
    act = action.strip().lower() if isinstance(action, str) else ""
    if act == "update":
        return f"Updating PDF · {_short(task_id if task_id else pdf_name)}"
    if act == "get":
        return f"Reading PDF · {_short(task_id if task_id else pdf_name)}"
    return f"Creating PDF · {_short(pdf_name if pdf_name else task_id)}"
