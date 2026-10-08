"""
embedding_memory - semantic text memory behind tool_search.
Stores one text as one local Qwen3 embedding chunk in X5Agent/Data/vector
and retrieves the most similar chunks for a query sentence via cosine similarity.
New schema: reason + text (store) + query (search-only) + top_k (count).
Persistent across restarts, bounded (500 entries, oldest dropped first).
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from .events import format_event

ALLOWED = {"store", "search", "list", "get", "delete", "clear"}
MAX_ENTRIES = 500
MAX_TEXT_LEN = 2000
DEFAULT_TOP_K = 5
MAX_TOP_K = 10


def _store_path() -> Path:
    try:
        import sys as _sys
        _proj = Path(__file__).resolve().parents[3]
        if str(_proj) not in _sys.path:
            _sys.path.insert(0, str(_proj))
        from core.paths import data_vector_file
        return data_vector_file("embedding_memory.json")
    except Exception:
        return Path(__file__).resolve().parents[3] / "storage" / "vector" / "embedding_memory.json"


STORE_PATH = _store_path()
_STORE_LEGACY = Path(__file__).resolve().parents[3] / "storage" / "vector" / "embedding_memory.json"


def _load() -> list[dict]:
    for cand in (STORE_PATH, _STORE_LEGACY):
        if not cand.exists():
            continue
        try:
            data = json.loads(cand.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("entries"), list):
                return data["entries"]
            if isinstance(data, list):
                return data
        except Exception:
            continue
    return []


def _save(entries: list[dict]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if len(entries) > MAX_ENTRIES:
        entries = entries[-MAX_ENTRIES:]
    tmp = STORE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps({"entries": entries, "count": len(entries)},
                              ensure_ascii=False), encoding="utf-8")
    tmp.replace(STORE_PATH)


def count_stored() -> int:
    """Number of stored embedding chunks. Never raises (0 on any failure)."""
    try:
        return len(_load())
    except Exception:
        return 0


def _clean_text(v: Any, name: str) -> str:
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"{name} must be non-empty string")
    t = " ".join(v.split())
    if len(t) > MAX_TEXT_LEN:
        raise ValueError(f"{name} too long (max {MAX_TEXT_LEN} chars)")
    return t


def _validate_top_k(v: Any) -> int:
    k = DEFAULT_TOP_K if v is None else v
    if isinstance(k, bool) or not isinstance(k, int) or k < 1 or k > MAX_TOP_K:
        raise ValueError(f"top_k must be integer 1-{MAX_TOP_K}")
    return k


def _cosine(q: list[float], m: list[float]) -> float:
    import math
    dot = sum(a * b for a, b in zip(q, m))
    nq = math.sqrt(sum(a * a for a in q))
    nm = math.sqrt(sum(b * b for b in m))
    if nq == 0 or nm == 0:
        return 0.0
    return dot / (nq * nm)


def _do_store(chunk: str) -> dict:
    from models.embedding.service import embed_texts
    vectors = embed_texts([chunk], normalize=True)
    entries = _load()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    entry = {"id": uuid.uuid4().hex[:8], "text": chunk,
             "embedding": vectors[0], "created_at": now}
    entries.append(entry)
    _save(entries)
    return {"event": format_event(text=chunk), "action": "store",
            "stored": {"id": entry["id"], "text": chunk},
            "total": len(entries)}


def _do_search(q: str, k: int) -> dict:
    from models.embedding.service import embed_query
    entries = _load()
    if not entries:
        return {"event": format_event(query=q), "action": "search",
                "query": q, "results": [],
                "note": "memory is empty, store a text first"}
    qvec = embed_query(q)
    scored = []
    for e in entries:
        emb = e.get("embedding")
        if not isinstance(emb, list) or not emb:
            continue
        scored.append((round(_cosine(qvec, emb), 4), e))
    scored.sort(reverse=True, key=lambda x: x[0])
    results = [{"id": e["id"], "text": e["text"], "score": s}
               for s, e in scored[:k]]
    return {"event": format_event(query=q), "action": "search",
            "query": q, "results": results}


def execute(reason: str | None = None, text: str | None = None,
            query: str | None = None, top_k: int | None = None,
            **_legacy: Any) -> dict:
    # reason is informational only (shown in UI); never affects logic.
    # Legacy kwargs (action/texts/entry_id) are accepted silently so old
    # model calls fail clearly instead of crashing with TypeError.
    legacy_texts = _legacy.get("texts")
    legacy_action = _legacy.get("action")
    if text is None and isinstance(legacy_texts, list) and legacy_texts:
        first = legacy_texts[0] if isinstance(legacy_texts[0], str) else None
        text = first
    has_text = isinstance(text, str) and bool(text.strip())
    has_query = isinstance(query, str) and bool(query.strip())
    if has_text and has_query:
        raise ValueError("provide either text (to store) or query (to search), not both")
    if not has_text and not has_query:
        if isinstance(legacy_action, str) and legacy_action.strip().lower() in ALLOWED:
            raise ValueError("embedding_memory now uses text to store and query to search (top_k 1-10)")
        raise ValueError("text or query required: provide text to store, or query to search")
    if has_query:
        q = _clean_text(query, "query")
        k = _validate_top_k(top_k)
        return _do_search(q, k)
    chunk = _clean_text(text, "text")
    return _do_store(chunk)