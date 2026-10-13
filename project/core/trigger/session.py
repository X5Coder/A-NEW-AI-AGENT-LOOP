"""
Trigger sessions — one hidden separate chat branch per trigger (same shape as the main chat, same full context L1-L8 + own history, no UI).

Ownership: core/trigger. Every trigger execution is persisted here, NEVER
in the main conversation.json — the main chat stays clean, each trigger
owns a session nobody sees (no UI page; it dies with its trigger).

File: X5Agent/Data/database/trigger_sessions/<trigger_id>.json
Shape: [{id, role: user|assistant, content, ts}] bounded to last 20000 (practically unlimited, still bounded to protect memory/disk).
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

MAX_MESSAGES = 20000
CONTENT_LIMIT = 4000

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _sessions_dir() -> Path:
    try:
        import sys as _sys
        _proj = Path(__file__).resolve().parents[2]
        _root = _proj if _proj.name == "project" else Path(__file__).resolve().parents[3]
        if str(_root) not in _sys.path:
            _sys.path.insert(0, str(_root))
        from core.paths import data_db_dir as _dd
        d = _dd() / "trigger_sessions"
    except Exception:
        d = Path(__file__).resolve().parents[2] / "storage" / "database" / "trigger_sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _validate_trigger_id(trigger_id: str) -> str:
    if not isinstance(trigger_id, str) or not _ID_RE.match(trigger_id.strip()):
        raise ValueError("invalid trigger_id for session")
    return trigger_id.strip()


def session_path(trigger_id: str) -> Path:
    """Resolved session file, jailed inside trigger_sessions. Raises on bad id."""
    tid = _validate_trigger_id(trigger_id)
    base = _sessions_dir().resolve()
    cand = (base / f"{tid}.json").resolve()
    try:
        cand.relative_to(base)
    except ValueError:
        raise ValueError("invalid trigger_id for session")
    return cand


def load_session(trigger_id: str) -> list[dict]:
    """Session messages (oldest first). Empty list when none. Never raises."""
    try:
        p = session_path(trigger_id)
        if not p.exists():
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        return [m for m in data if isinstance(m, dict)][:MAX_MESSAGES]
    except Exception:
        return []


def append_session(trigger_id: str, role: str, content: str, extra: dict | None = None) -> tuple[str, str] | None:
    """Append one message, bounded. Returns (id, ts). Never raises (None on failure)."""
    try:
        tid = _validate_trigger_id(trigger_id)
        if role not in ("user", "assistant"):
            return None
        text = str(content or "")[:CONTENT_LIMIT]
        if not text.strip():
            return None
        msgs = load_session(tid)
        mid = uuid.uuid4().hex[:8]
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        entry: dict = {"id": mid, "role": role, "content": text, "ts": ts}
        if isinstance(extra, dict):
            for k in ("steps", "elapsed", "images", "audio", "stopped"):
                if k in extra:
                    entry[k] = extra[k]
        msgs.append(entry)
        if len(msgs) > MAX_MESSAGES:
            msgs = msgs[-MAX_MESSAGES:]
        p = session_path(tid)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(msgs, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
        return mid, ts
    except Exception:
        return None


def history_block(trigger_id: str, limit: int = 30) -> str:
    """Recent session messages formatted as an L6 context block. Empty when none."""
    try:
        msgs = load_session(trigger_id)[-max(1, min(int(limit), 30)):]
        lines = []
        for m in msgs:
            role = "user" if m.get("role") == "user" else "assistant"
            lines.append(f"{role}: {str(m.get('content') or '')[:500]}")
        return "[Trigger Session History - this trigger's own hidden branch - L6]\n" + "\n".join(lines) if lines else ""
    except Exception:
        return ""


def delete_session(trigger_id: str) -> bool:
    """Remove a trigger's session file (on trigger death/delete). Never raises."""
    try:
        p = session_path(trigger_id)
        if p.exists():
            p.unlink()
        return True
    except Exception:
        return False
