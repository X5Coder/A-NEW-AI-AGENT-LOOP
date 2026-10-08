"""Gateway dispatcher — unified commands + 5s coalescing + queued runs.

Owns: per-chat state machines, command routing (/browser /dashboard
/cancel), media-to-text conversion (voice STT, image/file Workspace hints),
agent execution with reason-first narration (reason before each event,
raw output never dumped, final answer at the end).

Flow per chat: messages collect for 5s -> one merged request -> run.
A message arriving mid-run is queued (one notice) and runs right after.
Inbound media converts at run time inside the worker. Never raises.
"""
from __future__ import annotations

import sys
import threading
import time
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gateway import channels as _send

_CoalesceSeconds = 5.0
_MaxParts = 20

_states: dict[tuple[str, str], dict] = {}
_states_lock = threading.Lock()
_dashboard_url = "http://127.0.0.1:5173/"

# Bounded diagnostics ring (last 100 gateway events, no secrets).
_glog: list[dict] = []
_glog_lock = threading.Lock()
# Recently sent texts per chat (self-chat echo guard, 90s window).
_sent_echo: list[tuple[str, str, str, float]] = []


def log_event(kind: str, text: str) -> None:
    try:
        with _glog_lock:
            _glog.append({"ts": time.time(), "kind": str(kind)[:24], "msg": str(text or "")[:160]})
            while len(_glog) > 100:
                _glog.pop(0)
    except Exception:
        pass


def get_log() -> list[dict]:
    try:
        with _glog_lock:
            return [dict(e) for e in _glog]
    except Exception:
        return []


def _tell(channel: str, chat: str, text: str) -> bool:
    """Send + remember (echo guard for self-chat). Never raises."""
    try:
        _sent_echo.append((channel, chat, str(text or "").strip()[:200], time.time()))
        while len(_sent_echo) > 50:
            _sent_echo.pop(0)
    except Exception:
        pass
    return _send.send_text(channel, chat, text)


def _is_echo(msg: dict) -> bool:
    """Our own just-sent message bouncing back (self-chat) — never reprocess."""
    try:
        if not msg.get("fromMe"):
            return False
        t = str(msg.get("text") or "").strip()[:200]
        if not t:
            return False
        now = time.time()
        return any(
            c == msg.get("channel") and ch == msg.get("chat") and s == t and now - ts < 90
            for c, ch, s, ts in _sent_echo
        )
    except Exception:
        return False


def configure(dashboard_url: str) -> None:
    global _dashboard_url
    if dashboard_url:
        _dashboard_url = dashboard_url


def _state_for(key: tuple[str, str]) -> dict:
    with _states_lock:
        st = _states.get(key)
        if st is None:
            st = {
                "lock": threading.Lock(),
                "parts": [],
                "timer": None,
                "running": False,
                "cancel": None,
                "notified": False,
            }
            _states[key] = st
        return st


def _same_number(a: str, b: str) -> bool:
    a = "".join(c for c in str(a or "") if c.isdigit())
    b = "".join(c for c in str(b or "") if c.isdigit())
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= 9 and len(b) >= 9:
        return a.endswith(b[-9:]) or b.endswith(a[-9:])
    return False


def is_allowed(msg: dict) -> bool:
    """Channel allow-list: TG chat IDs, WA self/selected numbers."""
    try:
        from gateway.store import read_config

        cfg = read_config()
        if msg.get("channel") == "telegram":
            return str(msg.get("chat") or "") in list(cfg["telegram"]["chat_ids"])
        agent = cfg["whatsapp"]["agent_number"]
        sender = str(msg.get("sender") or "")
        lid = str(msg.get("lid") or "")
        # WhatsApp can identify a self-chat with an LID instead of the phone
        # number. `fromMe` is the reliable proof that this is the linked
        # agent account, so it must remain allowed in either access mode.
        if bool(msg.get("fromMe")):
            return True
        mappings = cfg["whatsapp"].get("lid_map", {})
        # The linked account's own LID is always allowed by itself: it is
        # the same identity as the agent number, no phone check needed.
        if lid and lid == mappings.get(agent):
            return True
        allowed_numbers = [agent] if cfg["whatsapp"]["mode"] == "self" else [agent, *cfg["whatsapp"]["allowed"]]
        if any(_same_number(sender, number) for number in allowed_numbers):
            return True
        # The bridge resolves allowed phone numbers to their current LIDs and
        # persists that internal map. The user only ever manages phone numbers.
        return bool(lid and any(lid == mappings.get(number) for number in allowed_numbers))
    except Exception:
        return False


def _mirror_workspace(blob: bytes, display: str) -> str | None:
    """Best-effort Workspace copy (jailed). Returns rel file_id or None."""
    try:
        from core.paths import workspace_root as _ws

        ws = _ws()
        ws.mkdir(parents=True, exist_ok=True)
        base = ws.resolve()
        safe = (Path(str(display or "file")[:120]).name or "file").replace('"', "'")
        candidate = (ws / f"{uuid.uuid4().hex[:8]}_{safe}").resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            return None
        if not candidate.exists():
            candidate.write_bytes(blob)
        return str(candidate.relative_to(base)).replace("\\", "/")
    except Exception:
        return None


def _convert_part(part: dict) -> tuple[str, str]:
    """Return (message_text, attachment_hint_line) for one buffered part."""
    kind = part.get("kind") or "text"
    text = str(part.get("text") or "").strip()
    blob = part.get("blob")
    if kind == "text" or not isinstance(blob, bytes) or not blob:
        return text, ""
    name = str(part.get("name") or "file")[:120]
    mime = str(part.get("mime") or "")
    if kind == "voice":
        ext = "ogg" if "ogg" in mime else "mp3" if "mpeg" in mime else "wav"
        try:
            from core.paths import data_subdir as _ds

            d = _ds("files", "audio")
            d.mkdir(parents=True, exist_ok=True)
            p = d / f"gw_{uuid.uuid4().hex[:12]}.{ext}"
            p.write_bytes(blob)
            from models.stt.service import transcribe_file

            heard = str(transcribe_file(str(p)) or "").strip()
        except Exception:
            heard = ""
        return (f"[voice message]: {heard}" if heard else "[voice message — could not transcribe]"), ""
    if kind == "image":
        ext = "png" if "png" in mime else "webp" if "webp" in mime else "jpg"
        try:
            from core.paths import data_subdir as _ds

            d = _ds("files", "images")
            d.mkdir(parents=True, exist_ok=True)
            p = d / f"gw_{uuid.uuid4().hex[:12]}.{ext}"
            p.write_bytes(blob)
            from core.memory.image_memory.service import store_image

            entry = store_image(name=name, path=str(p), is_persistent=False, is_temp=True)
            iid = str(entry.get("image_id") or "")
            ws_file = _mirror_workspace(blob, name)
            hint = f'- image: name="{name}" image_id="{iid}"' + (f' file_id="{ws_file}"' if ws_file else "")
            return text, hint
        except Exception:
            return text or "[image — could not save]", ""
    ws_file = _mirror_workspace(blob, name)
    hint = f'- document: name="{name}" file_id="{ws_file}"' if ws_file else ""
    return text or f"[file received: {name}]", hint


def _send_reason(channel: str, chat: str, seen: dict, *args) -> None:
    try:
        reason = str(args[2] if len(args) > 2 else "") if len(args) > 2 else ""
    except Exception:
        reason = ""
    reason = (reason or "").strip()
    if not reason or reason == seen.get("last"):
        return
    seen["last"] = reason
    _send.send_text(channel, chat, f"▫️ {reason[:300]}")


def _typing_loop(channel: str, chat: str, done) -> None:
    """Refresh the typing indicator until the run ends. Best-effort."""
    try:
        _send.send_typing(channel, chat)
    except Exception:
        pass
    while not done.is_set():
        try:
            if done.wait(4):
                break
            _send.send_typing(channel, chat)
        except Exception:
            break


def _wa_options() -> dict:
    """WhatsApp send options from settings (read/typing/reply). Never raises."""
    try:
        from gateway.store import read_config

        wa = read_config()["whatsapp"]
        return {
            "read": bool(wa.get("read_receipts", False)),
            "typing": bool(wa.get("typing_indicator", True)),
            "reply": bool(wa.get("reply_to_message", False)),
        }
    except Exception:
        return {"read": False, "typing": True, "reply": False}


def _run_parts(channel: str, chat: str, parts: list, cancel) -> None:
    log_event("run_start", f"{channel}:{chat} parts={len(parts)}")
    opts = _wa_options() if channel == "whatsapp" else {}
    typing_done = threading.Event()
    heartbeat = None
    if channel != "whatsapp" or opts.get("typing", True):
        heartbeat = threading.Thread(target=_typing_loop, args=(channel, chat, typing_done), daemon=True)
        try:
            heartbeat.start()
        except Exception:
            pass
    try:
        _run_parts_inner(channel, chat, parts, cancel, opts)
    finally:
        try:
            typing_done.set()
        except Exception:
            pass
        if channel == "whatsapp" and opts.get("read"):
            try:
                from gateway.whatsapp import adapter as _wa

                seen_ids: set[str] = set()
                for part in parts[:_MaxParts]:
                    mid = str((part or {}).get("message_id") or "")[:128]
                    if mid and mid not in seen_ids:
                        seen_ids.add(mid)
                        _wa.mark_read(mid)
            except Exception:
                pass
        log_event("run_end", f"{channel}:{chat}")


def _run_parts_inner(channel: str, chat: str, parts: list, cancel, opts: dict | None = None) -> None:
    texts: list[str] = []
    hints: list[str] = []
    for part in parts[:_MaxParts]:
        try:
            t, h = _convert_part(part)
        except Exception:
            t, h = "", ""
        if t:
            texts.append(t)
        if h:
            hints.append(h)
    extra = len(parts) - _MaxParts
    body = "\n".join(texts).strip()
    if hints:
        body += "\n\n[attachments]\n" + "\n".join(hints)
    if extra > 0:
        body += f"\n(+{extra} more omitted)"
    if not body:
        _send.send_text(channel, chat, "Couldn't read that — try again.")
        return
    seen: dict = {}
    if len(body) > 4000:
        # Shared session input cap: long runs execute locally, unmirrored.
        result = _run_direct(body, channel, chat, seen, cancel)
    else:
        result = _stream_dashboard_run(body, channel, chat, seen, cancel)
        if result is None:
            log_event("run_busy", f"{channel}:{chat} shared run busy, local fallback")
            result = _run_direct(body, channel, chat, seen, cancel)
        elif isinstance(result, dict) and result.get("transport_error"):
            log_event("run_fallback", f"{channel}:{chat} {result.get('transport_error')}")
            result = _run_direct(body, channel, chat, seen, cancel)
    try:
        reply_to = ""
        if channel == "whatsapp" and (opts or {}).get("reply"):
            try:
                reply_to = str((parts[0] or {}).get("message_id") or "")[:128]
            except Exception:
                reply_to = ""
        final = result.get("final_answer") if isinstance(result, dict) else None
        if isinstance(final, str) and final.strip():
            ok = _send.send_text(channel, chat, final.strip()[:8000], reply_to=reply_to)
        elif isinstance(result, dict) and result.get("cancelled"):
            ok = _send.send_text(channel, chat, "⏹ Stopped.")
        elif isinstance(result, dict) and result.get("direct_error"):
            ok = _send.send_text(channel, chat, f"Agent failed: {str(result.get('direct_error'))[:200]}")
        elif isinstance(result, dict) and result.get("error"):
            ok = _send.send_text(channel, chat, f"Agent failed: {str(result.get('error'))[:200]}")
        else:
            ok = _send.send_text(channel, chat, "Done.")
        if not ok:
            log_event("send_failed", f"{channel}:{chat} final reply not delivered")
    except Exception:
        pass


def _run_direct(body: str, channel: str, chat: str, seen: dict, cancel) -> dict:
    """Local agent run (fallback: shared session busy/unreachable/too long)."""
    def _narrate_ev(text: str) -> None:
        try:
            r = str(text or "").strip()[:300]
            if not r or r == seen.get("last"):
                return
            seen["last"] = r
            _send.send_text(channel, chat, r)
        except Exception:
            pass

    try:
        from core.agent.loop.engine import run_loop

        return run_loop(
            body,
            on_tool_start=lambda *a: _send_reason(channel, chat, seen, *a),
            on_event=lambda t, o=None, k=None, m=None, r=None, s=None: _narrate_ev(t),
            on_question=None,
            restricted=False,
            cancel_flag=cancel,
        )
    except Exception as e:
        return {"direct_error": str(e)[:200]}


def _stream_dashboard_run(body: str, channel: str, chat: str, seen: dict, cancel) -> dict | None:
    """Run ONCE through the main site session; tee progress to the channel.

    The dashboard Agent page renders the message + steps live from the same
    frames (shared context, single execution). Returns the done payload,
    None when the shared run is busy (caller falls back local), or
    {"transport_error": ...} when unreachable (caller falls back local).
    Never raises.
    """
    import json as _json
    import urllib.request as _urlreq

    def _narrate(reason: str) -> None:
        try:
            r = str(reason or "").strip()[:300]
            if not r or r == seen.get("last"):
                return
            seen["last"] = r
            _send.send_text(channel, chat, f"▫️ {r}")
        except Exception:
            pass

    def _forward_cancel() -> None:
        try:
            _urlreq.urlopen(
                _urlreq.Request(
                    _dashboard_url + "api/chat_cancel",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                ),
                timeout=10,
            )
        except Exception:
            pass

    try:
        req = _urlreq.Request(
            _dashboard_url + "api/chat_stream",
            data=_json.dumps({"message": body, "mode": "free", "source": "whatsapp"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            res = _urlreq.urlopen(req, timeout=None)
        except Exception as e:
            code = getattr(e, "code", 0)
            if code == 409:
                return None
            return {"transport_error": f"dashboard unreachable: {str(e)[:120]}"}
    except Exception as e:
        return {"transport_error": str(e)[:120]}
    try:
        for raw in res:
            try:
                if cancel is not None and cancel.is_set():
                    _forward_cancel()
                    try:
                        res.close()
                    except Exception:
                        pass
                    return {"cancelled": True}
                line = raw.decode("utf-8", "replace").strip()
            except Exception:
                continue
            if not line.startswith("data:"):
                continue
            try:
                fr = _json.loads(line[5:])
            except Exception:
                continue
            if not isinstance(fr, dict):
                continue
            t = fr.get("type")
            if t == "event_start":
                _narrate(fr.get("reason") or fr.get("event") or "")
            elif t == "event":
                # Completion after the reason: same dedup key, so an
                # identical label does not echo twice.
                _narrate(fr.get("event") or "")
            elif t == "live":
                try:
                    lv = fr.get("live") or {}
                    u = lv.get("url") if isinstance(lv, dict) else ""
                    if u:
                        _send.send_text(channel, chat, f"Live: {str(u)[:500]}")
                except Exception:
                    pass
            elif t == "done":
                # Normalize to the run_loop result shape (final_answer, ...).
                return {"final_answer": fr.get("reply"),
                        "steps": fr.get("steps", 0),
                        "message_ids": fr.get("ids"),
                        "final_images": fr.get("images") or [],
                        "final_audio": fr.get("audio") or [],
                        "elapsed_sec": fr.get("elapsed", 0)}
            elif t == "error":
                return {"error": fr.get("error") or "run failed"}
            elif t == "cancelled":
                return {"cancelled": True}
            # ping/token/event/live_end ignored; questions impossible here.
        return {"transport_error": "stream ended without result"}
    except Exception as e:
        return {"transport_error": f"stream failed: {str(e)[:120]}"}
    finally:
        try:
            res.close()
        except Exception:
            pass


def _worker(key: tuple[str, str]) -> None:
    channel, chat = key
    st = _state_for(key)
    while True:
        with st["lock"]:
            parts = st["parts"]
            st["parts"] = []
            if not parts:
                st["running"] = False
                st["cancel"] = None
                return
            cancel = threading.Event()
            st["cancel"] = cancel
            st["notified"] = False
        _run_parts(channel, chat, parts, cancel)


def _fire(key: tuple[str, str]) -> None:
    st = _state_for(key)
    with st["lock"]:
        st["timer"] = None
        if st["running"] or not st["parts"]:
            return
        st["running"] = True
    t = threading.Thread(target=_worker, args=(key,), daemon=True)
    t.start()


def _handle_command(msg: dict) -> bool:
    """Returns True if msg was a command (handled, never buffered)."""
    if msg.get("kind") != "text":
        return False
    cmd = str(msg.get("text") or "").strip().lower()
    if not cmd.startswith("/"):
        return False
    channel, chat = msg.get("channel"), msg.get("chat")
    if cmd == "/cancel":
        st = _state_for((channel, chat))
        with st["lock"]:
            running = st["running"]
            cancel = st["cancel"]
        if running and cancel is not None:
            try:
                cancel.set()
            except Exception:
                pass
            # Runs execute in the shared site session: stop that loop too.
            try:
                import urllib.request as _urlreq

                _urlreq.urlopen(
                    _urlreq.Request(
                        _dashboard_url + "api/chat_cancel",
                        data=b"{}",
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    ),
                    timeout=10,
                )
            except Exception:
                pass
            _send.send_text(channel, chat, "⏹ Stopping the run…")
        else:
            _send.send_text(channel, chat, "Nothing running.")
        return True
    if cmd == "/browser":
        try:
            from X5Browser.service import status_report

            rep = status_report()
            running = bool(rep.get("running")) if isinstance(rep, dict) else False
            ctrl = (rep.get("url") or "") if isinstance(rep, dict) else ""
            prev = (rep.get("preview_url") or "") if isinstance(rep, dict) else ""
            if running and (ctrl or prev):
                _send.send_text(channel, chat, f"🌐 Browser control:\n{ctrl}\n\n👁 Live view:\n{prev}")
            else:
                _send.send_text(channel, chat, "Browser is stopped — open it from the dashboard first.")
        except Exception:
            _send.send_text(channel, chat, "Browser is unavailable right now.")
        return True
    if cmd == "/dashboard":
        _send.send_text(channel, chat, f"📊 Dashboard:\n{_dashboard_url}")
        return True
    _send.send_text(channel, chat, "Commands:\n/browser — control + live links\n/dashboard — console link\n/cancel — stop the run")
    return True


def handle_incoming(msg: dict) -> None:
    """Entry point for pollers/listeners. Validates, routes, coalesces. Never raises."""
    try:
        if not isinstance(msg, dict) or not msg.get("channel") or not msg.get("chat"):
            return
        if _is_echo(msg):
            log_event("echo_skip", f"{msg.get('channel')}:{msg.get('chat')}")
            return
        if not is_allowed(msg):
            log_event(
                "blocked",
                f"{msg.get('channel')}:{msg.get('chat')} sender={str(msg.get('sender') or '')[:20]}",
            )
            return
        log_event("incoming", f"{msg.get('channel')}:{msg.get('chat')} {msg.get('kind')} {str(msg.get('text') or '')[:60]}")
        if _handle_command(msg):
            return
        if msg.get("kind") == "text" and not str(msg.get("text") or "").strip():
            return
        key = (msg["channel"], msg["chat"])
        st = _state_for(key)
        with st["lock"]:
            st["parts"].append(msg)
            if len(st["parts"]) > 60:
                st["parts"] = st["parts"][-60:]
            if st["running"]:
                if not st["notified"]:
                    st["notified"] = True
                    try:
                        _send.send_text(
                            key[0],
                            key[1],
                            "⏳ A run is already going — your message is queued and runs right after it finishes.",
                        )
                    except Exception:
                        pass
                return
            try:
                if st["timer"] is not None:
                    st["timer"].cancel()
            except Exception:
                pass
            try:
                st["timer"] = threading.Timer(_CoalesceSeconds, _fire, args=(key,))
                st["timer"].daemon = True
                st["timer"].start()
            except Exception:
                st["timer"] = None
    except Exception:
        pass
