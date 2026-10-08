"""Telegram adapter — Bot API calls for connection testing.

Owns: getMe + per-chat sendMessage test. Stdlib only (urllib), bounded
timeouts, no retries on test path (fast honest result). Never raises.
"""
from __future__ import annotations

import json
import urllib.request

_API = "https://api.telegram.org/bot"
_TIMEOUT = 15


def _call(token: str, method: str, payload: dict) -> tuple[bool, dict | str]:
    try:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{_API}{token}/{method}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as res:
            out = json.loads(res.read().decode("utf-8", "replace"))
    except Exception as e:
        return False, f"network error: {str(e)[:120]}"
    if isinstance(out, dict) and out.get("ok"):
        return True, out.get("result") if isinstance(out.get("result"), dict) else {}
    desc = ""
    try:
        desc = str(out.get("description") or "telegram error")
    except Exception:
        desc = "telegram error"
    return False, desc[:160]


def test_connection(token: str, chat_ids: list) -> dict:
    """Validate token (getMe) then deliver a test message to each chat."""
    token = str(token or "").strip()
    if not token:
        return {"ok": False, "error": "bot token required"}
    ok, me = _call(token, "getMe", {})
    if not ok:
        return {"ok": False, "error": str(me)}
    ids = [str(c).strip() for c in (chat_ids or []) if str(c).strip()][:20]
    ids = [c for c in ids if c.lstrip("-").isdigit()]
    chats: list[dict] = []
    all_ok = True
    for cid in ids:
        sent, res = _call(
            token, "sendMessage", {"chat_id": cid, "text": "✅ X5Agent test — your agent is connected."}
        )
        chats.append({"id": cid, "ok": bool(sent), "error": "" if sent else str(res)})
        all_ok = all_ok and bool(sent)
    bot = me if isinstance(me, dict) else {}
    return {
        "ok": all_ok if ids else True,
        "bot": {
            "username": str(bot.get("username") or ""),
            "name": str(bot.get("first_name") or ""),
        },
        "chats": chats,
    }
