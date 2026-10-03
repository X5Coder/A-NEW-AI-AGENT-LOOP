"""Events for image_memory"""

SHOW_ARROW = False


def format_event(action: str = "", name: str = "", image_id: str = "", **_) -> str:
    name_val = name or image_id or ""
    if action == "list":
        return "Listing images"
    if action == "clear_temp" or action == "clear_all":
        return "Clearing images"
    if action == "store":
        n = name_val.strip() if isinstance(name_val, str) and name_val.strip() else "image"
        if len(n) > 20:
            n = n[:17] + "..."
        return f"Storing image · {n}"
    if action == "update":
        n = name_val.strip() if isinstance(name_val, str) and name_val.strip() else "image"
        if len(n) > 20:
            n = n[:17] + "..."
        return f"Updating image · {n}"
    if action == "delete":
        n = name_val.strip() if isinstance(name_val, str) and name_val.strip() else "image"
        if len(n) > 20:
            n = n[:17] + "..."
        return f"Removing image · {n}"
    return "Managing image"
