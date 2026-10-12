"""
Scheduler sessions — one separate chat branch per scheduled job (same shape as the main chat: Working block before the reply, same full context L1-L8 + branch history).

Ownership: core/scheduler. Every scheduled execution (and every user
follow-up from the run page) is persisted here, NEVER in the main
conversation.json — the main chat stays clean, each task owns its session.

File: X5Agent/Data/database/scheduler_sessions/<job_id>.json
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

_JOB_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _sessions_dir() -> Path:
    try:
        import sys as _sys
        _proj = Path(__file__).resolve().parents[2]
        _root = _proj if _proj.name == "project" else Path(__file__).resolve().parents[3]
        if str(_root) not in _sys.path:
            _sys.path.insert(0, str(_root))
        from core.paths import data_db_dir as _dd
        d = _dd() / "scheduler_sessions"
    except Exception:
        d = Path(__file__).resolve().parents[2] / "storage" / "database" / "scheduler_sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _validate_job_id(job_id: str) -> str:
    if not isinstance(job_id, str) or not _JOB_RE.match(job_id.strip()):
        raise ValueError("invalid job_id for session")
    return job_id.strip()


def session_path(job_id: str) -> Path:
    """Resolved session file, jailed inside scheduler_sessions. Raises on bad id."""
    jid = _validate_job_id(job_id)
    base = _sessions_dir().resolve()
    cand = (base / f"{jid}.json").resolve()
    try:
        cand.relative_to(base)
    except ValueError:
        raise ValueError("invalid job_id for session")
    return cand


def load_session(job_id: str) -> list[dict]:
    """Session messages (oldest first). Empty list when none. Never raises."""
    try:
        p = session_path(job_id)
        if not p.exists():
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        return [m for m in data if isinstance(m, dict)][:MAX_MESSAGES]
    except Exception:
        return []


def append_session(job_id: str, role: str, content: str, extra: dict | None = None) -> tuple[str, str] | None:
    """Append one message, bounded. Returns (id, ts). Never raises (None on failure)."""
    try:
        jid = _validate_job_id(job_id)
        if role not in ("user", "assistant"):
            return None
        text = str(content or "")[:CONTENT_LIMIT]
        if not text.strip():
            return None
        msgs = load_session(jid)
        mid = uuid.uuid4().hex[:8]
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        entry: dict = {"id": mid, "role": role, "content": text, "ts": ts}
        if isinstance(extra, dict):
            for k in ("steps", "elapsed", "images", "audio"):
                if k in extra:
                    entry[k] = extra[k]
        msgs.append(entry)
        if len(msgs) > MAX_MESSAGES:
            msgs = msgs[-MAX_MESSAGES:]
        p = session_path(jid)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(msgs, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
        return mid, ts
    except Exception:
        return None


def history_block(job_id: str, limit: int = 30) -> str:
    """Recent session messages formatted as an L6 context block. Empty when none."""
    try:
        msgs = load_session(job_id)[-max(1, min(int(limit), 30)):]
        lines = []
        for m in msgs:
            role = "user" if m.get("role") == "user" else "assistant"
            lines.append(f"{role}: {str(m.get('content') or '')[:500]}")
        return "[Scheduled Session History - this task's own branch - L6]\n" + "\n".join(lines) if lines else ""
    except Exception:
        return ""


def delete_session(job_id: str) -> bool:
    """Remove a job's session file (on job delete). Never raises."""
    try:
        p = session_path(job_id)
        if p.exists():
            p.unlink()
        return True
    except Exception:
        return False


def update_message(job_id: str, mid: str, content: str) -> bool:
    """Edit one session message in place (copy of chat edit, branch-local)."""
    try:
        jid = _validate_job_id(job_id)
        key = str(mid or "").strip()[:16]
        text = str(content or "").strip()[:CONTENT_LIMIT]
        if not key or not text:
            return False
        msgs = load_session(jid)
        hit = next((m for m in msgs if str(m.get("id") or "")[:16] == key), None)
        if hit is None:
            return False
        hit["content"] = text
        p = session_path(jid)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(msgs, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
        return True
    except Exception:
        return False


def delete_message(job_id: str, mid: str) -> bool:
    """Delete one session message by id (branch-local). Never raises."""
    try:
        jid = _validate_job_id(job_id)
        key = str(mid or "").strip()[:16]
        if not key:
            return False
        msgs = load_session(jid)
        kept = [m for m in msgs if str(m.get("id") or "")[:16] != key]
        if len(kept) == len(msgs):
            return False
        p = session_path(jid)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
        return True
    except Exception:
        return False


def prune_below(job_id: str, anchor_mid: str) -> int:
    """Delete every session message below the anchor (regenerate path)."""
    try:
        jid = _validate_job_id(job_id)
        key = str(anchor_mid or "").strip()[:16]
        if not key:
            raise ValueError("anchor id required")
        msgs = load_session(jid)
        idx = next((i for i, m in enumerate(msgs) if str(m.get("id") or "")[:16] == key), None)
        if idx is None:
            raise FileNotFoundError("anchor message not found")
        removed = len(msgs) - (idx + 1)
        kept = msgs[: idx + 1]
        p = session_path(jid)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
        return max(0, removed)
    except (ValueError, FileNotFoundError):
        raise
    except Exception:
        return 0
