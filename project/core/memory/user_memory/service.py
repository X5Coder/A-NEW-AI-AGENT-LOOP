"""
user_memory - تخزين أي شيء عن اليوزر (ليست أداة، فقط خدمة داخلية).
Ownership: core/memory/user_memory, lifecycle: create->use->persist, bounded.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

MAX_ENTRIES = 200
MAX_KEY_LEN = 100
MAX_VALUE_LEN = 2000
def _storage_path() -> Path:
    # Unified: writes ALWAYS to X5Agent/Data (zip backup). Fallback legacy only if resolver missing.
    try:
        import sys as _sys
        _proj = Path(__file__).resolve().parents[3]
        if str(_proj) not in _sys.path:
            _sys.path.insert(0, str(_proj))
        from core.paths import data_db_file
        return data_db_file("user_memory.json")
    except Exception:
        return Path(__file__).resolve().parents[3] / "storage" / "database" / "user_memory.json"


STORAGE = _storage_path()
_LEGACY = Path(__file__).resolve().parents[3] / "storage" / "database" / "user_memory.json"


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
    # bounded: keep last MAX_ENTRIES keys
    if len(data) > MAX_ENTRIES:
        # keep most recent by value timestamp if present
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
        raise ValueError(f"key too long (max {MAX_KEY_LEN})")
    if ".." in k or "/" in k or "\\" in k:
        raise ValueError("Invalid key")
    # value is stringified bounded
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
    entry = data.get(key.strip())
    if entry is None:
        return None
    return entry.get("value") if isinstance(entry, dict) else str(entry)


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
    data = _load()
    n = len(data)
    _save({})
    return n
