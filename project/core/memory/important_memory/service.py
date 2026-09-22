"""
important_memory service - persistent important facts (tool behind tool_search).
Can store, delete, view all. Used for long-term key information.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

STORAGE = Path(__file__).resolve().parents[3] / "storage" / "database" / "important_memory.json"
MAX_ENTRIES = 200
MAX_CONTENT_LEN = 3000
MAX_KEY_LEN = 100


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


def store_fact(content: str, key: str | None = None, fact_id: str | None = None, tags: list[str] | None = None) -> dict:
    if not isinstance(content, str) or not content.strip():
        raise ValueError("content required")
    if len(content) > MAX_CONTENT_LEN:
        raise ValueError(f"content too long (max {MAX_CONTENT_LEN})")
    c = content.strip()
    if key is not None and not isinstance(key, str):
        raise TypeError("key must be string")
    if key is not None and len(key) > MAX_KEY_LEN:
        raise ValueError("key too long")
    k = key.strip() if key and key.strip() else None
    fid = fact_id.strip() if fact_id and fact_id.strip() else str(uuid.uuid4())[:8]
    if len(fid) > 64:
        raise ValueError("fact_id too long")
    items = _load()
    if any(x.get("id") == fid for x in items):
        raise ValueError(f"fact_id already exists: {fid}")
    if len(items) >= MAX_ENTRIES:
        raise RuntimeError(f"Too many facts (max {MAX_ENTRIES})")
    entry = {
        "id": fid,
        "key": k,
        "content": c,
        "tags": tags[:5] if isinstance(tags, list) else [],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    items.append(entry)
    _save(items)
    return entry


def update_fact(fact_id: str, content: str | None = None, key: str | None = None) -> dict:
    if not isinstance(fact_id, str) or not fact_id.strip():
        raise ValueError("fact_id required")
    fid = fact_id.strip()
    items = _load()
    idx = next((i for i, x in enumerate(items) if x.get("id") == fid), None)
    if idx is None:
        raise FileNotFoundError(f"fact_id not found: {fid}")
    if content is not None:
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content must be non-empty")
        if len(content) > MAX_CONTENT_LEN:
            raise ValueError("content too long")
        items[idx]["content"] = content.strip()
    if key is not None:
        if not isinstance(key, str):
            raise TypeError("key must be string")
        items[idx]["key"] = key.strip() if key.strip() else None
    items[idx]["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _save(items)
    return items[idx]


def delete_fact(fact_id: str) -> dict:
    if not isinstance(fact_id, str) or not fact_id.strip():
        raise ValueError("fact_id required")
    fid = fact_id.strip()
    items = _load()
    idx = next((i for i, x in enumerate(items) if x.get("id") == fid), None)
    if idx is None:
        raise FileNotFoundError(f"fact_id not found: {fid}")
    removed = items.pop(idx)
    _save(items)
    return removed


def get_fact(fact_id: str) -> dict | None:
    if not isinstance(fact_id, str):
        raise TypeError("fact_id must be string")
    fid = fact_id.strip()
    for x in _load():
        if x.get("id") == fid:
            return x
    return None


def list_facts() -> list[dict]:
    return _load()


def clear_all() -> int:
    n = len(_load())
    _save([])
    return n
