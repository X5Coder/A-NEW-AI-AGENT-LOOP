"""
vector_chat_search - semantic search over conversation history.
Uses embedding model if available, otherwise fallback lexical.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .events import format_event


def _validate_query(q: Any) -> str:
    if not isinstance(q, str):
        raise TypeError("query must be string")
    s = q.strip()
    if not s:
        raise ValueError("query must be non-empty")
    if len(s) > 2000:
        raise ValueError("query too long (max 2000)")
    return s


def _validate_k(k: Any) -> int:
    if k is None:
        return 5
    if not isinstance(k, int):
        raise TypeError("k must be integer")
    if k < 1 or k > 20:
        raise ValueError("k must be between 1 and 20")
    return k


def _load_history() -> list[dict]:
    # Conversation history stored as storage/database/conversation.json if exists
    # Fallback empty
    project_root = Path(__file__).resolve().parents[3]
    hist_path = project_root / "storage" / "database" / "conversation.json"
    if not hist_path.exists():
        return []
    try:
        data = json.loads(hist_path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data[:1000]  # bounded
        return []
    except Exception:
        return []


def execute(query: str, k: int | None = None) -> dict:
    q = _validate_query(query)
    limit = _validate_k(k)
    event = format_event()
    history = _load_history()
    if not history:
        return {"event": event, "query": q, "k": limit, "results": [], "note": "no history yet"}

    # Try embedding semantic search if model ready
    try:
        from models.embedding.service import embed_texts, is_ready
        if is_ready():
            # embed query + each message content
            texts = []
            for m in history:
                content = m.get("content", "") if isinstance(m, dict) else str(m)
                if isinstance(content, str) and content.strip():
                    texts.append(content[:2000])
                else:
                    texts.append("")
            if texts:
                q_emb = embed_texts([q])[0]
                doc_embs = embed_texts(texts)
                # cosine similarity (embeddings are normalized)
                import numpy as np
                qv = np.array(q_emb)
                scores = []
                for idx, emb in enumerate(doc_embs):
                    dv = np.array(emb)
                    score = float(np.dot(qv, dv))
                    scores.append((score, idx))
                scores.sort(reverse=True, key=lambda x: x[0])
                top = scores[:limit]
                results = []
                for score, idx in top:
                    msg = history[idx]
                    results.append({"score": round(score, 4), "message": msg})
                return {"event": event, "query": q, "k": limit, "results": results}
    except Exception:
        pass

    # Fallback lexical search
    q_low = q.lower()
    scored = []
    for idx, m in enumerate(history):
        content = m.get("content", "") if isinstance(m, dict) else str(m)
        if not isinstance(content, str):
            continue
        c_low = content.lower()
        score = c_low.count(q_low)  # simple
        if q_low in c_low:
            score += 1
        scored.append((score, idx))
    scored.sort(reverse=True, key=lambda x: x[0])
    results = [{"score": s, "message": history[i]} for s, i in scored[:limit] if s > 0]
    return {"event": event, "query": q, "k": limit, "results": results}
