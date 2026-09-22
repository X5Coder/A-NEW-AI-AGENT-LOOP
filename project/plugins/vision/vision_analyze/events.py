from pathlib import Path

def format_event(image_path: str | None = None, image_id: str | None = None, **_) -> str:
    name = ""
    if image_path:
        name = Path(image_path).name
    elif image_id:
        name = image_id
    else:
        name = "image"
    if len(name) > 30:
        name = name[:27] + "..."
    return f"Analyzing · {name}"
