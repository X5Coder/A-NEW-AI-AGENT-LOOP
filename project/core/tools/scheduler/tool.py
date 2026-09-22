"""
scheduler tool - schedule and manage tasks.
Persistence: storage/database/scheduler.json, bounded jobs, validation per spec.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from .events import format_event

MAX_JOBS = 100
ALLOWED_ACTIONS = {"create", "list", "update", "delete", "pause", "resume"}


def _validate_action(v: Any) -> str:
    if not isinstance(v, str):
        raise TypeError("action must be string")
    a = v.strip().lower()
    if a not in ALLOWED_ACTIONS:
        raise ValueError(f"action must be one of {ALLOWED_ACTIONS}")
    return a


def _validate_job_id(v: Any, required: bool) -> str | None:
    if not required and v is None:
        return None
    if not isinstance(v, str):
        raise TypeError("job_id must be string")
    s = v.strip()
    if not s and required:
        raise ValueError("job_id required for this action")
    if s and len(s) > 64:
        raise ValueError("job_id too long")
    if s and not re.match(r"^[a-zA-Z0-9_-]+$", s):
        raise ValueError("job_id must be alphanumeric, dash or underscore")
    return s if s else None


def _storage_path() -> Path:
    return Path(__file__).resolve().parents[3] / "storage" / "database" / "scheduler.json"


def _load_jobs() -> list[dict]:
    p = _storage_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_jobs(jobs: list[dict]) -> None:
    p = _storage_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")


def execute(action: str, job_id: str | None = None, task: str | None = None, start_at: str | None = None, repeat: str | None = None, repeat_count: int | None = None, end_at: str | None = None, timezone: str | None = None) -> dict:
    act = _validate_action(action)
    # Validate job_id requirement
    needs_id = act in ("update", "delete", "pause", "resume")
    jid = _validate_job_id(job_id, required=needs_id)
    event = format_event(repeat=repeat, start_at=start_at, repeat_count=repeat_count, action=act)

    jobs = _load_jobs()

    if act == "list":
        return {"event": event, "action": act, "jobs": jobs[:50], "total": len(jobs)}

    if act == "create":
        if not isinstance(task, str) or not task.strip():
            raise ValueError("task is required when creating")
        if len(task) > 1000:
            raise ValueError("task too long (max 1000)")
        if len(jobs) >= MAX_JOBS:
            raise RuntimeError(f"Too many scheduled jobs (max {MAX_JOBS})")
        if repeat_count is not None and (not isinstance(repeat_count, int) or repeat_count < 1 or repeat_count > 1000):
            raise ValueError("repeat_count must be 1..1000")
        # validate start_at / repeat simple
        if start_at is not None and not isinstance(start_at, str):
            raise TypeError("start_at must be string")
        if repeat is not None and not isinstance(repeat, str):
            raise TypeError("repeat must be string")
        new_id = str(uuid.uuid4())[:8]
        entry = {
            "job_id": new_id,
            "task": task.strip(),
            "start_at": start_at or "now",
            "repeat": repeat,
            "repeat_count": repeat_count,
            "end_at": end_at,
            "timezone": timezone,
            "status": "active",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        jobs.append(entry)
        _save_jobs(jobs)
        return {"event": event, "action": act, "job": entry}

    # For update/delete/pause/resume
    if not jid:
        raise ValueError("job_id required")
    idx = next((i for i, j in enumerate(jobs) if j.get("job_id") == jid), None)
    if idx is None:
        raise FileNotFoundError(f"job_id not found: {jid}")

    if act == "delete":
        removed = jobs.pop(idx)
        _save_jobs(jobs)
        return {"event": event, "action": act, "deleted": removed}

    if act == "pause":
        jobs[idx]["status"] = "paused"
        _save_jobs(jobs)
        return {"event": event, "action": act, "job": jobs[idx]}

    if act == "resume":
        jobs[idx]["status"] = "active"
        _save_jobs(jobs)
        return {"event": event, "action": act, "job": jobs[idx]}

    if act == "update":
        # allow updating task, repeat, etc.
        if task is not None:
            if not isinstance(task, str) or not task.strip():
                raise ValueError("task must be non-empty string")
            jobs[idx]["task"] = task.strip()
        if start_at is not None:
            jobs[idx]["start_at"] = start_at
        if repeat is not None:
            jobs[idx]["repeat"] = repeat
        if repeat_count is not None:
            if not isinstance(repeat_count, int) or repeat_count < 1:
                raise ValueError("repeat_count invalid")
            jobs[idx]["repeat_count"] = repeat_count
        if end_at is not None:
            jobs[idx]["end_at"] = end_at
        if timezone is not None:
            jobs[idx]["timezone"] = timezone
        jobs[idx]["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _save_jobs(jobs)
        return {"event": event, "action": act, "job": jobs[idx]}

    raise ValueError(f"unsupported action: {act}")
