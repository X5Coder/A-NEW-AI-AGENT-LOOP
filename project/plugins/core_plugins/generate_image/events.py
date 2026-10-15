"""Event for generate_image"""

SHOW_ARROW = True


def format_event(prompt: str = "", **_) -> str:
    name = "image"
    try:
        if isinstance(prompt, str) and prompt.strip():
            name = " ".join(prompt.split())[:25]
    except Exception:
        name = "image"
    return f"Generating image · {name}"
