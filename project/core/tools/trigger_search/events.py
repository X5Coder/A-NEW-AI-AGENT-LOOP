SHOW_ARROW = False


def format_event(action: str = "", **_) -> str:
    act = action.strip().lower() if isinstance(action, str) else ""
    if not act or act == "search":
        return "Finding triggers"
    return f"Managing trigger · {act}"
