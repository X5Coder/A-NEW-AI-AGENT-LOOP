"""WhatsApp inbox normalizer + push-driven drain (no polling anywhere).

The bridge POSTs a wakeup per new message batch; drain_once() collects it.
Ownership stays here: raw bridge items in, normalized dispatches out.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _digits(v: object) -> str:
    """Digits of a phone-number JID local part (PN only, never a LID)."""
    raw = str(v or "").strip()
    local, sep, domain = raw.partition("@")
    if sep and domain not in ("s.whatsapp.net", ""):
        return ""
    return "".join(c for c in (local if sep else raw) if c.isdigit())[:15]


def _lid_identity(value: object) -> str:
    """Extract a direct-chat LID without ever treating it as a phone number."""
    raw = str(value or "").strip()
    local, sep, domain = raw.partition("@")
    return local if sep and domain == "lid" and local.isdigit() and 7 <= len(local) <= 30 else ""


def _ignored_text(value: str) -> bool:
    """Ignore blank messages and punctuation-only dot messages entirely."""
    stripped = str(value or "").strip()
    return not stripped or all(char in ".…" for char in stripped)


def _reply_target(value: object) -> str:
    """Keep a direct-chat JID so replies reach LID-based self-chats."""
    raw = str(value or "").strip()
    if raw.endswith("@s.whatsapp.net") or raw.endswith("@lid"):
        local, _, domain = raw.partition("@")
        if local.isdigit() and 7 <= len(local) <= 30 and domain in ("s.whatsapp.net", "lid"):
            return raw
    return ""


def _normalize(item: dict) -> dict | None:
    try:
        if not isinstance(item, dict):
            return None
        if item.get("group"):
            return None
        # Prefer the phone-number PN (LID JIDs don't match the address book).
        # A bare `from` on an @lid chat IS the LID, never a phone number.
        lid = _lid_identity(item.get("chatJid"))
        from_me = bool(item.get("fromMe"))
        if lid:
            sender = _digits(
                item.get("senderPn") or item.get("remotePn") or item.get("participant")
            )
        else:
            sender = _digits(
                item.get("senderPn") or item.get("remotePn") or item.get("participant") or item.get("from")
            )
        if not sender:
            # LID-only identity: the linked account itself (fromMe) resolves
            # to the agent number; any other known LID resolves through the
            # stored phone->LID map instead of being dropped.
            try:
                from gateway.store import read_config

                wa_cfg = read_config()["whatsapp"]
                if from_me:
                    sender = str(wa_cfg.get("agent_number") or "")
                else:
                    mappings = wa_cfg.get("lid_map", {})
                    sender = next((p for p, l in mappings.items() if l == lid), "")
            except Exception:
                sender = ""
        if not sender:
            return None
        if lid and sender != lid:
            try:
                from gateway.store import remember_lid_mapping

                remember_lid_mapping(sender, lid)
            except Exception:
                pass
        kind = str(item.get("kind") or "text")
        if kind not in ("text", "voice", "image", "file"):
            kind = "text"
        text = str(item.get("text") or "").strip()
        blob = None
        data = item.get("data")
        if kind != "text" and isinstance(data, str) and data:
            try:
                import base64 as _b64

                blob = _b64.b64decode(data, validate=True)
            except Exception:
                blob = None
            if not blob:
                kind = "text"
                text = text or "[attachment could not be downloaded]"
        if kind == "text" and _ignored_text(text):
            return None
        return {
            "channel": "whatsapp",
            "chat": _reply_target(item.get("chatJid")) or sender,
            "sender": sender,
            "lid": lid,
            "fromMe": bool(item.get("fromMe")),
            "sender_name": str(item.get("name") or ""),
            "message_id": str(item.get("id") or "")[:128],
            "kind": kind,
            "text": text,
            "blob": blob,
            "name": str(item.get("fileName") or {"voice": "voice.ogg", "image": "photo.jpg"}.get(kind, "file")),
            "mime": str(item.get("mime") or ""),
        }
    except Exception:
        return None


def drain_once() -> int:
    """Drain + dispatch one wakeup batch, then verify the link. No loops.

    Called only from the bridge push endpoint. Returns dispatched count.
    Never raises.
    """
    from gateway import dispatch as _dispatch
    from gateway.whatsapp import adapter as _wa

    n = 0
    try:
        for item in _wa.inbox(50):
            try:
                norm = _normalize(item)
                if norm is not None:
                    _dispatch.handle_incoming(norm)
                    n += 1
            except Exception:
                continue
    except Exception:
        pass
    # Event-driven mismatch wipe (same invariant the status endpoints hold).
    try:
        from gateway.store import read_config

        saved = str(read_config()["whatsapp"]["agent_number"] or "")
        ok, linked = _wa.verify_against(saved)
        if not ok:
            _dispatch.log_event("mismatch", f"wiped link +{linked}")
    except Exception:
        pass
    return n
