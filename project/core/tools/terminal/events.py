"""Event for terminal"""
def format_event(command: str = "", **_) -> str:
    cmd = command.strip() if isinstance(command, str) else ""
    # Truncate to safe UI length
    if len(cmd) > 40:
        cmd = cmd[:37] + "..."
    if not cmd:
        cmd = "command"
    return f"Running · {cmd}"
