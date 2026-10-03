SHOW_ARROW = False


def format_event(action: str = "", trigger_name: str = "", trigger_id: str = "", **_) -> str:
    name = trigger_name or trigger_id or "trigger"
    if len(name) > 20:
        name = name[:17] + "..."
    if action == "create":
        return f"Creating trigger · {name}"
    if action == "list":
        return "Listing triggers"
    if action == "delete":
        return f"Removing trigger · {name}"
    return f"Trigger · {name}"

def format_search_event(**_) -> str:
    return "Finding triggers"
