"""
Trigger tool - neutral for all plugins, manages event triggers.
Each trigger (filesystem_new_file, etc.) is stored with task + config, auto-creates Skill in skills/trigger_skills via internal LLM.
Triggers are one-time, concurrent without limit, die after execution and delete their skill.
"""
from __future__ import annotations

import json
import time
import uuid
import os
import re
from pathlib import Path
from typing import Any

from .events import format_event

MAX_TRIGGERS = 200
ALLOWED_ACTIONS = {"create", "list", "get", "delete", "update"}

def _validate_action(v: Any) -> str:
    if not isinstance(v, str):
        raise TypeError("action must be string")
    a = v.strip().lower()
    if a not in ALLOWED_ACTIONS:
        raise ValueError(f"action must be one of {ALLOWED_ACTIONS}")
    return a

def _legacy_path(name: str) -> Path:
    return Path(__file__).resolve().parents[3] / "storage" / "database" / name


def _data_path(name: str) -> Path:
    # Unified: writes ALWAYS to X5Agent/Data (zip backup). Fallback legacy only if resolver missing.
    try:
        import sys as _sys
        _proj = Path(__file__).resolve().parents[3]
        if str(_proj) not in _sys.path:
            _sys.path.insert(0, str(_proj))
        from core.paths import data_db_file
        return data_db_file(name)
    except Exception:
        return _legacy_path(name)


def _storage_path() -> Path:
    return _data_path("triggers.json")

def _load_triggers() -> list[dict]:
    for p in (_data_path("triggers.json"), _legacy_path("triggers.json")):
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            continue
    return []

def _save_triggers(triggers: list[dict]) -> None:
    p = _storage_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(triggers, ensure_ascii=False, indent=2), encoding="utf-8")

def _load_trigger_specs() -> dict[str, dict]:
    """Load all trigger specs by name for validation."""
    root = Path(__file__).resolve().parents[3] / "plugins"
    specs = {}
    if root.exists():
        for cat in root.iterdir():
            if not cat.is_dir():
                continue
            trig_dir = cat / "triggers"
            if not trig_dir.exists():
                continue
            for d in trig_dir.iterdir():
                if not d.is_dir():
                    continue
                spec_path = d / "spec.json"
                if spec_path.exists():
                    try:
                        spec = json.loads(spec_path.read_text(encoding="utf-8"))
                        specs[spec.get("name")] = spec
                    except Exception:
                        continue
    return specs

def _get_api_key() -> str | None:
    for k in ["OPENROUTER_API_KEY","OPENROUTER_KEY","OR_API_KEY"]:
        v = os.environ.get(k)
        if v and v.strip():
            return v.strip()
    return None

def _load_chat_snippet(limit: int = 30) -> str:
    legacy_db = Path(__file__).resolve().parents[3] / "storage" / "database"
    try:
        import sys as _sys2
        _proj2 = Path(__file__).resolve().parents[3]
        if str(_proj2) not in _sys2.path:
            _sys2.path.insert(0, str(_proj2))
        from core.paths import data_db_dir as _dd
        data_db = _dd()
    except Exception:
        data_db = legacy_db
    for db in (data_db, legacy_db):
        for cand in [db / "conversation.json", db / "chat.json"]:
            if not cand.exists():
                continue
            try:
                data = json.loads(cand.read_text(encoding="utf-8"))
                if isinstance(data, list) and data:
                    last = data[-limit:]
                    return "\n".join([f"{m.get('role')}: {str(m.get('content'))[:300]}" for m in last])
            except Exception:
                continue
    return ""

def _generate_trigger_skill(trigger_name: str, task: str, config: dict | None) -> str:
    """Internal LLM request using trigger_skill_system.md, raises with real error per situation."""
    system_path = Path(__file__).resolve().parents[3] / "prompts" / "trigger_skill_system.md"
    system_prompt = system_path.read_text(encoding="utf-8").strip() if system_path.exists() else "You are a trigger skill generator."
    # Find trigger spec for description
    specs = _load_trigger_specs()
    trig_desc = specs.get(trigger_name, {}).get("description", "")
    chat_snippet = _load_chat_snippet(limit=30)
    parts = [f"Trigger: {trigger_name} - {trig_desc}", f"Task to run when triggered: {task}"]
    if config:
        parts.append(f"Config: {json.dumps(config, ensure_ascii=False)}")
    if chat_snippet:
        parts.append(f"Chat context:\n{chat_snippet[:2000]}")
    parts.append("Generate skill markdown now.")
    user_prompt = "\n\n".join(parts)
    from core.lm.providers.openrouter.provider import chat as provider_chat
    api_key = _get_api_key()
    resp = provider_chat(
        messages=[{"role":"system","content": system_prompt},{"role":"user","content": user_prompt}],
        model=None,
        api_key=api_key,
        temperature=0.4,
        max_tokens=1500,
    )
    content = resp.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError(f"Trigger skill generation empty (model={resp.get('model_used')})")
    return content.strip()

def _save_trigger_skill(trigger_id: str, trigger_name: str, task: str, config: dict | None) -> tuple[str, str]:
    project_root = Path(__file__).resolve().parents[3]
    base_dir = project_root / "skills" / "trigger_skills"
    base_dir.mkdir(parents=True, exist_ok=True)
    sanitized = re.sub(r"\s+", "_", trigger_name.strip()[:20])
    sanitized = re.sub(r"[^a-zA-Z0-9_\-أ-ي]", "", sanitized)
    if not sanitized:
        sanitized = "trigger"
    safe = f"trigger_{trigger_id}_{sanitized}"
    if len(safe) > 60:
        safe = safe[:60]
    target = base_dir / safe
    if target.exists():
        safe = f"{safe}_{str(uuid.uuid4())[:4]}"
        target = base_dir / safe
    # Generate skill via internal LLM (no fallback, real error per situation)
    skill_content = _generate_trigger_skill(trigger_name, task, config)
    target.mkdir(parents=True, exist_ok=True)
    skill_data = {
        "name": safe,
        "description": task[:200],
        "content": skill_content,
        "instructions": skill_content,
        "trigger_id": trigger_id,
        "trigger_name": trigger_name,
        "original_task": task,
        "config": config or {},
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "trigger_skill": True,
    }
    (target / "skill.json").write_text(json.dumps(skill_data, ensure_ascii=False, indent=2), encoding="utf-8")
    (target / "README.md").write_text(f"# {safe}\n\n{skill_content}\n", encoding="utf-8")
    return safe, skill_content

def _delete_trigger_skill(skill_ref: str) -> bool:
    if not skill_ref:
        return False
    project_root = Path(__file__).resolve().parents[3]
    target = project_root / "skills" / "trigger_skills" / skill_ref
    try:
        target.resolve().relative_to((project_root / "skills" / "trigger_skills").resolve())
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

def execute(action: str, trigger_name: str | None = None, task: str | None = None, config: dict | None = None, trigger_id: str | None = None) -> dict:
    act = _validate_action(action)
    event = format_event(action=act, trigger_name=trigger_name or "", trigger_id=trigger_id or "")
    triggers = _load_triggers()

    if act == "list":
        return {"event": event, "action": act, "triggers": triggers[:50], "total": len(triggers)}

    if act == "create":
        if not isinstance(trigger_name, str) or not trigger_name.strip():
            raise ValueError("trigger_name required (from trigger_search)")
        if not isinstance(task, str) or not task.strip():
            raise ValueError("task required")
        if len(task) > 2000:
            raise ValueError("task too long")
        if len(triggers) >= MAX_TRIGGERS:
            raise RuntimeError(f"Too many triggers (max {MAX_TRIGGERS})")
        # Validate trigger exists
        specs = _load_trigger_specs()
        if trigger_name.strip() not in specs:
            raise ValueError(f"Unknown trigger: {trigger_name}. Use trigger_search to find available triggers.")
        # Config validation: must be dict if provided, neutral for all plugins
        cfg = config if isinstance(config, dict) else ({} if config is None else None)
        if cfg is None:
            raise TypeError("config must be object")
        # Basic size check
        if len(json.dumps(cfg, ensure_ascii=False)) > 2000:
            raise ValueError("config too large")
        new_id = str(uuid.uuid4())[:8]
        # Create skill via internal LLM (with whole chat + task)
        skill_ref, skill_content = _save_trigger_skill(new_id, trigger_name.strip(), task.strip(), cfg)
        entry = {
            "trigger_id": new_id,
            "trigger_name": trigger_name.strip(),
            "task": task.strip(),
            "config": cfg,
            "skill_ref": skill_ref,
            "skill_path": f"skills/trigger_skills/{skill_ref}",
            "skill_content_preview": skill_content[:200],
            "status": "active",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "run_count": 0,
        }
        triggers.append(entry)
        _save_triggers(triggers)
        # Start watcher for new trigger (single engine, continuous)
        try:
            from core.trigger.engine import check_and_start_new_trigger
            check_and_start_new_trigger(entry)
        except Exception:
            pass
        return {"event": event, "action": act, "trigger": entry, "skill_ref": skill_ref}

    # For get/delete/update need trigger_id
    if not trigger_id or not isinstance(trigger_id, str) or not trigger_id.strip():
        raise ValueError("trigger_id required for this action")
    tid = trigger_id.strip()
    idx = next((i for i, t in enumerate(triggers) if t.get("trigger_id") == tid), None)
    if idx is None:
        raise FileNotFoundError(f"trigger_id not found: {tid}")

    if act == "delete":
        removed = triggers.pop(idx)
        _save_triggers(triggers)
        skill_ref = removed.get("skill_ref")
        skill_deleted = _delete_trigger_skill(skill_ref) if skill_ref else False
        # Stop watcher if running
        try:
            from core.trigger.engine import _WATCHERS
            watcher = _WATCHERS.pop(removed.get("trigger_id"), None)
            if watcher:
                try:
                    watcher.stop()
                except Exception:
                    pass
        except Exception:
            pass
        return {"event": event, "action": act, "deleted": removed, "skill_deleted": skill_deleted}

    if act == "get":
        return {"event": event, "action": act, "trigger": triggers[idx]}

    if act == "update":
        # Allow updating task or config
        if task is not None:
            if not isinstance(task, str) or not task.strip():
                raise ValueError("task must be non-empty")
            triggers[idx]["task"] = task.strip()
        if config is not None:
            if not isinstance(config, dict):
                raise TypeError("config must be object")
            triggers[idx]["config"] = config
        triggers[idx]["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _save_triggers(triggers)
        return {"event": event, "action": act, "trigger": triggers[idx]}

    raise ValueError(f"unsupported action {act}")
