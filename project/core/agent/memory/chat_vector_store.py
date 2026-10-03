"""
Chat Vector Store - per-message embeddings for conversation.
Each message has id, content, embedding. Deleting/editing a message auto-manages its embedding.
Uses Qwen3-Embedding-0.6B via models/embedding/service.py.
Storage: storage/vector/chat_embeddings.json + storage/database/conversation.json (source of truth).
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

def _resolve_vector_path() -> Path:
    # Unified: writes ALWAYS to X5Agent/Data/vector. Fallback legacy only if resolver missing.
    try:
        import sys as _sys
        _proj = Path(__file__).resolve().parents[3]
        if str(_proj) not in _sys.path:
            _sys.path.insert(0, str(_proj))
        from core.paths import data_vector_file
        return data_vector_file("chat_embeddings.json")
    except Exception:
        return Path(__file__).resolve().parents[3] / "storage" / "vector" / "chat_embeddings.json"


def _resolve_conversation_candidates() -> list[Path]:
    # Data first (unified), legacy fallback.
    legacy = Path(__file__).resolve().parents[3] / "storage" / "database" / "conversation.json"
    try:
        import sys as _sys2
        _proj2 = Path(__file__).resolve().parents[3]
        if str(_proj2) not in _sys2.path:
            _sys2.path.insert(0, str(_proj2))
        from core.paths import data_db_dir as _dd
        return [_dd() / "conversation.json", legacy]
    except Exception:
        return [legacy]


VECTOR_PATH = _resolve_vector_path()
_CONVERSATION_CANDIDATES = _resolve_conversation_candidates()
CONVERSATION_PATH = _CONVERSATION_CANDIDATES[0]


def _load_vectors() -> dict:
    if not VECTOR_PATH.exists():
        return {}
    try:
        data = json.loads(VECTOR_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_vectors(data: dict) -> None:
    VECTOR_PATH.parent.mkdir(parents=True, exist_ok=True)
    # bounded
    if len(data) > 1000:
        # keep last 1000
        items = list(data.items())[-1000:]
        data = dict(items)
    VECTOR_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_conversation() -> list[dict]:
    for cand in _CONVERSATION_CANDIDATES:
        if not cand.exists():
            continue
        try:
            data = json.loads(cand.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            continue
    return []


def _save_conversation(data: list[dict]) -> None:
    CONVERSATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    if len(data) > 1000:
        data = data[-1000:]
    CONVERSATION_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _embed_single(text: str) -> list[float]:
    from models.embedding.service import embed_texts
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be non-empty")
    if len(text) > 4000:
        text = text[:4000]
    return embed_texts([text])[0]


def add_message(message_id: str, content: str, role: str = "user") -> dict:
    """Add a message embedding. Called when new message is persisted."""
    if not isinstance(message_id, str) or not message_id.strip():
        raise ValueError("message_id required")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("content required")
    mid = message_id.strip()
    vectors = _load_vectors()
    if mid in vectors:
        return vectors[mid]
    emb = _embed_single(content)
    entry = {"id": mid, "role": role, "content": content[:4000], "embedding": emb, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    vectors[mid] = entry
    _save_vectors(vectors)
    return entry


def delete_message(message_id: str) -> bool:
    """Delete a message and its embedding. Returns True if existed."""
    if not isinstance(message_id, str) or not message_id.strip():
        raise ValueError("message_id required")
    mid = message_id.strip()
    # Remove from conversation
    conv = _load_conversation()
    new_conv = [m for m in conv if str(m.get("id") or m.get("message_id") or "") != mid]
    # Also handle messages without id - match by content? For now, also check if id stored in message
    # If message has no id field, we can't reliably delete by id, but we try to remove by id if present
    found_in_conv = len(new_conv) < len(conv)
    if found_in_conv:
        _save_conversation(new_conv)
    vectors = _load_vectors()
    found_in_vec = mid in vectors
    if found_in_vec:
        del vectors[mid]
        _save_vectors(vectors)
    return found_in_conv or found_in_vec


def update_message(message_id: str, new_content: str) -> dict:
    """Edit a message: delete old embedding and create new one (new version)."""
    if not isinstance(message_id, str) or not message_id.strip():
        raise ValueError("message_id required")
    if not isinstance(new_content, str) or not new_content.strip():
        raise ValueError("new_content required")
    mid = message_id.strip()
    # Update conversation (preserve original role)
    conv = _load_conversation()
    role = "user"
    updated = False
    for m in conv:
        if str(m.get("id") or m.get("message_id") or "") == mid:
            role = str(m.get("role") or "user")
            m["content"] = new_content[:4000]
            m["edited_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            updated = True
            break
    if updated:
        _save_conversation(conv)
    # Delete old embedding and create new
    vectors = _load_vectors()
    if mid in vectors:
        del vectors[mid]
    # will be recreated via add
    _save_vectors(vectors)
    return add_message(mid, new_content, role=role)


def ensure_synced() -> dict:
    """Sync conversation.json with vector store: add missing, delete orphaned, update changed. Called on startup and before search."""
    conv = _load_conversation()
    vectors = _load_vectors()
    # Ensure each message has id
    changed = False
    for m in conv:
        if "id" not in m and "message_id" not in m:
            m["id"] = str(uuid.uuid4())[:8]
            changed = True
        # normalize id field
        if "message_id" in m and "id" not in m:
            m["id"] = m.pop("message_id")
        elif "message_id" in m:
            # keep id, remove duplicate
            m.pop("message_id", None)
    if changed:
        _save_conversation(conv)
        conv = _load_conversation()
        vectors = _load_vectors()

    # Add missing embeddings
    for m in conv:
        mid = str(m.get("id","")).strip()
        if not mid:
            continue
        content = str(m.get("content","")).strip()
        if not content:
            continue
        if mid not in vectors:
            try:
                add_message(mid, content, role=m.get("role","user"))
                vectors = _load_vectors()  # reload
            except Exception:
                continue
        else:
            # If content changed, update embedding
            stored = vectors[mid].get("content","")
            if stored != content:
                try:
                    # delete old, create new
                    del vectors[mid]
                    _save_vectors(vectors)
                    add_message(mid, content, role=m.get("role","user"))
                    vectors = _load_vectors()
                except Exception:
                    continue
    # Delete orphaned vectors (messages deleted from conversation)
    conv_ids = {str(m.get("id","")).strip() for m in conv if m.get("id")}
    orphaned = [mid for mid in list(vectors.keys()) if mid not in conv_ids]
    for mid in orphaned:
        del vectors[mid]
    if orphaned:
        _save_vectors(vectors)
    return {"conversation_count": len(conv), "vector_count": len(vectors), "orphaned_removed": len(orphaned)}


def search(query: str, k: int = 5) -> list[dict]:
    """Semantic search over chat messages using stored embeddings."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be non-empty")
    if not isinstance(k, int) or k < 1 or k > 20:
        k = 5
    ensure_synced()
    vectors = _load_vectors()
    if not vectors:
        return []
    conv = _load_conversation()
    # Build lookup for content
    from models.embedding.service import embed_query
    import numpy as np
    q_emb = np.array(embed_query(query), dtype=np.float32)
    scored = []
    for mid, entry in vectors.items():
        emb = np.array(entry.get("embedding", []), dtype=np.float32)
        if emb.size == 0:
            continue
        score = float(np.dot(q_emb, emb))
        # find original message
        msg = next((m for m in conv if str(m.get("id"))==mid), {"id": mid, "content": entry.get("content"), "role": entry.get("role")})
        scored.append((score, msg, entry))
    scored.sort(reverse=True, key=lambda x: x[0])
    results = []
    for score, msg, entry in scored[:k]:
        results.append({"score": round(score,4), "message": msg, "id": entry.get("id")})
    return results


def clear_all() -> int:
    vectors = _load_vectors()
    n = len(vectors)
    _save_vectors({})
    return n
