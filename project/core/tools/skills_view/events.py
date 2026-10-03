"""Event for skills_view"""

SHOW_ARROW = True


def format_event(skill_name: str = "", **_) -> str:
    name = skill_name.strip() if isinstance(skill_name, str) and skill_name.strip() else "skill"
    # Use filename only, never full content
    if "/" in name or "\\" in name:
        name = name.split("/")[-1].split("\\")[-1]
    if len(name) > 30:
        name = name[:27] + "..."
    return f"Opening · {name}"
