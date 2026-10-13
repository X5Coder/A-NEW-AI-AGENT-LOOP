"""Event for terminal: fixed English title, details live under the arrow."""

SHOW_ARROW = True


def format_event(command: str = "", **_) -> str:
    # Fixed title: the full command + output render under the arrow
    # (see engine._summarize_output), never in the title.
    return "Terminal Command"
