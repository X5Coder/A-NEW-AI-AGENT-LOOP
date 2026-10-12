"""
X5Browser events — one short, plain-English, non-technical label per tool.

Shown in the chat the moment the tool is called; the label shimmers while the
tool runs and settles when the next event starts. Screenshot tools also return
has_screenshot + screenshot_path in their output, so the UI shows the image
(and only the image) under an animated expand arrow.

Labels stay action-meaningful because the model sees them in tool outputs too.
"""
from __future__ import annotations

_LABELS = {
    "page_snapshot": "Looking at the page",
    "navigate": "Opening the page",
    "click": "Clicking for you",
    "fill": "Typing for you",
    "read_text": "Reading this page",
    "quick_search": "Searching Google",
    "fetch_pages": "Fetching these pages",
    "extract_data": "Extracting the details",
    "read_markdown": "Reading as Markdown",
    "extract_image": "Getting this image",
    "find_elements": "Searching the page",
    "inspect_element": "Inspecting one element",
    "screenshot": "Taking a screenshot",
    "get_page_info": "Checking the page",
    "wait_for": "Waiting a moment",
    "click_verify": "Clicking and checking",
    "fill_form": "Filling the form",
    "press_keys": "Pressing keys",
    "scroll": "Scrolling the page",
    "hover": "Hovering for options",
    "hover_click": "Opening this menu",
    "choose_option": "Picking this choice",
    "drag_drop": "Dragging an item",
    "upload_files": "Attaching your files",
    "manage_tabs": "Switching tabs",
    "get_events": "Checking page events",
    "answer_popup": "Answering this popup",
    "manage_downloads": "Getting your download",
    "read_console": "Checking for errors",
    "console_command": "Running page script",
    "list_requests": "Checking network activity",
    "export_page": "Saving this page",
    "verify_state": "Double-checking",
    "batch_actions": "Running several steps",
    "manage_triggers": "Watching this page",
    "open_browser": "Opening the browser",
    "close_browser": "Closing the browser",
    "restart_browser": "Restarting the browser",
    "browser_status": "Checking the browser",
    "human_control": "Handing you control",
    "live_preview": "Sharing a live look",
    "show_preview": "Showing you the live preview",
}

# True = step may show an expand arrow with user-readable output.
# False = plain step line only, no arrow (technical JSON / control flow).
SHOW_ARROW = {
    "read_text": True,
    "quick_search": True,
    "fetch_pages": True,
    "extract_data": True,
    "read_markdown": True,
    "extract_image": True,
    "screenshot": True,
    "get_page_info": True,
    "browser_status": True,
    "read_console": True,
    "verify_state": True,
}


def should_show_arrow(tool_name: str) -> bool:
    """Whether one browser tool may show an output arrow. Never raises."""
    try:
        name = tool_name.strip() if isinstance(tool_name, str) else ""
        return bool(SHOW_ARROW.get(name, False))
    except Exception:
        return False


def format_event(tool_name: str, **kwargs) -> str:
    """Plain-English event label for one browser tool call. Never raises."""
    try:
        name = tool_name.strip() if isinstance(tool_name, str) else ""
        label = _LABELS.get(name)
        return label if label else (name or "browser")
    except Exception:
        return "browser"
