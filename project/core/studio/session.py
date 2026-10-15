"""
Studio branch session — one isolated session per studio (never the main chat).

Mirrors scheduler/trigger branch sessions: Data/database/studio_sessions/<id>.json,
last 100 messages, auto-deleted with the studio. The studio agent loop runs here
with the full main-chat context L1-L8 PLUS its own history.
"""
from __future__ import annotations

import json
from pathlib import Path

MAX_MSGS = 100


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def session_path(studio_id: str) -> Path:
    sid = str(studio_id or "").strip()
    if not sid or len(sid) > 32 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in sid):
        raise ValueError("Invalid studio_id for session")
    try:
        import sys as _sys
        proj = _project_root()
        if str(proj) not in _sys.path:
            _sys.path.insert(0, str(proj))
        from core.paths import data_root, ensure_x5agent
        ensure_x5agent()
        d = data_root() / "database" / "studio_sessions"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{sid}.json"
    except Exception:
        d = _project_root().parents[0] / "X5Agent" / "Data" / "database" / "studio_sessions"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{sid}.json"


def load_history(studio_id: str) -> list[dict]:
    try:
        p = session_path(studio_id)
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [m for m in data if isinstance(m, dict)][-MAX_MSGS:]
    except Exception:
        pass
    return []


def append_messages(studio_id: str, messages: list[dict]) -> None:
    try:
        import sys as _sys
        from pathlib import Path as _Path
        proj = _Path(__file__).resolve().parents[2]
        if str(proj) not in _sys.path:
            _sys.path.insert(0, str(proj))
        from core.studio.store import studio_lock
        with studio_lock():
            hist = load_history(studio_id)
            for m in messages:
                if isinstance(m, dict):
                    hist.append(m)
            session_path(studio_id).write_text(
                json.dumps(hist[-MAX_MSGS:], ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def delete_session(studio_id: str) -> None:
    try:
        p = session_path(studio_id)
        if p.is_file():
            p.unlink()
    except Exception:
        pass
