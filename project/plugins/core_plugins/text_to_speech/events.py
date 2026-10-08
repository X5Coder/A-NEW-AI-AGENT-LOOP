"""Event for text_to_speech"""

SHOW_ARROW = True


def format_event(text: str = "", **_) -> str:
    name = "speech"
    try:
        if isinstance(text, str) and text.strip():
            name = " ".join(text.split())[:25]
    except Exception:
        name = "speech"
    return f"Speaking · {name}"