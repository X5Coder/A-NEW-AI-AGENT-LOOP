"""Event labels for studio_page (CORE in studio sessions)."""

SHOW_ARROW = True


def format_event(action: str = "", page_no: int = 0, **_) -> str:
    try:
        n = int(page_no)
    except Exception:
        n = 0
    a = str(action or "list").strip().lower()
    if a == "read":
        return f"Reading slide · {n}" if n else "Reading slide"
    if a == "deliver":
        return f"Delivering page · {n}" if n else "Delivering page"
    if a == "patch":
        return f"Patching page · {n}" if n else "Patching page"
    if a == "test":
        return f"Testing page · {n}" if n else "Testing page"
    if a == "fetch":
        return f"Saving web file · {n}" if n else "Saving web file"
    return "Listing slides"
