"""Event for document_search"""
SHOW_ARROW = True


def format_event(file_id: str = "", **_) -> str:
    name = ""
    if isinstance(file_id, str) and file_id.strip():
        name = file_id.strip().replace("\\", "/").split("/")[-1]
    if len(name) > 40:
        name = name[:37] + "..."
    return f"Searching docs{(' · ' + name) if name else ''}"
