"""Events for important_memory"""

SHOW_ARROW = True


def format_event(action: str = "", key: str = "", fact_id: str = "", **_) -> str:
    name = key or fact_id or ""
    if action == "list":
        return "Listing memories"
    if action == "clear_all":
        return "Clearing memories"
    if action == "store":
        n = name.strip() if isinstance(name, str) and name.strip() else "memory"
        if len(n) > 20:
            n = n[:17] + "..."
        return f"Saving memory · {n}"
    if action == "update":
        n = name.strip() if isinstance(name, str) and name.strip() else "memory"
        if len(n) > 20:
            n = n[:17] + "..."
        return f"Updating memory · {n}"
    if action == "delete":
        n = name.strip() if isinstance(name, str) and name.strip() else "memory"
        if len(n) > 20:
            n = n[:17] + "..."
        return f"Removing memory · {n}"
    return "Managing memory"
