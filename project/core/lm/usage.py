"""
LLM usage accounting - measured tokens/requests per model.

Single responsibility: append one entry per provider call and aggregate per day.
Everything here is MEASURED from real API responses (usage.prompt_tokens /
usage.completion_tokens) - never estimated. Each entry also captures its OWN
price at call time (prompt_tokens x cached prompt price + completion_tokens x
cached completion price); totals are the SUM of those per-request captures,
never a re-pricing of aggregates. Storage: X5Agent/Data/database/usage_log.json
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


def _pricing_path() -> Path:
    try:
        import sys as _sys
        _proj = Path(__file__).resolve().parents[2]
        _root = _proj if _proj.name == "project" else Path(__file__).resolve().parents[3]
        if str(_root) not in _sys.path:
            _sys.path.insert(0, str(_root))
        from core.paths import data_db_file
        return data_db_file("model_pricing.json")
    except Exception:
        return Path(__file__).resolve().parents[2] / "storage" / "database" / "model_pricing.json"


def _legacy_pricing_path() -> Path:
    return Path(__file__).resolve().parents[2] / "storage" / "database" / "model_pricing.json"


def _cached_price(model: str, cache: dict | None = None) -> tuple[float | None, float | None]:
    """Per-token USD price for one model from the server's cached pricing table.

    Read-only (never fetches, never writes): the server refreshes
    model_pricing.json in the background. Unknown models yield (None, None) -
    their tokens stay real, their USD stays 0. Never raises.
    """
    try:
        name = str(model or "")
        if not name:
            return None, None
        if cache is not None and name in cache:
            return cache[name]
        import json as _json
        found_file = False
        for cand in (_pricing_path(), _legacy_pricing_path()):
            try:
                if not cand.exists():
                    continue
                found_file = True
                data = _json.loads(cand.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    break
                v = data.get(name)
                if v is None:
                    # Custom model names ("gpt-4o-mini") vs provider ids
                    # ("openai/gpt-4o-mini"): one deterministic suffix match
                    # (sorted keys, first wins) so more requests measure real USD.
                    try:
                        short = name.split("/")[-1].strip().lower()
                        if short:
                            for k in sorted(data):
                                if (isinstance(k, str)
                                        and k.split("/")[-1].strip().lower() == short
                                        and isinstance(data.get(k), (list, tuple))
                                        and len(data[k]) >= 2):
                                    v = data[k]
                                    break
                    except Exception:
                        v = None
                if isinstance(v, (list, tuple)) and len(v) >= 2:
                    pp, cp = float(v[0]), float(v[1])
                    out = (pp, cp) if pp >= 0 and cp >= 0 else (None, None)
                else:
                    out = (None, None)
                if cache is not None:
                    cache[name] = out
                return out
            except Exception:
                continue
        if cache is not None and found_file:
            cache[name] = (None, None)
    except Exception:
        pass
    return None, None


def _price_entry(prompt_tokens: int, completion_tokens: int,
                 model: str, cache: dict | None = None) -> float:
    """One request's own USD cost (0.0 when its model has no cached price)."""
    try:
        pp, cp = _cached_price(model, cache)
        if pp is None or cp is None:
            return 0.0
        return round(max(0, prompt_tokens) * pp + max(0, completion_tokens) * cp, 6)
    except Exception:
        return 0.0


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
        entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "model": mdl,
            "prompt_tokens": pt,
            "completion_tokens": ct,
        }
        # Each request captures its OWN price now (not re-priced later from
        # aggregates): unknown models simply store no cost (tokens stay real).
        try:
            pp, cp = _cached_price(mdl)
            if pp is not None and cp is not None:
                entry["cost"] = round(pt * pp + ct * cp, 6)
        except Exception:
            pass
        entries.append(entry)
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


def _stored_or_priced_cost(e: dict, pt: int, ct: int, mdl: str,
                           cache: dict | None = None) -> float:
    """One entry's USD cost: its captured price, else priced now for legacy rows."""
    try:
        raw = e.get("cost")
        if raw is not None:
            return max(0.0, float(raw))
    except Exception:
        pass
    return _price_entry(pt, ct, mdl, cache)


def get_daily(days: int = 30) -> list[dict]:
    """
    Aggregate measured usage per UTC day, oldest->newest, exactly `days` rows.
    Row: {date, requests, tokens_in, tokens_out, cost, models: {model: {requests, in, out}}}.
    `cost` is the SUM of each request's own captured price (legacy rows without
    one are priced with the cached table) - never a re-pricing of aggregates.
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
        row = {"date": d, "requests": 0, "tokens_in": 0, "tokens_out": 0,
               "cost": 0.0, "models": {}}
        rows.append(row)
        lookup[d] = row
    price_cache: dict = {}
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
            row["cost"] += _stored_or_priced_cost(e, pt, ct, mdl, price_cache)
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


def get_totals() -> dict:
    """Lifetime measured totals: {requests, tokens_in, tokens_out, cost}.

    `cost` is the SUM of every request's own captured price - the number the
    Balance card shows. Never raises.
    """
    totals = {"requests": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0}
    try:
        price_cache: dict = {}
        for e in _read_entries():
            try:
                pt = max(0, int(e.get("prompt_tokens") or 0))
            except Exception:
                pt = 0
            try:
                ct = max(0, int(e.get("completion_tokens") or 0))
            except Exception:
                ct = 0
            mdl = str(e.get("model") or "unknown")[:128]
            totals["requests"] += 1
            totals["tokens_in"] += pt
            totals["tokens_out"] += ct
            totals["cost"] += _stored_or_priced_cost(e, pt, ct, mdl, price_cache)
    except Exception:
        pass
    try:
        totals["cost"] = round(float(totals["cost"]), 4)
    except Exception:
        totals["cost"] = 0.0
    return totals
