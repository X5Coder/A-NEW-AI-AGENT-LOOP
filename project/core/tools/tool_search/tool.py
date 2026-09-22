"""
tool_search - search available tools by capability via embedding.
Uses embedding store at plugins/tool-discovery/search/store.py
Automatically triggers embedding creation and change detection on first call.
"""
from __future__ import annotations

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


def _get_search_fn():
    """Load search function from plugins/tool-discovery/search/store.py via importlib (dash in path)."""
    import importlib.util
    from pathlib import Path
    store_path = Path(__file__).resolve().parents[3] / "plugins" / "tool-discovery" / "search" / "store.py"
    spec = importlib.util.spec_from_file_location("tool_discovery_store", str(store_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load store module from {store_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
    return mod.search  # type: ignore


def execute(query: str) -> dict:
    q = _validate_query(query)
    event = format_event()
    search = _get_search_fn()
    # Do semantic search - this will auto-create embeddings if needed and detect changes
    try:
        results = search(q, top_k=5)
    except FileNotFoundError as e:
        raise FileNotFoundError(str(e)) from e
    except Exception as e:
        raise RuntimeError(f"tool_search failed: {e}") from e

    # Return full specifications for use as requested
    return {"event": event, "query": q, "results": results, "count": len(results)}
