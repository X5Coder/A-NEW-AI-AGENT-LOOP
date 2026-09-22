"""
Vision service - wraps SmolVLM2-500M-Video-Instruct via transformers.
Single responsibility: load vision model lazily, analyze images with text prompt.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

MAX_RETRIES = 3
RETRY_BACKOFF = [1, 2, 4]

_processor = None
_model = None
_model_path_cached: Path | None = None


def _resolve_model_path() -> Path:
    from models.loader import get_model_path
    p = get_model_path("vision")
    if p is None or not p.exists():
        raise FileNotFoundError("Vision model not found. Run python scripts/setup_models.py")
    return p


def _load():
    global _processor, _model, _model_path_cached
    if _processor is not None and _model is not None:
        return _processor, _model
    model_path = _resolve_model_path()
    project_root = Path(__file__).resolve().parents[2]
    allowed = (project_root / "models").resolve()
    try:
        model_path.resolve().relative_to(allowed)
    except ValueError as exc:
        raise ValueError(f"Vision model path escapes allowed directory") from exc

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            import torch
            # Try SmolVLM specific classes first (correct for SmolVLM2-500M), without device_map to avoid accelerate dependency
            try:
                from transformers import SmolVLMProcessor, SmolVLMForConditionalGeneration

                processor = SmolVLMProcessor.from_pretrained(str(model_path), trust_remote_code=False)
                model = SmolVLMForConditionalGeneration.from_pretrained(
                    str(model_path),
                    torch_dtype=torch.float32,
                    trust_remote_code=False,
                    low_cpu_mem_usage=True,
                )
                try:
                    model = model.to("cpu")
                except Exception:
                    pass
            except ImportError:
                # Fallback to AutoProcessor / AutoModel generic
                from transformers import AutoProcessor, AutoModel

                processor = AutoProcessor.from_pretrained(str(model_path), trust_remote_code=False)
                model = AutoModel.from_pretrained(
                    str(model_path),
                    torch_dtype=torch.float32,
                    trust_remote_code=False,
                    low_cpu_mem_usage=True,
                )
                try:
                    model = model.to("cpu")
                except Exception:
                    pass
            model.eval()
            _processor = processor
            _model = model
            _model_path_cached = model_path
            return _processor, _model
        except Exception as e:
            if attempt == MAX_RETRIES:
                raise RuntimeError(f"Failed to load vision model after {MAX_RETRIES}: {e}") from e
            time.sleep(RETRY_BACKOFF[min(attempt - 1, len(RETRY_BACKOFF) - 1)])
    raise RuntimeError("Unreachable vision load")


def _validate_image_path(path: str) -> Path:
    if not isinstance(path, str) or not path.strip():
        raise ValueError("image_path must be non-empty string")
    p = Path(path)
    # Prevent path traversal - resolve and ensure inside project or storage
    # We allow any existing file but check for existence and extension
    if not p.exists():
        # Try relative to project root
        project_root = Path(__file__).resolve().parents[2]
        alt = (project_root / path).resolve()
        if alt.exists():
            p = alt
        else:
            raise FileNotFoundError(f"Image not found: {path}")
    if p.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("Image too large (max 20MB)")
    if p.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
        raise ValueError(f"Unsupported image type: {p.suffix}")
    return p


def analyze_image(image_path: Optional[str] = None, image_id: Optional[str] = None, prompt: Optional[str] = None) -> str:
    """
    Analyze image and answer prompt.
    At least one of image_id or image_path required. image_id resolves to storage/files/images/{id}
    """
    if not image_path and not image_id:
        raise ValueError("At least one of image_id or image_path is required")
    if prompt is not None and not isinstance(prompt, str):
        raise ValueError("prompt must be string")
    if prompt is not None and len(prompt) > 2000:
        raise ValueError("prompt too long (max 2000)")

    # Resolve image
    resolved: Path
    if image_path:
        resolved = _validate_image_path(image_path)
    else:
        # image_id -> storage/files/images/
        assert image_id is not None
        if "/" in image_id or "\\" in image_id or ".." in image_id:
            raise ValueError("Invalid image_id")
        project_root = Path(__file__).resolve().parents[2]
        resolved = (project_root / "storage" / "files" / "images" / image_id).resolve()
        # Validate inside images dir
        allowed = (project_root / "storage" / "files" / "images").resolve()
        try:
            resolved.relative_to(allowed)
        except ValueError as exc:
            raise ValueError("image_id escapes allowed directory") from exc
        if not resolved.exists():
            raise FileNotFoundError(f"Image id not found: {image_id}")

    prompt_text = prompt.strip() if prompt and prompt.strip() else "Describe this image in detail."

    processor, model = _load()

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            from PIL import Image
            import torch
            image = Image.open(str(resolved)).convert("RGB")
            # SmolVLM chat template
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "image": image},
                        {"type": "text", "text": prompt_text},
                    ],
                }
            ]
            # Apply chat template
            text = processor.apply_chat_template(messages, add_generation_prompt=True)
            inputs = processor(text=[text], images=[image], return_tensors="pt")
            # Move to cpu
            inputs = {k: v.to("cpu") if hasattr(v, "to") else v for k, v in inputs.items()}
            with torch.no_grad():
                generated = model.generate(**inputs, max_new_tokens=256, do_sample=False)
            # Decode, strip prompt
            decoded = processor.batch_decode(generated, skip_special_tokens=True)[0]
            # SmolVLM output contains prompt + answer; try to extract answer
            # Split on last occurrence of "assistant"
            if "assistant" in decoded.lower():
                # naive split
                parts = decoded.split("assistant")
                answer = parts[-1].strip(" :\n")
            else:
                answer = decoded.strip()
            # If answer contains original prompt text, strip it
            if prompt_text in answer:
                answer = answer.replace(prompt_text, "").strip()
            return answer if answer else decoded.strip()
        except Exception as e:
            if attempt == MAX_RETRIES:
                raise RuntimeError(f"Vision analysis failed after {MAX_RETRIES}: {e}") from e
            time.sleep(RETRY_BACKOFF[min(attempt - 1, len(RETRY_BACKOFF) - 1)])
    raise RuntimeError("Unreachable vision analyze")


def is_ready() -> bool:
    try:
        from models.loader import is_model_ready
        return is_model_ready("vision")
    except Exception:
        return False
