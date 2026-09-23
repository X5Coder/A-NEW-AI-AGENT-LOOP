"""
Scheduler Engine - runs every 60s, checks due jobs, executes them in isolated mode for large tasks.
Ownership: core/scheduler, lifecycle: main.py starts background thread or manual tick.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

STORAGE = Path(__file__).resolve().parents[2] / "storage" / "database" / "scheduler.json"


def _load_jobs() -> list[dict]:
    if not STORAGE.exists():
        return []
    try:
        return json.loads(STORAGE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_jobs(jobs: list[dict]) -> None:
    STORAGE.parent.mkdir(parents=True, exist_ok=True)
    STORAGE.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_iso(s: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _compute_next_after_repeat(job: dict, last_run: datetime) -> str | None:
    """Given job repeat, compute next_run after last_run."""
    repeat = (job.get("repeat") or "").strip().lower()
    import re
    # use helper from tool (duplicate logic but keep simple)
    now = datetime.now(timezone.utc)
    base = last_run
    if repeat.startswith("daily at"):
        m = re.match(r"daily at\s+(\d{1,2}):(\d{2})", repeat)
        if m:
            hh, mm = int(m.group(1)), int(m.group(2))
            # next day same time
            candidate = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
            # need to be after base (which is last_run), so add 1 day if candidate <= base
            if candidate <= base:
                candidate += timedelta(days=1)
            # ensure not in past vs now? already after base, but if base was yesterday and now is later, candidate may be in past? add days until future
            while candidate <= now:
                candidate += timedelta(days=1)
            return candidate.astimezone(timezone.utc).isoformat()
    if repeat.startswith("every "):
        m = re.match(r"every\s+(\d+)\s*(m|min|h|hour|d|day)", repeat)
        if m:
            val = int(m.group(1))
            unit = m.group(2)
            if unit.startswith("m"):
                return (base + timedelta(minutes=val)).astimezone(timezone.utc).isoformat()
            if unit.startswith("h"):
                return (base + timedelta(hours=val)).astimezone(timezone.utc).isoformat()
            if unit.startswith("d"):
                return (base + timedelta(days=val)).astimezone(timezone.utc).isoformat()
        if "day" in repeat:
            return (base + timedelta(days=1)).astimezone(timezone.utc).isoformat()
    if "daily" in repeat:
        return (base + timedelta(days=1)).astimezone(timezone.utc).isoformat()
    # one-time: no next
    return None


def _is_due(job: dict, now: datetime) -> bool:
    if job.get("status") != "active":
        return False
    nxt = job.get("next_run")
    if not nxt:
        return False
    dt = _parse_iso(nxt)
    if not dt:
        return False
    return now >= dt


def _load_scheduled_skill_content(skill_ref: str) -> str | None:
    """Load skill instructions from skills/scheduled_skills/<ref>/skill.json"""
    if not skill_ref:
        return None
    project_root = Path(__file__).resolve().parents[2]
    target = project_root / "skills" / "scheduled_skills" / skill_ref / "skill.json"
    try:
        target.resolve().relative_to((project_root / "skills" / "scheduled_skills").resolve())
    except ValueError:
        return None
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        return data.get("instructions") or data.get("content")
    except Exception:
        return None


def _build_isolated_context(job: dict) -> tuple[str, str]:
    """For scheduled skills, build isolated context without chat history."""
    bundle = job.get("task_bundle") or {}
    original = bundle.get("original_user_input") or job.get("task") or ""
    todo_snap = bundle.get("todo_snapshot")
    from datetime import datetime, timezone
    import platform
    from pathlib import Path
    now = datetime.now(timezone.utc).astimezone()
    env = f"[Environment]\n- DateTime: {now.strftime('%Y-%m-%d %H:%M:%S %Z')} (ISO: {now.isoformat()})\n- OS: {platform.system()} {platform.release()}\n- CWD: {Path.cwd()}"
    try:
        import json
        db = Path(__file__).resolve().parents[2] / "storage" / "database"
        imp = json.loads((db / "important_memory.json").read_text(encoding="utf-8")) if (db / "important_memory.json").exists() else []
        mem_block = "[Important Memory]\n" + "\n".join([f"  - {x.get('key')}: {x.get('content')[:120]}" for x in imp[-5:]]) if imp else "[Memories: empty]"
    except Exception:
        mem_block = ""
    todo_block = ""
    if todo_snap:
        steps = todo_snap.get("steps") or []
        todo_block = f"[Todo Snapshot - from scheduling time]\n- Task: {todo_snap.get('task')}\n- Steps:\n" + "\n".join([f"  {i+1}. {s}" for i, s in enumerate(steps)])
    skill_block = f"[Skill Ref: {bundle.get('skill_ref')}]" if bundle.get("skill_ref") else ""
    parts = [env, mem_block, todo_block, skill_block, f"[Scheduled Task - isolated execution]\n{original.strip()}"]
    parts = [p for p in parts if p]
    return original.strip(), "\n\n".join(parts)


import threading
_JOB_LOCK = threading.Lock()

def _execute_single_job(job: dict, now: datetime, api_key: str | None, model: str | None, verbose: bool) -> tuple[dict, dict]:
    """Execute one job, returns (updated_job, result). Thread-safe for read."""
    job_id = job.get("job_id")
    task = job.get("task") or ""
    bundle = job.get("task_bundle")
    context_mode = job.get("context_mode") or ("isolated" if job.get("is_large") else "light")
    if verbose:
        print(f"[scheduler] Executing due job {job_id} mode={context_mode} task='{task[:60]}...'")
    try:
        from core.agent.loop.engine import run_loop
        skill_ref = job.get("skill_ref")
        skill_content = _load_scheduled_skill_content(skill_ref) if skill_ref else None
        if skill_content:
            todo_snap = (bundle or {}).get("todo_snapshot")
            result = run_loop(user_input=skill_content, max_steps=80, model=model, api_key=api_key, verbose=False, isolated=True, todo_snapshot=todo_snap)
        elif context_mode == "isolated" and bundle:
            original = (bundle.get("original_user_input") or task).strip()
            todo_snap = bundle.get("todo_snapshot")
            result = run_loop(user_input=original, max_steps=80, model=model, api_key=api_key, verbose=False, isolated=True, todo_snapshot=todo_snap)
        else:
            result = run_loop(user_input=task, max_steps=30, model=model, api_key=api_key, verbose=False)
        success = True
        output = result.get("final_answer", "")[:500]
    except Exception as e:
        success = False
        output = f"Execution failed: {e}"
        if verbose:
            print(f"[scheduler] job {job_id} failed: {e}")
    updated = dict(job)
    updated["last_run"] = now.astimezone(timezone.utc).isoformat()
    updated["run_count"] = int(job.get("run_count", 0)) + 1
    updated["run_history"] = job.get("run_history", [])[-9:] + [{"at": updated["last_run"], "success": success, "output": output[:300]}]
    if job.get("repeat"):
        nxt = _compute_next_after_repeat(job, now)
        updated["next_run"] = nxt
        if not nxt:
            updated["status"] = "completed"
    else:
        updated["status"] = "completed"
        updated["next_run"] = None
    return updated, {"job_id": job_id, "success": success, "output": output, "next_run": updated.get("next_run")}


def check_and_execute_due_jobs(api_key: str | None = None, model: str | None = None, verbose: bool = True) -> list[dict]:
    """
    Check scheduler.json, execute due jobs via agent loop.
    Supports concurrent execution: multiple due jobs run in parallel via ThreadPool.
    Returns list of executed job results.
    """
    jobs = _load_jobs()
    if not jobs:
        return []
    now = datetime.now(timezone.utc)
    # Pre-filter due jobs and handle completed/limits synchronously
    due_indices: list[int] = []
    changed = False
    for idx, job in enumerate(jobs):
        if not _is_due(job, now):
            continue
        rc = job.get("repeat_count")
        if isinstance(rc, int) and job.get("run_count", 0) >= rc:
            jobs[idx]["status"] = "completed"
            changed = True
            continue
        if job.get("end_at"):
            end_dt = _parse_iso(job["end_at"])
            if end_dt and now > end_dt:
                jobs[idx]["status"] = "completed"
                changed = True
                continue
        due_indices.append(idx)

    if not due_indices:
        if changed:
            with _JOB_LOCK:
                _save_jobs(jobs)
        return []

    # Execute due jobs concurrently
    from concurrent.futures import ThreadPoolExecutor, as_completed
    executed: list[dict] = []
    # Use up to 5 concurrent workers (bounded)
    max_workers = min(5, len(due_indices))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {executor.submit(_execute_single_job, jobs[i], now, api_key, model, verbose): i for i in due_indices}
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                updated_job, result = future.result()
                with _JOB_LOCK:
                    jobs[idx] = updated_job
                executed.append(result)
                changed = True
            except Exception as e:
                print(f"[scheduler] concurrent job {jobs[idx].get('job_id')} exception: {e}")

    if changed:
        with _JOB_LOCK:
            _save_jobs(jobs)
    return executed


def start_background_loop(interval_seconds: int = 60, api_key: str | None = None, model: str | None = None):
    """Blocking background loop - call from main.py thread."""
    import threading

    def loop():
        while True:
            try:
                check_and_execute_due_jobs(api_key=api_key, model=model, verbose=True)
            except Exception as e:
                print(f"[scheduler engine] tick failed: {e}")
            time.sleep(interval_seconds)

    t = threading.Thread(target=loop, daemon=True, name="scheduler-engine")
    t.start()
    return t
