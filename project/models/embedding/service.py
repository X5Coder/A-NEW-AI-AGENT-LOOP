"""
Embedding service - wraps Qwen3-Embedding-0.6B via sentence-transformers.
Single responsibility: load model lazily, produce normalized embeddings, bounded retries.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import List

MAX_RETRIES = 3
RETRY_BACKOFF = [1, 2, 4]

_model = None
_model_path_cached: Path | None = None


def _resolve_model_path() -> Path:
    from models.loader import get_model_path
    p = get_model_path("embedding")
    if p is None or not p.exists():
        raise FileNotFoundError("Embedding model not found. Run python scripts/setup_models.py")
    return p


def _load_model():
    global _model, _model_path_cached
    if _model is not None:
        return _model
    model_path = _resolve_model_path()
    # Validate path is inside project/models
    from pathlib import Path as _P
    project_root = _P(__file__).resolve().parents[2]
    allowed = (project_root / "models").resolve()
    try:
        model_path.resolve().relative_to(allowed)
    except ValueError as exc:
        raise ValueError(f"Model path escapes allowed directory: {model_path}") from exc

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            from sentence_transformers import SentenceTransformer
            # sentence-transformers loads safetensors directly
            m = SentenceTransformer(str(model_path), device="cpu", trust_remote_code=False)
            _model = m
            _model_path_cached = model_path
            return _model
        except Exception as e:
            if attempt == MAX_RETRIES:
                raise RuntimeError(f"Failed to load embedding model after {MAX_RETRIES} attempts: {e}") from e
            time.sleep(RETRY_BACKOFF[min(attempt - 1, len(RETRY_BACKOFF) - 1)])
    raise RuntimeError("Unreachable embedding load")


def embed_texts(texts: List[str], normalize: bool = True) -> List[List[float]]:
    """
    Embed list of texts. Returns list of vectors (normalized by default).
    Validates inputs, bounded retries, no secrets.
    """
    if not isinstance(texts, list):
        raise TypeError("texts must be list[str]")
    if not texts:
        return []
    for t in texts:
        if not isinstance(t, str):
            raise TypeError("each text must be str")
        if len(t) > 8000:
            raise ValueError("text too long (max 8000 chars)")
    model = _load_model()
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # sentence-transformers encode handles batching
            embeddings = model.encode(texts, normalize_embeddings=normalize, show_progress_bar=False)
            # convert to list
            if hasattr(embeddings, "tolist"):
                return embeddings.tolist()
            return [list(e) for e in embeddings]
        except Exception as e:
            last_err = e
            if attempt == MAX_RETRIES:
                break
            time.sleep(RETRY_BACKOFF[min(attempt - 1, len(RETRY_BACKOFF) - 1)])
    raise RuntimeError(f"Embedding failed after {MAX_RETRIES} retries: {last_err}") from last_err


def embed_query(query: str) -> List[float]:
    """Embed single query string."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be non-empty string")
    if len(query) > 2000:
        raise ValueError("query too long (max 2000 chars)")
    return embed_texts([query])[0]


def is_ready() -> bool:
    """Check if embedding model files exist (without loading)."""
    try:
        from models.loader import is_model_ready
        return is_model_ready("embedding")
    except Exception:
        return False
