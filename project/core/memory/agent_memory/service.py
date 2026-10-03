"""
agent_memory - تخزين حالة الايجنت الداخلية (ليست أداة).
Similar to user_memory but separate ownership.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

MAX_ENTRIES = 200
MAX_KEY_LEN = 100
MAX_VALUE_LEN = 5000
def _storage_path() -> Path:
    # Unified: writes ALWAYS to X5Agent/Data (zip backup). Fallback legacy only if resolver missing.
    try:
        import sys as _sys
        _proj = Path(__file__).resolve().parents[3]
        if str(_proj) not in _sys.path:
            _sys.path.insert(0, str(_proj))
        from core.paths import data_db_file
        return data_db_file("agent_memory.json")
    except Exception:
        return Path(__file__).resolve().parents[3] / "storage" / "database" / "agent_memory.json"


STORAGE = _storage_path()
_LEGACY = Path(__file__).resolve().parents[3] / "storage" / "database" / "agent_memory.json"


def _load() -> dict:
    for cand in (STORAGE, _LEGACY):
        if not cand.exists():
            continue
        try:
            data = json.loads(cand.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return {}


def _save(data: dict) -> None:
    STORAGE.parent.mkdir(parents=True, exist_ok=True)
    if len(data) > MAX_ENTRIES:
        items = list(data.items())
        data = dict(items[-MAX_ENTRIES:])
    STORAGE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def set_value(key: str, value: Any) -> dict:
    if not isinstance(key, str):
        raise TypeError("key must be string")
    k = key.strip()
    if not k:
        raise ValueError("key required")
    if len(k) > MAX_KEY_LEN:
        raise ValueError(f"key too long")
    v_str = str(value) if not isinstance(value, str) else value
    if len(v_str) > MAX_VALUE_LEN:
        raise ValueError(f"value too long (max {MAX_VALUE_LEN})")
    data = _load()
    data[k] = {"value": v_str, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _save(data)
    return {"key": k, "value": v_str}


def get_value(key: str) -> str | None:
    if not isinstance(key, str):
        raise TypeError("key must be string")
    data = _load()
    e = data.get(key.strip())
    return e.get("value") if isinstance(e, dict) else (str(e) if e is not None else None)


def list_all() -> dict:
    return _load()


def delete_key(key: str) -> bool:
    if not isinstance(key, str):
        raise TypeError("key must be string")
    k = key.strip()
    data = _load()
    if k not in data:
        return False
    del data[k]
    _save(data)
    return True


def clear_all() -> int:
    n = len(_load())
    _save({})
    return n
