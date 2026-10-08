"""Gateway config store — Data/database/gateway.json is authoritative.

Owns: validated WhatsApp + Telegram channel settings. Secrets (bot token)
are stored locally only (Data is gitignored) and never returned in full —
reads expose has_token only. Never raises; returns (ok, payload|error).
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.paths import read_db_json, write_db_json

_FILENAME = "gateway.json"
_MAX_NUMBERS = 50
_MAX_CHATS = 20
_MAX_LIDS = 50


def _digits(v: object, max_len: int = 15) -> str:
    return "".join(c for c in str(v or "") if c.isdigit())[:max_len]


def _lid(v: object) -> str:
    """Validate WhatsApp's opaque LID local part; it is not a phone number."""
    value = str(v or "").strip()
    return value if value.isdigit() and 7 <= len(value) <= 30 else ""


def _lid_map(values: object) -> dict[str, str]:
    """Validated phone -> LID cache. It stays server-side only."""
    out: dict[str, str] = {}
    if not isinstance(values, dict):
        return out
    for phone, lid_value in values.items():
        number, lid = _digits(phone), _lid(lid_value)
        if 7 <= len(number) <= 15 and lid and len(out) < _MAX_LIDS:
            out[number] = lid
    return out


def default_config() -> dict:
    return {
        "whatsapp": {
            "agent_number": "", "mode": "self", "allowed": [],
            "read_receipts": False, "typing_indicator": True, "reply_to_message": False,
            "lid_map": {},
        },
        "telegram": {"username": "", "chat_ids": []},
    }


def _has_token(cfg: dict) -> bool:
    try:
        return bool(cfg.get("_telegram_token"))
    except Exception:
        return False


def read_config() -> dict:
    """Full config including secrets (server-side only, never sent raw)."""
    try:
        raw = read_db_json(_FILENAME, None)
        if not isinstance(raw, dict):
            return default_config()
        cfg = default_config()
        wa = raw.get("whatsapp") if isinstance(raw.get("whatsapp"), dict) else {}
        tg = raw.get("telegram") if isinstance(raw.get("telegram"), dict) else {}
        if isinstance(wa.get("agent_number"), str):
            cfg["whatsapp"]["agent_number"] = _digits(wa["agent_number"])
        if wa.get("mode") in ("self", "selected"):
            cfg["whatsapp"]["mode"] = wa["mode"]
        if isinstance(wa.get("allowed"), list):
            nums = [_digits(n) for n in wa["allowed"] if _digits(n)]
            seen: list[str] = []
            for n in nums:
                if 7 <= len(n) <= 15 and n not in seen:
                    seen.append(n)
            cfg["whatsapp"]["allowed"] = seen[:_MAX_NUMBERS]
        cfg["whatsapp"]["lid_map"] = _lid_map(wa.get("lid_map"))
        for name in ("read_receipts", "typing_indicator", "reply_to_message"):
            if isinstance(wa.get(name), bool):
                cfg["whatsapp"][name] = wa[name]
        if isinstance(tg.get("username"), str):
            cfg["telegram"]["username"] = tg["username"].strip().lstrip("@")[:64]
        if isinstance(tg.get("chat_ids"), list):
            ids = [str(c).strip()[:32] for c in tg["chat_ids"]]
            ids = [c for c in ids if c and (c.lstrip("-").isdigit())]
            cfg["telegram"]["chat_ids"] = ids[:_MAX_CHATS]
        if isinstance(raw.get("_telegram_token"), str) and raw["_telegram_token"]:
            cfg["_telegram_token"] = raw["_telegram_token"][:160]
        return cfg
    except Exception:
        return default_config()


def public_config(live_whatsapp: dict | None = None) -> dict:
    """Masked config safe for the browser (token never leaves the server)."""
    cfg = read_config()
    return {
        "whatsapp": {
            "agent_number": cfg["whatsapp"]["agent_number"],
            "mode": cfg["whatsapp"]["mode"],
            "allowed": cfg["whatsapp"]["allowed"],
            "read_receipts": cfg["whatsapp"]["read_receipts"],
            "typing_indicator": cfg["whatsapp"]["typing_indicator"],
            "reply_to_message": cfg["whatsapp"]["reply_to_message"],
            "lid_count": len(cfg["whatsapp"].get("lid_map", {})),
            "live": live_whatsapp or {"state": "idle"},
        },
        "telegram": {
            "has_token": _has_token(cfg),
            "username": cfg["telegram"]["username"],
            "chat_ids": cfg["telegram"]["chat_ids"],
        },
    }


def save_whatsapp(agent_number: str, mode: str, allowed: list, options: dict | None = None) -> tuple[bool, dict | str]:
    number = _digits(agent_number)
    if len(number) < 7:
        return False, "agent number required (7-15 digits)"
    if mode not in ("self", "selected"):
        return False, "mode must be self|selected"
    nums: list[str] = []
    for n in allowed or []:
        d = _digits(n)
        if 7 <= len(d) <= 15 and d not in nums:
            nums.append(d)
    cfg = read_config()
    options = options if isinstance(options, dict) else {}
    current = cfg["whatsapp"]
    cfg["whatsapp"] = {
        "agent_number": number,
        "mode": mode,
        "allowed": nums[:_MAX_NUMBERS],
        "read_receipts": options["read_receipts"] if isinstance(options.get("read_receipts"), bool) else bool(current.get("read_receipts", False)),
        "typing_indicator": options["typing_indicator"] if isinstance(options.get("typing_indicator"), bool) else bool(current.get("typing_indicator", True)),
        "reply_to_message": options["reply_to_message"] if isinstance(options.get("reply_to_message"), bool) else bool(current.get("reply_to_message", False)),
        "lid_map": current.get("lid_map", {}),
    }
    permitted = {number, *cfg["whatsapp"]["allowed"]}
    cfg["whatsapp"]["lid_map"] = {
        phone: lid for phone, lid in _lid_map(cfg["whatsapp"]["lid_map"]).items()
        if phone in permitted
    }
    try:
        write_db_json(_FILENAME, cfg)
        return True, cfg["whatsapp"]
    except Exception as e:
        return False, f"save failed: {e}"


def remember_lid_mapping(phone_value: object, lid_value: object) -> bool:
    """Persist a WhatsApp-resolved LID only for an already allowed number."""
    phone, lid = _digits(phone_value), _lid(lid_value)
    if not (7 <= len(phone) <= 15 and lid):
        return False
    try:
        cfg = read_config()
        wa = cfg["whatsapp"]
        permitted = {str(wa.get("agent_number") or ""), *wa.get("allowed", [])}
        if phone not in permitted:
            return False
        mappings = _lid_map(wa.get("lid_map"))
        if mappings.get(phone) == lid:
            return True
        mappings[phone] = lid
        wa["lid_map"] = mappings
        write_db_json(_FILENAME, cfg)
        return True
    except Exception:
        return False


def save_telegram(token: str, username: str, chat_ids: list) -> tuple[bool, dict | str]:
    cfg = read_config()
    token = str(token or "").strip()[:160]
    if token:
        import re

        if not re.match(r"^\d+:[\w-]{10,}$", token):
            return False, "token looks invalid"
        cfg["_telegram_token"] = token
    elif not _has_token(cfg):
        return False, "bot token required"
    cfg["telegram"] = {
        "username": str(username or "").strip().lstrip("@")[:64],
        "chat_ids": [str(c).strip()[:32] for c in (chat_ids or []) if str(c).strip()][: _MAX_CHATS],
    }
    cfg["telegram"]["chat_ids"] = [
        c for c in cfg["telegram"]["chat_ids"] if c and c.lstrip("-").isdigit()
    ]
    try:
        write_db_json(_FILENAME, cfg)
        out = dict(cfg["telegram"])
        out["has_token"] = True
        return True, out
    except Exception as e:
        return False, f"save failed: {e}"


def get_telegram_token() -> str:
    try:
        return str(read_config().get("_telegram_token") or "")
    except Exception:
        return ""


def clear_channel(channel: str) -> bool:
    try:
        cfg = read_config()
        if channel == "whatsapp":
            cfg["whatsapp"] = default_config()["whatsapp"]
        elif channel == "telegram":
            cfg["telegram"] = {"username": "", "chat_ids": []}
            cfg.pop("_telegram_token", None)
        else:
            return False
        write_db_json(_FILENAME, cfg)
        return True
    except Exception:
        return False
