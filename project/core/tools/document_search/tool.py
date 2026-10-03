"""
document_search - semantic chunk search inside one uploaded document.

CORE tool (always in context, next to vector_chat_search): takes a
Workspace file_id + a query sentence + top_k (min 1) and returns the
most similar 300-word markdown chunks with scores. The file_id MUST be
a MarkItDown-supported type or the call is rejected before any work.
Indexing (convert -> chunk -> embed) happens automatically on first use
and re-runs only when the file hash changed. Deleted Workspace files
lose their embeddings and raise FileNotFoundError.
"""
from __future__ import annotations

from typing import Any

from .events import format_event


def _validate_top_k(v: Any) -> int:
    if v is None:
        return 3
    if isinstance(v, bool) or not isinstance(v, int):
        raise TypeError("top_k must be integer")
    if v < 1 or v > 20:
        raise ValueError("top_k must be 1..20")
    return v


def execute(file_id: str, query: str, top_k: int | None = None) -> dict:
    import sys as _sys
    from pathlib import Path as _P
    proj = _P(__file__).resolve().parents[3]
    if str(proj) not in _sys.path:
        _sys.path.insert(0, str(proj))
    from core.documents.store import normalize_file_id, search

    fid = normalize_file_id(file_id)
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be non-empty string")
    if len(query.strip()) > 2000:
        raise ValueError("query too long (max 2000)")
    limit = _validate_top_k(top_k)
    event = format_event(file_id=fid)
    found = search(fid, query.strip(), top_k=limit)
    results = found.get("results", [])
    text = results[0]["text"][:1000] if results else ""
    return {"event": event, "file_id": fid, "query": query.strip(),
            "top_k": limit, "chunk_count": found.get("chunk_count", 0),
            "results": results, "text": text}
