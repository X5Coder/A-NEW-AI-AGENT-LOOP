"""
trigger_search - search available triggers by capability via embedding.
Only searches triggers (plugins/*/triggers/*/spec.json), not tools.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

def _get_search_fn():
    store_path = Path(__file__).resolve().parents[3] / "plugins" / "trigger-discovery" / "search" / "store.py"
    spec = importlib.util.spec_from_file_location("trigger_discovery_store", str(store_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load trigger store from {store_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.search

def _validate_query(q):
    if not isinstance(q, str):
        raise TypeError("query must be string")
    s = q.strip()
    if not s:
        raise ValueError("query must be non-empty")
    if len(s) > 2000:
        raise ValueError("query too long")
    return s

def execute(query: str, top_k=None) -> dict:
    from .events import format_event
    q = _validate_query(query)
    if top_k is None:
        k = 3
    elif isinstance(top_k, bool) or not isinstance(top_k, int):
        raise TypeError("top_k must be integer")
    elif top_k < 1 or top_k > 20:
        raise ValueError("top_k must be 1..20")
    else:
        k = top_k
    event = format_event()
    search = _get_search_fn()
    try:
        results = search(q, top_k=k)
    except FileNotFoundError as e:
        raise FileNotFoundError(str(e)) from e
    except Exception as e:
        raise RuntimeError(f"trigger_search failed: {e}") from e
    return {"event": event, "query": q, "results": results, "count": len(results)}
