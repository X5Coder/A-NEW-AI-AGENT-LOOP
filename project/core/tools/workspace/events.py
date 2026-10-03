"""Event for workspace"""
SHOW_ARROW = False


def format_event(action: str = "", path: str = "", name: str = "", **_) -> str:
    act = action.strip() if isinstance(action, str) else ""
    target = ""
    if isinstance(name, str) and name.strip():
        target = name.strip()
    elif isinstance(path, str) and path.strip():
        target = path.strip()
    if len(target) > 40:
        target = target[:37] + "..."
    if not act:
        act = "workspace"
    return f"Workspace · {act}{(' · ' + target) if target else ''}"
