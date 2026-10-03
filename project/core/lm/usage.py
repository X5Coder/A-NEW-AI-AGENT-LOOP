"""
LLM usage accounting - measured tokens/requests per model.

Single responsibility: append one entry per provider call and aggregate per day.
Everything here is MEASURED from real API responses (usage.prompt_tokens /
usage.completion_tokens) - never estimated. Storage: X5Agent/Data/database/usage_log.json
(single source of truth, inside zip backup). Bounded: max 5000 entries, older than
400 days pruned on write. All functions best-effort and never raise.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

LOG_NAME = "usage_log.json"
MAX_ENTRIES = 5000
MAX_AGE_DAYS = 400


def _log_path() -> Path:
    try:
        import sys as _sys
        _proj = Path(__file__).resolve().parents[2]
        # core/lm/usage.py -> parents[2] is project/
        _root = _proj if _proj.name == "project" else Path(__file__).resolve().parents[3]
        if str(_root) not in _sys.path:
            _sys.path.insert(0, str(_root))
        from core.paths import data_db_file
        return data_db_file(LOG_NAME)
    except Exception:
        return Path(__file__).resolve().parents[2] / "storage" / "database" / LOG_NAME


def _legacy_path() -> Path:
    return Path(__file__).resolve().parents[2] / "storage" / "database" / LOG_NAME


def _read_entries() -> list[dict]:
    import json as _json
    for cand in (_log_path(), _legacy_path()):
        try:
            if not cand.exists():
                continue
            data = _json.loads(cand.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [e for e in data if isinstance(e, dict)]
        except Exception:
            continue
    return []


def log_usage(model: str, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
    """Append one measured provider call. Never raises (accounting must not break chat)."""
    try:
        import json as _json
        mdl = str(model or "unknown")[:128]
        try:
            pt = max(0, int(prompt_tokens or 0))
        except Exception:
            pt = 0
        try:
            ct = max(0, int(completion_tokens or 0))
        except Exception:
            ct = 0
        path = _log_path()
        entries = _read_entries()
        entries.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "model": mdl,
            "prompt_tokens": pt,
            "completion_tokens": ct,
        })
        # Bound: prune older than MAX_AGE_DAYS, then cap count (keep newest).
        try:
            cutoff = time.time() - MAX_AGE_DAYS * 86400
            kept: list[dict] = []
            for e in entries:
                try:
                    ets = datetime.fromisoformat(str(e.get("ts", "")).replace("Z", "+00:00")).timestamp()
                except Exception:
                    ets = cutoff  # keep unparseable rather than dropping data
                if ets >= cutoff:
                    kept.append(e)
            entries = kept
        except Exception:
            pass
        if len(entries) > MAX_ENTRIES:
            entries = entries[-MAX_ENTRIES:]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def get_daily(days: int = 30) -> list[dict]:
    """
    Aggregate measured usage per UTC day, oldest->newest, exactly `days` rows.
    Row: {date, requests, tokens_in, tokens_out, models: {model: {requests, in, out}}}.
    """
    try:
        n = int(days)
    except Exception:
        n = 30
    n = max(1, min(400, n))
    today = datetime.now(timezone.utc).date()
    rows: list[dict] = []
    lookup: dict[str, dict] = {}
    for i in range(n - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        row = {"date": d, "requests": 0, "tokens_in": 0, "tokens_out": 0, "models": {}}
        rows.append(row)
        lookup[d] = row
    try:
        for e in _read_entries():
            try:
                dstr = datetime.fromisoformat(str(e.get("ts", "")).replace("Z", "+00:00")).date().isoformat()
            except Exception:
                continue
            row = lookup.get(dstr)
            if row is None:
                continue
            try:
                pt = max(0, int(e.get("prompt_tokens") or 0))
            except Exception:
                pt = 0
            try:
                ct = max(0, int(e.get("completion_tokens") or 0))
            except Exception:
                ct = 0
            mdl = str(e.get("model") or "unknown")[:128]
            row["requests"] += 1
            row["tokens_in"] += pt
            row["tokens_out"] += ct
            m = row["models"].get(mdl)
            if m is None:
                m = {"requests": 0, "in": 0, "out": 0}
                row["models"][mdl] = m
            m["requests"] += 1
            m["in"] += pt
            m["out"] += ct
    except Exception:
        pass
    return rows
