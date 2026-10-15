"""
studio_read — read-only view of ANY slide in this studio session.

One of the 3 studio-session tools (studio_page approve + studio_read +
http_request). The model calls it to reuse patterns across slides
(consistency); it can never write. Writes on the tasked slide go
through studio_page (deliver/patch/test) only.
"""
from __future__ import annotations

import sys
from pathlib import Path

from .events import format_event  # noqa: F401


def _bootstrap() -> Path:
    proj = Path(__file__).resolve().parents[3]
    if str(proj) not in sys.path:
        sys.path.insert(0, str(proj))
    return proj


def execute(studio_id: str = "", page_no: int = 0, **_) -> dict:
    _bootstrap()
    from core.studio import store as _store
    sid = str(studio_id or "").strip()
    if not sid:
        raise ValueError("studio_id is required (e.g. pdf_1)")
    try:
        _store.get_studio(sid)  # validates the studio exists (unknown -> 404-style error)
        data = _store.read_page_code(sid, page_no)
    except (FileNotFoundError, ValueError) as e:
        return {"success": False, "studio_id": sid, "error": str(e)[:300]}
    return {"success": True, "error": "", **data}
