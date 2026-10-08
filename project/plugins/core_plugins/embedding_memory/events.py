"""Event for embedding_memory"""

SHOW_ARROW = True


def format_event(text: str | None = None, query: str | None = None,
                 action: str = "", **_) -> str:
    # Search mode when a retrieval query is present, otherwise store mode.
    # Legacy action accepted silently for old callers.
    try:
        if isinstance(query, str) and query.strip():
            return "Searching memory"
        if isinstance(action, str) and action.strip().lower() == "search":
            return "Searching memory"
    except Exception:
        pass
    return "Saving memory"