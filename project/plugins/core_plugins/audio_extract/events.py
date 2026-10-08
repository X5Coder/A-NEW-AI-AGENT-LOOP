"""Event for audio_extract"""
from pathlib import Path

SHOW_ARROW = True


def format_event(video_path: str = "", **_) -> str:
    name = Path(video_path).name if isinstance(video_path, str) and video_path else "audio"
    if not name:
        name = "audio"
    if len(name) > 30:
        name = name[:27] + "..."
    return f"Extracting audio · {name}"
