"""
vision_analyze - analyze image via the connected AI Vision Model.
Refuses with a fixed message (+ model link) when no vision model is connected.
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


def execute(image_id: str | None = None, image_path: str | None = None, prompt: str | None = None, cancel_flag=None) -> dict:
    if image_id is not None and not isinstance(image_id, str):
        raise TypeError("image_id must be string")
    if image_path is not None and not isinstance(image_path, str):
        raise TypeError("image_path must be string")
    if not image_id and not image_path:
        raise ValueError("At least one of image_id or image_path is required")
    pr = _validate_prompt(prompt)
    event = format_event(image_path=image_path, image_id=image_id)

    from core.ai_models.media import image_to_base64, resolve_image
    from core.ai_models.store import require_model
    record = require_model("vision")  # fixed refusal + model link when missing
    resolved = resolve_image(image_id=image_id, image_path=image_path)
    b64, mime = image_to_base64(resolved)
    from core.ai_models.engine import analyze
    try:
        # STOP reaches the vision request mid-flight: the loop forwards its
        # cancel event here (dispatcher only passes it when accepted), and
        # the engine aborts between read chunks instead of after inference.
        options = {"cancel_event": cancel_flag} if cancel_flag is not None else None
        answer = analyze(record, {
            "prompt": pr or "Describe this image in detail.",
            "image_base64": b64, "image_mime": mime,
        }, options)
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Vision analysis failed: {e}") from None

    return {"event": event, "image_id": image_id, "image_path": image_path, "prompt": pr, "answer": answer}
