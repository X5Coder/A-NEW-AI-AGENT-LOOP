"""
todo_list - create and manage structured task plan.
Persists to storage/database/todos.json with bounded size.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from .events import format_event

MAX_TODOS = 100
MAX_TASK_LEN = 500
MAX_STEP_LEN = 300
MAX_STEPS = 20


def _validate_task(v: Any) -> str:
    if not isinstance(v, str):
        raise TypeError("task must be string")
    s = v.strip()
    if not s:
        raise ValueError("task must be non-empty")
    if len(s) > MAX_TASK_LEN:
        raise ValueError(f"task too long (max {MAX_TASK_LEN})")
    return s


def _validate_steps(v: Any) -> list[str] | None:
    if v is None:
        return None
    if not isinstance(v, list):
        raise TypeError("steps must be array<string>")
    if len(v) > MAX_STEPS:
        raise ValueError(f"steps too many (max {MAX_STEPS})")
    cleaned = []
    for s in v:
        if not isinstance(s, str):
            raise TypeError("each step must be string")
        t = s.strip()
        if not t:
            raise ValueError("step must be non-empty")
        if len(t) > MAX_STEP_LEN:
            raise ValueError(f"step too long (max {MAX_STEP_LEN})")
        cleaned.append(t)
    return cleaned


def _storage_path() -> Path:
    return Path(__file__).resolve().parents[3] / "storage" / "database" / "todos.json"


def _load_all() -> list[dict]:
    p = _storage_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_all(items: list[dict]) -> None:
    p = _storage_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # bounded
    if len(items) > MAX_TODOS:
        items = items[-MAX_TODOS:]
    p.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def execute(task: str, steps: list[str] | None = None) -> dict:
    t = _validate_task(task)
    s = _validate_steps(steps)
    event = format_event()
    items = _load_all()
    if len(items) >= MAX_TODOS:
        raise RuntimeError(f"Too many todos (max {MAX_TODOS}), please complete some first")
    entry = {
        "id": str(uuid.uuid4())[:8],
        "task": t,
        "steps": s or [],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "planned",
    }
    items.append(entry)
    _save_all(items)
    return {"event": event, "todo": entry, "total": len(items)}
