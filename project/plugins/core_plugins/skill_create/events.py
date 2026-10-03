SHOW_ARROW = False


def format_event(name: str = "", skill_name: str = "", **_) -> str:
    n = name or skill_name or "skill"
    if isinstance(n, str):
        n = n.strip()
    else:
        n = str(n)
    if len(n) > 30:
        n = n[:27] + "..."
    return f"Creating skill · {n}"
