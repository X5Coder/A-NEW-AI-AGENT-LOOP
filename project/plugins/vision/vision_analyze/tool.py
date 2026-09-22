"""
vision_analyze - analyze image via SmolVLM2-500M.
Delegates to models/vision/service.py with validation and bounded resources.
"""
from __future__ import annotations

from .events import format_event


def _validate_prompt(p) -> str | None:
    if p is None:
        return None
    if not isinstance(p, str):
        raise TypeError("prompt must be string")
    if len(p) > 2000:
        raise ValueError("prompt too long (max 2000)")
    return p.strip() if p.strip() else None


def execute(image_id: str | None = None, image_path: str | None = None, prompt: str | None = None) -> dict:
    if image_id is not None and not isinstance(image_id, str):
        raise TypeError("image_id must be string")
    if image_path is not None and not isinstance(image_path, str):
        raise TypeError("image_path must be string")
    if not image_id and not image_path:
        raise ValueError("At least one of image_id or image_path is required")
    pr = _validate_prompt(prompt)
    event = format_event(image_path=image_path, image_id=image_id)

    # Delegate to vision service
    from models.vision.service import analyze_image
    # analyze_image does its own validation and path traversal checks
    try:
        answer = analyze_image(image_path=image_path, image_id=image_id, prompt=pr)
    except FileNotFoundError as e:
        raise FileNotFoundError(str(e)) from e
    except Exception as e:
        raise RuntimeError(f"Vision analysis failed: {e}") from e

    return {"event": event, "image_id": image_id, "image_path": image_path, "prompt": pr, "answer": answer}
