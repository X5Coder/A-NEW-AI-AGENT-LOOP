"""Gateway senders — outbound text delivery per channel. Never raises.

Telegram: Bot API sendMessage (chunked 4000). WhatsApp: bridge /send.
Secrets (bot token) are read fresh from the store per call, never logged.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_CHUNK = 4000
_TIMEOUT = 20


def _chunks(text: str) -> list[str]:
    text = str(text or "")
    if not text.strip():
        return []
    return [text[i : i + _CHUNK] for i in range(0, len(text), _CHUNK)] or []


def send_telegram(chat_id: str, text: str) -> bool:
    try:
        from gateway.store import get_telegram_token

        token = get_telegram_token()
        if not token:
            return False
        ok_all = True
        for part in _chunks(text):
            try:
                body = json.dumps({"chat_id": chat_id, "text": part}).encode("utf-8")
                req = urllib.request.Request(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=_TIMEOUT) as res:
                    out = json.loads(res.read().decode("utf-8", "replace"))
                    ok_all = ok_all and bool(isinstance(out, dict) and out.get("ok"))
            except Exception:
                ok_all = False
        return ok_all
    except Exception:
        return False


def send_whatsapp(to: str, text: str, reply_to: str = "") -> bool:
    try:
        from gateway.whatsapp import adapter as _wa

        ok_all = True
        first = True
        for part in _chunks(text):
            ok, _ = _wa.send_text(to, part, reply_to=reply_to if first else "")
            first = False
            ok_all = ok_all and ok
        return ok_all
    except Exception:
        return False


def send_text(channel: str, chat: str, text: str, reply_to: str = "") -> bool:
    """Best-effort send. Returns True only if every chunk delivered."""
    try:
        if channel == "telegram":
            return send_telegram(chat, text)
        if channel == "whatsapp":
            return send_whatsapp(chat, text, reply_to=reply_to)
        return False
    except Exception:
        return False


def send_typing(channel: str, chat: str) -> bool:
    """Typing indicator (refresh while the run is going). Best-effort."""
    try:
        if channel == "telegram":
            from gateway.store import get_telegram_token

            token = get_telegram_token()
            if not token:
                return False
            body = json.dumps({"chat_id": chat, "action": "typing"}).encode("utf-8")
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendChatAction",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=_TIMEOUT):
                return True
        if channel == "whatsapp":
            from gateway.whatsapp import adapter as _wa

            return bool(_wa.typing(chat))
        return False
    except Exception:
        return False
