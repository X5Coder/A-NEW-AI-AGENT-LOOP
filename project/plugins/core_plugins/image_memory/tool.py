"""
image_memory tool - discoverable behind tool_search.
Manages image metadata: id, name, path, size, type, width, height, vision_text.
Persistent images sent every request; temp auto-cleared.
Delegates to core/memory/image_memory/service.py
"""
from __future__ import annotations

from typing import Any

from .events import format_event

ALLOWED_ACTIONS = {"store","update","delete","list","get","clear_temp","clear_all"}


def _validate_action(v: Any) -> str:
    if not isinstance(v, str):
        raise TypeError("action must be string")
    a = v.strip().lower()
    if a not in ALLOWED_ACTIONS:
        raise ValueError(f"action must be one of {ALLOWED_ACTIONS}")
    return a


def execute(action: str, image_id: str | None = None, name: str | None = None, path: str | None = None, vision_text: str | None = None, is_persistent: bool | None = None) -> dict:
    act = _validate_action(action)
    event = format_event(action=act, name=name or "", image_id=image_id or "")
    # Lazy import service
    from core.memory.image_memory.service import store_image, update_image, delete_image, get_image, list_images, clear_temp, clear_all, get_context_payload

    if act == "store":
        # Detect temp: if is_persistent False, then is_temp True? Actually is_temp separate, but we map is_persistent=False => temp? Spec says temp images are not persistent
        is_persist = True if is_persistent is None else bool(is_persistent)
        # is_temp = not is_persist if the caller marks as temp? For simplicity, if not persistent we mark is_temp True to allow auto-clear
        is_tmp = not is_persist
        entry = store_image(name=name, path=path, image_id=image_id, vision_text=vision_text, is_persistent=is_persist, is_temp=is_tmp)
        return {"event": event, "action": act, "image": entry, "context": get_context_payload()}

    if act == "update":
        if not image_id:
            raise ValueError("image_id required for update")
        entry = update_image(image_id, vision_text=vision_text, name=name, path=path, is_persistent=is_persistent)
        return {"event": event, "action": act, "image": entry, "context": get_context_payload()}

    if act == "delete":
        if not image_id:
            raise ValueError("image_id required for delete")
        removed = delete_image(image_id)
        return {"event": event, "action": act, "deleted": removed}

    if act == "get":
        if not image_id:
            raise ValueError("image_id required for get")
        img = get_image(image_id)
        if img is None:
            raise FileNotFoundError(f"image_id not found: {image_id}")
        return {"event": event, "action": act, "image": img}

    if act == "list":
        items = list_images(include_temp=True)
        return {"event": event, "action": act, "images": items, "count": len(items), "context": get_context_payload()}

    if act == "clear_temp":
        n = clear_temp()
        return {"event": event, "action": act, "cleared": n}

    if act == "clear_all":
        n = clear_all()
        return {"event": event, "action": act, "cleared": n}

    raise ValueError(f"unknown action {act}")
