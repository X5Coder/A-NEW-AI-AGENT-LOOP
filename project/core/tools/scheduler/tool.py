"""
scheduler tool - schedule and manage tasks with practical scheduled_skills.
Every scheduled job (small or large) auto-creates a Skill in skills/scheduled_skills via internal LLM request
using scheduler_skill_system.md, and every execution sends the Skill as task (isolated, no chat).
If job deleted, its Skill auto-deleted.
"""
from __future__ import annotations

import json
import re
import time
import uuid
import os
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


def _load_todos() -> list[dict]:
    p = Path(__file__).resolve().parents[3] / "storage" / "database" / "todos.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _find_recent_todo_for_task(task: str) -> dict | None:
    todos = _load_todos()
    if not todos:
        return None
    task_low = task.lower()
    for t in reversed(todos[-5:]):
        t_task = str(t.get("task","")).lower()
        if t_task in task_low or task_low in t_task:
            return t
    last = todos[-1]
    if len(task) > 80 and isinstance(last.get("steps"), list) and last.get("steps"):
        return last
    return None


def _load_chat_snippet(limit: int = 30) -> str:
    """Collect last chat messages as context for skill generation (all details from chat)."""
    db = Path(__file__).resolve().parents[3] / "storage" / "database"
    for cand in [db / "conversation.json", db / "chat.json"]:
        if not cand.exists():
            continue
        try:
            data = json.loads(cand.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                last = data[-limit:]
                lines = []
                for m in last:
                    role = m.get("role","user")
                    content = str(m.get("content",""))[:300]
                    lines.append(f"{role}: {content}")
                return "\n".join(lines)
        except Exception:
            continue
    return ""


def _get_api_key() -> str | None:
    for k in ["OPENROUTER_API_KEY", "OPENROUTER_KEY", "OR_API_KEY"]:
        v = os.environ.get(k)
        if v and v.strip():
            return v.strip()
    return None


def _generate_skill_content_via_llm(task: str, todo_snapshot: dict | None, chat_snippet: str) -> str:
    """Internal LLM request using scheduler_skill_system.md. Returns skill markdown or raises with actual error (per situation)."""
    system_path = Path(__file__).resolve().parents[2] / "agent" / "prompts" / "scheduler_skill_system.md"
    system_prompt = ""
    if system_path.exists():
        try:
            system_prompt = system_path.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    if not system_prompt:
        system_prompt = "You are a skill generator for scheduled tasks. Create a concise executable skill from the task."

    parts = [f"Task to schedule: {task}"]
    if todo_snapshot:
        steps = todo_snapshot.get("steps") or []
        parts.append(f"Todo plan: {todo_snapshot.get('task')} - Steps: {'; '.join(steps)}")
    if chat_snippet:
        parts.append(f"Chat context (last messages with all details):\n{chat_snippet[:2000]}")
    parts.append("Generate the skill markdown now.")
    user_prompt = "\n\n".join(parts)

    # Let provider handle api_key resolution and raise appropriate error per situation (missing key, network, 429, etc.)
    from core.lm.providers.openrouter.provider import chat as provider_chat
    api_key = _get_api_key()  # may be None - provider will raise with correct message
    resp = provider_chat(
        messages=[{"role":"system","content": system_prompt},{"role":"user","content": user_prompt}],
        model=None,
        api_key=api_key,
        temperature=0.4,
        max_tokens=1500,
    )
    content = resp.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError(f"Skill generation returned empty content (model={resp.get('model_used')}, finish_reason={resp.get('finish_reason')})")
    return content.strip()


def _save_scheduled_skill(job_id: str, task: str, todo_snapshot: dict | None) -> tuple[str, str]:
    """Create skill under skills/scheduled_skills/<safe_name> via internal LLM request.
    On any failure, the actual provider error is propagated to the original workflow (no fixed error type).
    Returns (safe_name, skill_content) on success.
    """
    project_root = Path(__file__).resolve().parents[3]
    base_dir = project_root / "skills" / "scheduled_skills"
    base_dir.mkdir(parents=True, exist_ok=True)
    import re as _re
    sanitized = _re.sub(r"\s+", "_", task.strip()[:20])
    sanitized = _re.sub(r"[^a-zA-Z0-9_\-أ-ي]", "", sanitized)
    if not sanitized:
        sanitized = "task"
    safe = f"scheduled_{job_id}_{sanitized}"
    if len(safe) > 60:
        safe = safe[:60]
    target = base_dir / safe
    if target.exists():
        safe = f"{safe}_{str(uuid.uuid4())[:4]}"
        target = base_dir / safe

    chat_snippet = _load_chat_snippet(limit=30)
    # This will raise with the real provider error per situation (missing key, timeout, 429, empty content, etc.)
    # No fallback - error is returned to original workflow
    llm_content = _generate_skill_content_via_llm(task, todo_snapshot, chat_snippet)

    target.mkdir(parents=True, exist_ok=True)
    skill_data = {
        "name": safe,
        "description": task[:200],
        "content": llm_content,
        "instructions": llm_content,
        "scheduled_job_id": job_id,
        "original_task": task,
        "todo_snapshot": todo_snapshot,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "scheduled": True,
    }
    (target / "skill.json").write_text(json.dumps(skill_data, ensure_ascii=False, indent=2), encoding="utf-8")
    (target / "README.md").write_text(f"# {safe}\n\n{llm_content}\n", encoding="utf-8")
    return safe, llm_content


def _delete_scheduled_skill(skill_ref: str) -> bool:
    if not skill_ref:
        return False
    # skill_ref is safe name, path is skills/scheduled_skills/<safe>
    project_root = Path(__file__).resolve().parents[3]
    target = project_root / "skills" / "scheduled_skills" / skill_ref
    # Validate inside scheduled_skills
    try:
        target.resolve().relative_to((project_root / "skills" / "scheduled_skills").resolve())
    except ValueError:
        return False
    if not target.exists():
        return False
    import shutil
    try:
        shutil.rmtree(str(target))
        return True
    except Exception:
        return False


def _parse_next_run(start_at: str | None, repeat: str | None, timezone_str: str | None = None) -> str | None:
    from datetime import datetime, timedelta, timezone
    import re
    try:
        now = datetime.now(timezone.utc).astimezone()
        if not start_at or start_at.strip().lower() == "now":
            base = now
        elif start_at.strip().lower().startswith("in "):
            m = re.match(r"in\s+(\d+)\s*(m|min|minute|h|hour|d|day)", start_at.strip().lower())
            if m:
                val = int(m.group(1))
                unit = m.group(2)
                if unit.startswith("m"):
                    base = now + timedelta(minutes=val)
                elif unit.startswith("h"):
                    base = now + timedelta(hours=val)
                else:
                    base = now + timedelta(days=val)
            else:
                base = now
        elif "T" in start_at and len(start_at) > 10:
            try:
                base = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
                if base.tzinfo is None:
                    base = base.replace(tzinfo=now.tzinfo)
            except Exception:
                base = now
        else:
            base = now
        if repeat:
            r = repeat.strip().lower()
            if r.startswith("daily at"):
                m = re.match(r"daily at\s+(\d{1,2}):(\d{2})", r)
                if m:
                    hh, mm = int(m.group(1)), int(m.group(2))
                    candidate = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
                    if candidate <= now:
                        candidate += timedelta(days=1)
                    return candidate.astimezone(timezone.utc).isoformat()
            elif r.startswith("every "):
                m = re.match(r"every\s+(\d+)\s*(m|min|h|hour|d|day)", r)
                if m:
                    return base.astimezone(timezone.utc).isoformat()
                if "day" in r:
                    candidate = now.replace(hour=8, minute=0, second=0, microsecond=0)
                    if candidate <= now:
                        candidate += timedelta(days=1)
                    return candidate.astimezone(timezone.utc).isoformat()
            if "daily" in r:
                candidate = now.replace(hour=8, minute=0, second=0, microsecond=0)
                if candidate <= now:
                    candidate += timedelta(days=1)
                return candidate.astimezone(timezone.utc).isoformat()
        return base.astimezone(timezone.utc).isoformat()
    except Exception:
        return None


def execute(action: str, job_id: str | None = None, task: str | None = None, start_at: str | None = None, repeat: str | None = None, repeat_count: int | None = None, end_at: str | None = None, timezone: str | None = None) -> dict:
    act = _validate_action(action)
    needs_id = act in ("update", "delete", "pause", "resume")
    jid = _validate_job_id(job_id, required=needs_id)
    event = format_event(repeat=repeat, start_at=start_at, repeat_count=repeat_count, action=act)
    jobs = _load_jobs()

    if act == "list":
        return {"event": event, "action": act, "jobs": jobs[:50], "total": len(jobs)}

    if act == "create":
        if not isinstance(task, str) or not task.strip():
            raise ValueError("task is required when creating")
        if len(task) > 2000:
            raise ValueError("task too long (max 2000)")
        if len(jobs) >= MAX_JOBS:
            raise RuntimeError(f"Too many scheduled jobs (max {MAX_JOBS})")
        if repeat_count is not None and (not isinstance(repeat_count, int) or repeat_count < 1 or repeat_count > 1000):
            raise ValueError("repeat_count must be 1..1000")
        if start_at is not None and not isinstance(start_at, str):
            raise TypeError("start_at must be string")
        if repeat is not None and not isinstance(repeat, str):
            raise TypeError("repeat must be string")
        new_id = str(uuid.uuid4())[:8]
        # Practical: for ANY task (small or large) create a scheduled_skill with all details from chat
        todo_snap = _find_recent_todo_for_task(task.strip())
        # Always create scheduled skill (most reliable) via internal LLM request
        skill_ref, skill_content = _save_scheduled_skill(new_id, task.strip(), todo_snap)
        next_run = _parse_next_run(start_at or "now", repeat, timezone)
        entry = {
            "job_id": new_id,
            "task": task.strip(),
            "start_at": start_at or "now",
            "repeat": repeat,
            "repeat_count": repeat_count,
            "end_at": end_at,
            "timezone": timezone or "Africa/Cairo",
            "status": "active",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "next_run": next_run,
            "last_run": None,
            "run_count": 0,
            "run_history": [],
            "is_large": bool(todo_snap and len(todo_snap.get("steps",[]))>1),
            "context_mode": "isolated",  # always isolated for scheduled_skills
            "task_bundle": {
                "original_user_input": task.strip(),
                "todo_snapshot": todo_snap,
                "is_large": bool(todo_snap),
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            "skill_ref": skill_ref,
            "skill_content_preview": skill_content[:200],
            "skill_path": f"skills/scheduled_skills/{skill_ref}",
        }
        jobs.append(entry)
        _save_jobs(jobs)
        return {"event": event, "action": act, "job": entry, "skill_ref": skill_ref, "skill_created": True}

    if not jid:
        raise ValueError("job_id required")
    idx = next((i for i, j in enumerate(jobs) if j.get("job_id") == jid), None)
    if idx is None:
        raise FileNotFoundError(f"job_id not found: {jid}")

    if act == "delete":
        removed = jobs.pop(idx)
        _save_jobs(jobs)
        # Auto-delete scheduled skill
        skill_ref = removed.get("skill_ref")
        skill_deleted = False
        if skill_ref:
            skill_deleted = _delete_scheduled_skill(skill_ref)
        return {"event": event, "action": act, "deleted": removed, "skill_deleted": skill_deleted, "skill_ref": skill_ref}

    if act == "pause":
        jobs[idx]["status"] = "paused"
        _save_jobs(jobs)
        return {"event": event, "action": act, "job": jobs[idx]}

    if act == "resume":
        jobs[idx]["status"] = "active"
        _save_jobs(jobs)
        return {"event": event, "action": act, "job": jobs[idx]}

    if act == "update":
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
        # recompute next_run if timing changed
        if start_at is not None or repeat is not None:
            jobs[idx]["next_run"] = _parse_next_run(jobs[idx].get("start_at"), jobs[idx].get("repeat"), jobs[idx].get("timezone"))
        _save_jobs(jobs)
        return {"event": event, "action": act, "job": jobs[idx]}

    raise ValueError(f"unsupported action: {act}")
