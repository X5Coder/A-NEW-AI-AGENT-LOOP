"""
image_memory service - persistent storage for image metadata + vision text.
Responsibility: store metadata: id, name, path, size, type, width, height, vision_text, is_persistent
Lifecycle: temp images auto-cleared after vision/loop end, persistent sent every request as context.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

STORAGE = Path(__file__).resolve().parents[3] / "storage" / "database" / "image_memory.json"
MAX_ENTRIES = 100
MAX_VISION_LEN = 5000


def _load() -> list[dict]:
    if not STORAGE.exists():
        return []
    try:
        data = json.loads(STORAGE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(items: list[dict]) -> None:
    STORAGE.parent.mkdir(parents=True, exist_ok=True)
    if len(items) > MAX_ENTRIES:
        items = items[-MAX_ENTRIES:]
    STORAGE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_meta(path_str: str | None) -> dict:
    meta: dict[str, Any] = {}
    if not path_str:
        return meta
    p = Path(path_str)
    # try resolve relative to project
    project_root = Path(__file__).resolve().parents[3]
    cand = p
    if not p.is_absolute():
        cand = (project_root / path_str).resolve()
    else:
        cand = p.resolve()
    # ensure inside project
    try:
        cand.relative_to(project_root.resolve())
    except ValueError:
        # allow if exists outside? but for security require inside
        pass
    if cand.exists() and cand.is_file():
        try:
            meta["size"] = cand.stat().st_size
            meta["type"] = cand.suffix.lower().lstrip(".") or "unknown"
            # width/height via PIL
            from PIL import Image
            with Image.open(str(cand)) as im:
                meta["width"], meta["height"] = im.size
        except Exception:
            pass
        meta["path"] = str(cand)
    else:
        meta["path"] = path_str
    return meta


def store_image(name: str | None = None, path: str | None = None, image_id: str | None = None, vision_text: str | None = None, is_persistent: bool = True, is_temp: bool = False) -> dict:
    if not name and not path and not image_id:
        raise ValueError("At least one of name, path, or image_id required")
    if image_id is not None and not isinstance(image_id, str):
        raise TypeError("image_id must be string")
    if name is not None and not isinstance(name, str):
        raise TypeError("name must be string")
    if path is not None and not isinstance(path, str):
        raise TypeError("path must be string")
    if vision_text is not None and not isinstance(vision_text, str):
        raise TypeError("vision_text must be string")
    if vision_text is not None and len(vision_text) > MAX_VISION_LEN:
        raise ValueError(f"vision_text too long (max {MAX_VISION_LEN})")
    # generate id if missing
    iid = image_id.strip() if image_id and image_id.strip() else str(uuid.uuid4())[:8]
    if len(iid) > 64:
        raise ValueError("image_id too long")
    # check duplicate
    items = _load()
    if any(x.get("image_id") == iid for x in items):
        raise ValueError(f"image_id already exists: {iid}")
    if len(items) >= MAX_ENTRIES:
        raise RuntimeError(f"Too many images (max {MAX_ENTRIES}), delete some first")
    meta = _extract_meta(path)
    derived_name = name.strip() if name and name.strip() else (Path(path).name if path else iid)
    if len(derived_name) > 200:
        raise ValueError("name too long")
    entry = {
        "image_id": iid,
        "name": derived_name,
        "path": meta.get("path", path or ""),
        "size": meta.get("size"),
        "type": meta.get("type"),
        "width": meta.get("width"),
        "height": meta.get("height"),
        "vision_text": vision_text.strip() if vision_text and vision_text.strip() else None,
        "is_persistent": bool(is_persistent),
        "is_temp": bool(is_temp),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    items.append(entry)
    _save(items)
    return entry


def update_image(image_id: str, vision_text: str | None = None, name: str | None = None, path: str | None = None, is_persistent: bool | None = None) -> dict:
    if not isinstance(image_id, str) or not image_id.strip():
        raise ValueError("image_id required")
    iid = image_id.strip()
    items = _load()
    idx = next((i for i, x in enumerate(items) if x.get("image_id") == iid), None)
    if idx is None:
        raise FileNotFoundError(f"image_id not found: {iid}")
    if vision_text is not None:
        if not isinstance(vision_text, str):
            raise TypeError("vision_text must be string")
        if len(vision_text) > MAX_VISION_LEN:
            raise ValueError("vision_text too long")
        items[idx]["vision_text"] = vision_text.strip() if vision_text.strip() else None
    if name is not None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be non-empty")
        if len(name) > 200:
            raise ValueError("name too long")
        items[idx]["name"] = name.strip()
    if path is not None:
        meta = _extract_meta(path)
        items[idx]["path"] = meta.get("path", path)
        if "size" in meta:
            items[idx]["size"] = meta["size"]
        if "type" in meta:
            items[idx]["type"] = meta["type"]
        if "width" in meta:
            items[idx]["width"] = meta["width"]
        if "height" in meta:
            items[idx]["height"] = meta["height"]
    if is_persistent is not None:
        items[idx]["is_persistent"] = bool(is_persistent)
    items[idx]["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _save(items)
    return items[idx]


def delete_image(image_id: str) -> dict:
    if not isinstance(image_id, str) or not image_id.strip():
        raise ValueError("image_id required")
    iid = image_id.strip()
    items = _load()
    idx = next((i for i, x in enumerate(items) if x.get("image_id") == iid), None)
    if idx is None:
        raise FileNotFoundError(f"image_id not found: {iid}")
    removed = items.pop(idx)
    _save(items)
    return removed


def get_image(image_id: str) -> dict | None:
    if not isinstance(image_id, str):
        raise TypeError("image_id must be string")
    items = _load()
    for x in items:
        if x.get("image_id") == image_id.strip():
            return x
    return None


def list_images(include_temp: bool = True) -> list[dict]:
    items = _load()
    if not include_temp:
        return [x for x in items if not x.get("is_temp")]
    return items


def list_persistent_with_vision() -> list[dict]:
    """Return persistent images that have vision_text - to be injected as context each request."""
    items = _load()
    return [x for x in items if x.get("is_persistent") and x.get("vision_text")]


def clear_temp() -> int:
    items = _load()
    before = len(items)
    kept = [x for x in items if not x.get("is_temp")]
    _save(kept)
    return before - len(kept)


def clear_all() -> int:
    n = len(_load())
    _save([])
    return n


def get_context_payload() -> dict:
    """For agent loop: metadata to send with every request."""
    persistent = list_images(include_temp=False)
    with_vision = list_persistent_with_vision()
    return {
        "all_images": persistent,
        "with_vision": with_vision,
        "count": len(persistent),
        "vision_count": len(with_vision),
    }
