"""
Bundled embedding service (sentence-transformers, offline).

Loads ONLY from project/embedding_model/ (the single bundled folder, ships
with the project; never downloads at runtime). Device (GPU/CPU) is detected
once at startup and cached, then reused for every call.
Use core.embedding.bootstrap.ensure_embedding_model() at startup so the
weights are always present before the first embed call.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import List

MAX_RETRIES = 3
RETRY_BACKOFF = [1, 2, 4]

_model = None


def _load_model():
    global _model
    if _model is not None:
        return _model
    from core.embedding.bootstrap import detect_device, embedding_dir, is_complete
    model_path = embedding_dir()
    if not is_complete():
        raise FileNotFoundError(
            "Embedding model folder is incomplete (project/embedding_model/). "
            "Start the console or run install.py - the model downloads "
            "automatically on first run.")
    try:
        model_path.resolve().relative_to(Path(__file__).resolve().parents[2].resolve())
    except ValueError as exc:
        raise ValueError("Embedding path escapes the project directory") from exc
    device = detect_device()
    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            from sentence_transformers import SentenceTransformer
            _model = SentenceTransformer(str(model_path), device=device, trust_remote_code=False)
            return _model
        except Exception as e:
            last_err = e
            if attempt == MAX_RETRIES:
                break
            time.sleep(RETRY_BACKOFF[min(attempt - 1, len(RETRY_BACKOFF) - 1)])
    raise RuntimeError(f"Failed to load built-in embedding model: {last_err}") from last_err


def embed_texts(texts: List[str], normalize: bool = True) -> List[List[float]]:
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
            embeddings = model.encode(texts, normalize_embeddings=normalize, show_progress_bar=False)
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
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be non-empty string")
    if len(query) > 2000:
        raise ValueError("query too long (max 2000 chars)")
    return embed_texts([query])[0]


def is_ready() -> bool:
    try:
        from core.embedding.bootstrap import is_complete
        return is_complete()
    except Exception:
        return False
