"""
Trigger Engine - single engine for all triggers, continuous via Watchdog for filesystem.
Each trigger is one-time, runs once and dies, at most MAX_ACTIVE_TRIGGERS concurrently.
When event fires, loads its skill from trigger_skills and executes isolated with full context (tools + memory + system prompt), then deletes skill and trigger.
"""
from __future__ import annotations

import json
import time
import threading
from pathlib import Path
from typing import Any

def _storage_path() -> Path:
    # Unified: writes ALWAYS to X5Agent/Data (zip backup). Fallback legacy only if resolver missing.
    try:
        import sys as _sys
        _proj = Path(__file__).resolve().parents[2]
        _root = _proj if _proj.name == "project" else Path(__file__).resolve().parents[3]
        if str(_root) not in _sys.path:
            _sys.path.insert(0, str(_root))
        from core.paths import data_db_file
        return data_db_file("triggers.json")
    except Exception:
        return Path(__file__).resolve().parents[2] / "storage" / "database" / "triggers.json"


STORAGE = _storage_path()
_LEGACY = Path(__file__).resolve().parents[2] / "storage" / "database" / "triggers.json"
TRIGGER_SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills" / "trigger_skills"

MAX_ACTIVE_TRIGGERS = 50  # Rule 74: bound concurrent watchers, never unbounded.

_LOCK = threading.Lock()
_WATCHERS: dict[str, Any] = {}  # trigger_id -> watcher


def _load_triggers() -> list[dict]:
    for cand in (STORAGE, _LEGACY):
        if not cand.exists():
            continue
        try:
            data = json.loads(cand.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            continue
    return []

def _save_triggers(triggers: list[dict]) -> None:
    STORAGE.parent.mkdir(parents=True, exist_ok=True)
    STORAGE.write_text(json.dumps(triggers, ensure_ascii=False, indent=2), encoding="utf-8")

def _load_skill_content(skill_ref: str) -> str | None:
    if not skill_ref:
        return None
    project_root = Path(__file__).resolve().parents[2]
    target = project_root / "skills" / "trigger_skills" / skill_ref / "skill.json"
    try:
        target.resolve().relative_to((project_root / "skills" / "trigger_skills").resolve())
    except ValueError:
        return None
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8")).get("instructions")
    except Exception:
        return None

def _delete_skill_and_trigger(trigger_id: str):
    triggers = _load_triggers()
    idx = next((i for i, t in enumerate(triggers) if t.get("trigger_id") == trigger_id), None)
    if idx is not None:
        skill_ref = triggers[idx].get("skill_ref")
        triggers.pop(idx)
        _save_triggers(triggers)
        if skill_ref:
            project_root = Path(__file__).resolve().parents[2]
            target = project_root / "skills" / "trigger_skills" / skill_ref
            try:
                import shutil
                if target.exists():
                    shutil.rmtree(str(target))
            except Exception:
                pass
        # Stop watcher if any
        watcher = _WATCHERS.pop(trigger_id, None)
        if watcher:
            try:
                watcher.stop()
            except Exception:
                pass

def _execute_trigger_skill(trigger: dict):
    """Execute skill for trigger in isolated mode, then delete trigger and skill."""
    trigger_id = trigger.get("trigger_id")
    skill_ref = trigger.get("skill_ref")
    skill_content = _load_skill_content(skill_ref) if skill_ref else None
    if not skill_content:
        skill_content = trigger.get("task") or ""
    # Get todo snapshot if any? For triggers, we should also handle todo, but triggers are simple
    # Execute via agent loop isolated
    try:
        from core.agent.loop.engine import run_loop
        # Use isolated with full memory and system prompt, no chat
        result = run_loop(user_input=skill_content, max_steps=80, verbose=False, isolated=True)
        print(f"[trigger engine] Trigger {trigger_id} executed: {result.get('final_answer','')[:100]}")
    except Exception as e:
        print(f"[trigger engine] Trigger {trigger_id} failed: {e}")
    finally:
        # Trigger dies after execution, delete skill and trigger
        _delete_skill_and_trigger(trigger_id)
        print(f"[trigger engine] Trigger {trigger_id} died and skill deleted")

def _start_filesystem_watch(trigger: dict):
    """Start watchdog for filesystem_new_file or filesystem_file_modified."""
    trigger_id = trigger.get("trigger_id")
    # Prevent duplicate watcher for same trigger (single engine, one-time)
    if trigger_id in _WATCHERS:
        return
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        print("[trigger engine] watchdog not installed, falling back to polling for filesystem")
        # Fallback to polling
        return _start_polling_watch(trigger)
    config = trigger.get("config") or {}
    watch_path = config.get("watch_path") or "storage/files/watch"
    pattern = config.get("pattern") or "*"
    project_root = Path(__file__).resolve().parents[2]
    watch_dir = (project_root / watch_path).resolve() if not Path(watch_path).is_absolute() else Path(watch_path).resolve()
    # Ensure watch dir exists
    watch_dir.mkdir(parents=True, exist_ok=True)

    class Handler(FileSystemEventHandler):
        def __init__(self, tid: str, tname: str):
            self.tid = tid
            self.tname = tname
            self._fired = False
        def on_created(self, event):
            if self.tname == "filesystem_new_file" and not event.is_directory:
                if _matches_pattern(event.src_path, pattern):
                    self._fire(event.src_path)
        def on_modified(self, event):
            if self.tname == "filesystem_file_modified" and not event.is_directory:
                if _matches_pattern(event.src_path, pattern):
                    self._fire(event.src_path)
        def _fire(self, src):
            if self._fired:
                return
            self._fired = True
            print(f"[trigger engine] Event {self.tname} for {src} -> executing trigger {self.tid}")
            # Run in thread
            threading.Thread(target=_execute_trigger_skill, args=(_load_triggers_dict(self.tid),), daemon=True).start()
            # Stop observer after one fire (one-time)
            try:
                observer = _WATCHERS.get(self.tid)
                if observer:
                    observer.stop()
            except Exception:
                pass

    def _matches_pattern(path: str, pat: str) -> bool:
        import fnmatch
        if pat in ("*", "*.*", ""):
            return True
        return fnmatch.fnmatch(Path(path).name, pat)

    def _load_triggers_dict(tid: str) -> dict:
        for t in _load_triggers():
            if t.get("trigger_id") == tid:
                return t
        return {"trigger_id": tid, "task": trigger.get("task"), "skill_ref": trigger.get("skill_ref")}

    handler = Handler(trigger_id, trigger.get("trigger_name"))
    observer = Observer()
    observer.schedule(handler, str(watch_dir), recursive=False)
    observer.start()
    _WATCHERS[trigger_id] = observer
    print(f"[trigger engine] Started watch for {trigger_id} ({trigger.get('trigger_name')}) on {watch_dir}")

def _start_polling_watch(trigger: dict):
    """Fallback polling for filesystem if watchdog not available."""
    def poll():
        import time as _time
        config = trigger.get("config") or {}
        watch_path = config.get("watch_path") or "storage/files/watch"
        pattern = config.get("pattern") or "*"
        project_root = Path(__file__).resolve().parents[2]
        watch_dir = (project_root / watch_path).resolve()
        seen = set()
        # Initial scan
        if watch_dir.exists():
            for f in watch_dir.iterdir():
                if f.is_file():
                    seen.add(f.name)
        while True:
            _time.sleep(5)
            # Check if trigger still exists (might have been deleted)
            triggers = _load_triggers()
            if not any(t.get("trigger_id") == trigger.get("trigger_id") for t in triggers):
                break
            if not watch_dir.exists():
                continue
            current = {f.name for f in watch_dir.iterdir() if f.is_file()}
            # Check for new files
            if trigger.get("trigger_name") == "filesystem_new_file":
                new_files = current - seen
                # Filter by pattern
                import fnmatch
                new_files = {f for f in new_files if fnmatch.fnmatch(f, pattern) or pattern in ("*", "*.*")}
                if new_files:
                    print(f"[trigger engine] Poll detected new file {new_files} for {trigger.get('trigger_id')}")
                    _execute_trigger_skill(trigger)
                    break
            elif trigger.get("trigger_name") == "filesystem_file_modified":
                # For modified, we check mtime
                # Simplified: if any file's mtime is recent
                for f in watch_dir.iterdir():
                    if f.is_file() and f.stat().st_mtime > time.time() - 6:
                        if f.name in current:
                            import fnmatch
                            if fnmatch.fnmatch(f.name, pattern) or pattern == "*":
                                print(f"[trigger engine] Poll detected modified {f.name}")
                                _execute_trigger_skill(trigger)
                                return
            seen = current
    threading.Thread(target=poll, daemon=True).start()

def start_all_triggers():
    """Start engine for all active triggers (called at startup)."""
    triggers = _load_triggers()
    for trig in triggers:
        if trig.get("status") == "active" or trig.get("status") is None:
            tname = trig.get("trigger_name")
            if tname in ("filesystem_new_file", "filesystem_file_modified"):
                _start_filesystem_watch(trig)
            else:
                print(f"[trigger engine] Unknown trigger {tname}, skipping")

def check_and_start_new_trigger(trigger: dict):
    """Called after trigger creation to start its watcher."""
    with _LOCK:
        if len(_WATCHERS) >= MAX_ACTIVE_TRIGGERS:
            print(f"[trigger engine] At cap ({MAX_ACTIVE_TRIGGERS}), new trigger not watched.")
            return
    tname = trigger.get("trigger_name")
    if tname in ("filesystem_new_file", "filesystem_file_modified"):
        _start_filesystem_watch(trigger)

def get_active_triggers_for_context() -> list[dict]:
    """Return triggers to be sent in context as metadata + skill name."""
    triggers = _load_triggers()
    result = []
    for t in triggers:
        result.append({
            "trigger_id": t.get("trigger_id"),
            "trigger_name": t.get("trigger_name"),
            "task": t.get("task")[:100] if t.get("task") else "",
            "skill_ref": t.get("skill_ref"),
            "skill_path": t.get("skill_path"),
            "config": t.get("config"),
        })
    return result
