"""Event for read_file"""
from pathlib import Path

SHOW_ARROW = True


def format_event(path: str = "", **_) -> str:
    name = Path(path).name if path else "file"
    if not name:
        name = "file"
    if len(name) > 30:
        name = name[:27] + "..."
    return f"Reading · {name}"
