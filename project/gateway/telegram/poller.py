"""Telegram long-poll ingester — getUpdates -> normalize -> dispatch.

Owns: offset persistence (Data/database/gateway_poll.json), media download
(voice/photo/document, 12MB cap). Text + caption flow as text; voice notes,
photos and files flow as media parts. Never raises out of the loop.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_API = "https://api.telegram.org/bot"
_MEDIA_MAX = 12 * 1024 * 1024


def _post(token: str, method: str, payload: dict, timeout: int):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{_API}{token}/{method}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8", "replace"))


def _load_offset() -> int:
    try:
        from core.paths import read_db_json

        v = read_db_json("gateway_poll.json", {})
        return int(v.get("tg_offset") or 0) if isinstance(v, dict) else 0
    except Exception:
        return 0


def _save_offset(offset: int) -> None:
    try:
        from core.paths import write_db_json

        write_db_json("gateway_poll.json", {"tg_offset": int(offset)})
    except Exception:
        pass


def _download_file(token: str, file_id: str) -> tuple[bytes, str]:
    """Returns (bytes, file_name). Empty bytes when too big/unavailable."""
    try:
        info = _post(token, "getFile", {"file_id": file_id}, 20)
        if not isinstance(info, dict) or not info.get("ok"):
            return b"", ""
        result = info.get("result") or {}
        path = str(result.get("file_path") or "")
        if not path:
            return b"", ""
        req = urllib.request.Request(f"{_API}{token}/{path}", method="GET")
        with urllib.request.urlopen(req, timeout=30) as res:
            if int(res.headers.get("Content-Length") or 0) > _MEDIA_MAX:
                return b"", ""
            blob = res.read(_MEDIA_MAX + 1)
        if not blob or len(blob) > _MEDIA_MAX:
            return b"", ""
        return blob, path.rsplit("/", 1)[-1]
    except Exception:
        return b"", ""


def _normalize(update: dict, token: str) -> dict | None:
    try:
        msg = update.get("message") or update.get("edited_message")
        if not isinstance(msg, dict):
            return None
        chat = msg.get("chat") or {}
        chat_id = str(chat.get("id") or "")
        if not chat_id:
            return None
        sender_name = str(((msg.get("from") or {}).get("first_name")) or "")
        text = str(msg.get("text") or msg.get("caption") or "").strip()
        if isinstance(msg.get("voice"), dict):
            blob, name = _download_file(token, str(msg["voice"].get("file_id") or ""))
            return {
                "channel": "telegram", "chat": chat_id, "sender": chat_id,
                "sender_name": sender_name, "kind": "voice",
                "text": text, "blob": blob or None, "name": name or "voice.ogg",
                "mime": str(msg["voice"].get("mime_type") or ""),
            }
        photos = msg.get("photo")
        if isinstance(photos, list) and photos:
            best = max(photos, key=lambda p: int((p or {}).get("file_size") or 0))
            blob, name = _download_file(token, str(best.get("file_id") or ""))
            if blob:
                return {
                    "channel": "telegram", "chat": chat_id, "sender": chat_id,
                    "sender_name": sender_name, "kind": "image",
                    "text": text, "blob": blob, "name": name or "photo.jpg",
                    "mime": "image/jpeg",
                }
            if text:
                return {
                    "channel": "telegram", "chat": chat_id, "sender": chat_id,
                    "sender_name": sender_name, "kind": "text", "text": text,
                    "blob": None, "name": "", "mime": "",
                }
            return None
        if isinstance(msg.get("document"), dict):
            doc = msg["document"]
            blob, name = _download_file(token, str(doc.get("file_id") or ""))
            if blob:
                return {
                    "channel": "telegram", "chat": chat_id, "sender": chat_id,
                    "sender_name": sender_name, "kind": "file",
                    "text": text, "blob": blob,
                    "name": str(doc.get("file_name") or name or "file"),
                    "mime": str(doc.get("mime_type") or ""),
                }
            if text:
                return {
                    "channel": "telegram", "chat": chat_id, "sender": chat_id,
                    "sender_name": sender_name, "kind": "text", "text": text,
                    "blob": None, "name": "", "mime": "",
                }
            return None
        if not text:
            return None
        return {
            "channel": "telegram", "chat": chat_id, "sender": chat_id,
            "sender_name": sender_name, "kind": "text", "text": text,
            "blob": None, "name": "", "mime": "",
        }
    except Exception:
        return None


def run(stop: threading.Event) -> None:
    """Poll forever until stop is set. Best-effort backoff on errors."""
    from gateway import dispatch as _dispatch
    from gateway.store import get_telegram_token

    offset = _load_offset()
    idle_errors = 0
    while not stop.is_set():
        try:
            token = get_telegram_token()
            if not token:
                if stop.wait(10):
                    break
                continue
            out = _post(token, "getUpdates", {"offset": offset, "timeout": 25, "allowed_updates": ["message", "edited_message"]}, 40)
            idle_errors = 0
            if not isinstance(out, dict) or not out.get("ok"):
                if stop.wait(5):
                    break
                continue
            for update in out.get("result") or []:
                try:
                    offset = max(offset, int(update.get("update_id") or 0) + 1)
                    item = _normalize(update, token)
                    if item is not None:
                        _dispatch.handle_incoming(item)
                except Exception:
                    continue
            _save_offset(offset)
        except Exception:
            idle_errors += 1
            if stop.wait(min(30, 5 * max(1, idle_errors))):
                break
