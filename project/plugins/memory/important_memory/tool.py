"""
important_memory tool - discoverable behind tool_search.
Store/view/delete important facts.
Delegates to core/memory/important_memory/service.py
"""
from __future__ import annotations

from typing import Any

from .events import format_event

ALLOWED = {"store","update","delete","list","get","clear_all"}


def _validate_action(v: Any) -> str:
    if not isinstance(v, str):
        raise TypeError("action must be string")
    a = v.strip().lower()
    if a not in ALLOWED:
        raise ValueError(f"action must be one of {ALLOWED}")
    return a


def execute(action: str, content: str | None = None, key: str | None = None, fact_id: str | None = None) -> dict:
    act = _validate_action(action)
    event = format_event(action=act, key=key or "", fact_id=fact_id or "")
    from core.memory.important_memory.service import store_fact, update_fact, delete_fact, get_fact, list_facts, clear_all

    if act == "store":
        if not content or not isinstance(content, str) or not content.strip():
            raise ValueError("content required for store")
        entry = store_fact(content=content, key=key, fact_id=fact_id)
        return {"event": event, "action": act, "fact": entry}

    if act == "update":
        if not fact_id:
            raise ValueError("fact_id required for update")
        entry = update_fact(fact_id, content=content, key=key)
        return {"event": event, "action": act, "fact": entry}

    if act == "delete":
        if not fact_id:
            raise ValueError("fact_id required for delete")
        removed = delete_fact(fact_id)
        return {"event": event, "action": act, "deleted": removed}

    if act == "get":
        if not fact_id:
            raise ValueError("fact_id required for get")
        fact = get_fact(fact_id)
        if fact is None:
            raise FileNotFoundError(f"fact_id not found: {fact_id}")
        return {"event": event, "action": act, "fact": fact}

    if act == "list":
        items = list_facts()
        return {"event": event, "action": act, "facts": items, "count": len(items)}

    if act == "clear_all":
        n = clear_all()
        return {"event": event, "action": act, "cleared": n}

    raise ValueError(f"unknown action {act}")
