"""WhatsApp adapter — owns the Baileys bridge subprocess lifecycle + loopback proxy.

Lifecycle: create (spawn node, cwd=bridge dir, free loopback port, AUTH_DIR
in Data) -> use (proxy /status /connect /pair /disconnect) -> release.
Single owner: this module (lock-guarded). status() never spawns (idle
default); connect()/pair() boot on demand. Never raises to callers.
"""
from __future__ import annotations

import base64
import hmac
import io
import json
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.paths import data_subdir

_BRIDGE_DIR = Path(__file__).resolve().parent / "bridge"
_BOOT_TIMEOUT = 20
_PROXY_TIMEOUT = 15

_lock = threading.Lock()
_proc: subprocess.Popen | None = None
_port: int = 0
_PIDFILE = "bridge.pid"

# Bridge push channel (event-driven, no polling): the bridge POSTs wakeups
# and link changes to Python. Secret is per-boot and loopback-only.
_PUSH_SECRET = secrets.token_hex(32)
_PUSH_URL = ""


def configure_push(url: str) -> None:
    """Set where the bridge pushes events (server port may vary)."""
    global _PUSH_URL
    try:
        _PUSH_URL = str(url or "").strip()
    except Exception:
        pass


def check_push_secret(value: object) -> bool:
    try:
        return bool(value) and hmac.compare_digest(str(value), _PUSH_SECRET)
    except Exception:
        return False


# Watchdog: the engine can freeze with a live process (answers nothing,
# not even /health). TRANSPORT failures (timeout/refused/dead proc — never
# an app-level Baileys 500 like rate-limit) inside a 60s window mean a wedged
# loop, so the proc is dropped; drain/send paths reboot inline via
# _ensure_live (zero background polling anywhere). Old hits expire so one
# stale failure can never team up with a fresh one to kill a healthy engine.
_wedge_hits: list[float] = []
_WEDGE_KILL_AFTER = 2
_WEDGE_WINDOW = 60.0
_last_heal = 0.0
_HEAL_COOLDOWN = 30.0


def _ensure_live() -> bool:
    """Drop a wedged proc and boot a clean engine, then retry once.

    Called inline on operation failure (drain/send), so stalls heal at
    use-time with zero background polling. Bounded: one respawn attempt +
    one heal per 30s (no respawn storms) + 10s connect wait. Healing counts
    only when the socket reports connected. Must be called with _lock held
    (_spawn takes no lock).
    """
    global _last_heal
    try:
        now = time.monotonic()
        if now - _last_heal < _HEAL_COOLDOWN:
            return False
        _last_heal = now
        stop()
        if not _spawn():
            return False
        ok, st = _proxy("POST", "/connect", {"phone": ""})
        if not ok:
            return False
        if isinstance(st, dict) and st.get("state") == "connected":
            _note_proxy_result(True)
            return True
        deadline = time.time() + 10
        while time.time() < deadline:
            time.sleep(1)
            ok2, st2 = _proxy("GET", "/status")
            if ok2 and isinstance(st2, dict) and st2.get("state") == "connected":
                _note_proxy_result(True)
                return True
        return False
    except Exception:
        return False


def _note_proxy_result(ok: bool, out: dict | None = None) -> bool:
    """Reset on success; drop a wedged engine after bounded failures.

    Only transport failures count (an app-level 500 must never condemn a
    healthy engine). Returns True when the caller should reboot + retry once
    (proc was just condemned). Never raises.
    """
    global _wedge_hits
    if ok:
        _wedge_hits = []
        return False
    try:
        transport = not isinstance(out, dict) or bool(out.get("_transport"))
    except Exception:
        transport = True
    if not transport:
        return False
    now = time.monotonic()
    _wedge_hits = [t for t in _wedge_hits if now - t < _WEDGE_WINDOW] + [now]
    if len(_wedge_hits) < _WEDGE_KILL_AFTER:
        return False
    _wedge_hits = []
    try:
        stop()
    except Exception:
        pass
    try:
        from gateway import dispatch as _dispatch

        _dispatch.log_event("bridge", "wedged engine dropped (self-heal on next use)")
    except Exception:
        pass
    return True


def _idle(extra: dict | None = None) -> dict:
    base = {"state": "idle", "phone": "", "qr": False, "pairing": "", "error": ""}
    if extra:
        base.update(extra)
    return base


def engine_status() -> dict:
    """Static capability report (no spawn): node + bridge install present?"""
    node = shutil.which("node") or ""
    bridge_js = _BRIDGE_DIR / "bridge.js"
    baileys = _BRIDGE_DIR / "node_modules" / "@whiskeysockets" / "baileys" / "package.json"
    return {
        "node": bool(node),
        "bridge": bridge_js.is_file(),
        "library": baileys.is_file(),
        "running": _proc is not None and _proc.poll() is None,
    }


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        try:
            s.close()
        except Exception:
            pass


def _pidfile() -> Path:
    return _auth_dir() / _PIDFILE


def _record_bridge_pid() -> None:
    try:
        if _proc is not None and _proc.pid:
            _pidfile().write_text(str(_proc.pid), encoding="utf-8")
    except Exception:
        pass


def _clear_bridge_pid() -> None:
    try:
        _pidfile().unlink(missing_ok=True)
    except Exception:
        pass


def _is_bridge_pid(pid: int) -> bool:
    """True when PID is a live node running our bridge.js (no wmic needed)."""
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             f'(Get-CimInstance Win32_Process -Filter "ProcessId={int(pid)}").CommandLine'],
            timeout=10,
        ).decode("utf-8", "replace").lower()
        return "node" in out and "bridge.js" in out
    except Exception:
        return False


def reap_orphans() -> int:
    """Kill a bridge left behind by a dead server (pidfile-tracked).

    A restart must never leave two sockets on one session: WhatsApp drops
    one of them and the UI flaps between linked and idle. Falls back to
    the legacy sweep. Never raises.
    """
    killed = 0
    try:
        raw = _pidfile().read_text(encoding="utf-8").strip()
    except Exception:
        return reap_stale()
    try:
        pid = int(raw)
    except Exception:
        _clear_bridge_pid()
        return reap_stale()
    try:
        if _is_bridge_pid(pid):
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], timeout=10,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            killed = 1
    except Exception:
        pass
    _clear_bridge_pid()
    try:
        killed += reap_stale()
    except Exception:
        pass
    return killed


def _spawn() -> bool:
    """Start the bridge (max 2 attempts, bounded). Returns True when READY."""
    global _proc, _port
    node = shutil.which("node")
    if not node or not (_BRIDGE_DIR / "bridge.js").is_file():
        return False
    try:
        auth_dir = data_subdir("configs", "whatsapp_session")
        auth_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return False
    for _ in range(2):
        try:
            if _proc is not None and _proc.poll() is None:
                return True
            _port = _free_port()
            env = dict(__import__("os").environ)
            env["BRIDGE_PORT"] = str(_port)
            env["AUTH_DIR"] = str(auth_dir)
            env["BRIDGE_PUSH_URL"] = _PUSH_URL
            env["BRIDGE_PUSH_SECRET"] = _PUSH_SECRET
            _proc = subprocess.Popen(
                [node, "bridge.js"],
                cwd=str(_BRIDGE_DIR),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            assert _proc.stdout is not None
            deadline = time.time() + _BOOT_TIMEOUT
            ready = False
            while time.time() < deadline:
                try:
                    line = _proc.stdout.readline().decode("utf-8", "replace")
                except Exception:
                    break
                if "READY port=" in line:
                    ready = True
                    break
                if _proc.poll() is not None:
                    break
            if ready:
                _record_bridge_pid()
                return True
            stop()
        except Exception:
            try:
                stop()
            except Exception:
                pass
    return False


def _auth_dir() -> Path:
    d = data_subdir("configs", "whatsapp_session")
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def has_session() -> bool:
    """True when a previous link left credentials on disk (reconnectable)."""
    try:
        return (_auth_dir() / "creds.json").is_file()
    except Exception:
        return False


def reap_stale() -> int:
    """Kill orphan bridge processes from a dead server (same script only)."""
    killed = 0
    try:
        import subprocess as _sp

        out = _sp.check_output(
            ["wmic", "process", "where", "name='node.exe'", "get", "processid,commandline", "/format:csv"],
            timeout=15,
        ).decode("utf-8", "replace")
        for line in out.splitlines():
            low = line.lower()
            if "bridge.js" not in low or "whatsapp" not in low:
                continue
            parts = [p.strip() for p in line.split(",")]
            try:
                pid = int(parts[-1])
            except Exception:
                continue
            try:
                _sp.run(["taskkill", "/F", "/PID", str(pid)], timeout=10,
                        stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
                killed += 1
            except Exception:
                continue
    except Exception:
        pass
    return killed


def warm() -> bool:
    """Boot the engine silently when a saved session exists (auto-reconnect)."""
    try:
        with _lock:
            if _proc is not None and _proc.poll() is None:
                return True
            if not has_session():
                return False
            if not _spawn():
                return False
            ok, _ = _proxy("POST", "/connect", {"phone": ""})
            return bool(ok)
    except Exception:
        return False


def stop() -> None:
    global _proc, _port
    try:
        if _proc is not None and _proc.poll() is None:
            _proc.terminate()
            try:
                _proc.wait(timeout=5)
            except Exception:
                try:
                    _proc.kill()
                except Exception:
                    pass
    finally:
        _proc = None
        _port = 0
        _clear_bridge_pid()


def _proxy(method: str, path: str, payload: dict | None = None) -> tuple[bool, dict]:
    """Loopback call. App-level Baileys failures come back as (False, body)
    WITHOUT the _transport flag; timeouts/refusals/dead-proc carry it, so the
    wedge watchdog never condemns a healthy engine over a 500."""
    if _proc is None or _proc.poll() is not None or not _port:
        return False, {"error": "bridge not running", "_transport": True}
    try:
        data = json.dumps(payload or {}).encode("utf-8") if method == "POST" else None
        req = urllib.request.Request(
            f"http://127.0.0.1:{_port}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        with urllib.request.urlopen(req, timeout=_PROXY_TIMEOUT) as res:
            out = json.loads(res.read().decode("utf-8", "replace"))
            return True, out if isinstance(out, dict) else {}
    except urllib.error.HTTPError as e:
        try:
            out = json.loads(e.read().decode("utf-8", "replace"))
            if isinstance(out, dict):
                return False, out
        except Exception:
            pass
        return False, {"error": f"bridge error: HTTP {getattr(e, 'code', '?')}", "_transport": True}
    except Exception as e:
        return False, {"error": f"bridge error: {str(e)[:120]}", "_transport": True}


def _qr_data_url(qr_text: str) -> str:
    """Render Baileys QR text to a PNG data URL (segno, pure-python)."""
    try:
        import segno

        buf = io.BytesIO()
        segno.make(qr_text, error="m").save(buf, kind="png", scale=6, border=2)
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return ""


def _public_link_state(st: dict) -> dict:
    """Convert one validated bridge state into the browser-safe link shape."""
    try:
        qr_text = str(st.get("qr") or "")
        return {
            "state": str(st.get("state") or "idle"),
            "phone": str(st.get("phone") or ""),
            "linked": "".join(c for c in str(st.get("linked") or "") if c.isdigit()),
            "qr": bool(qr_text),
            "qr_data_url": _qr_data_url(qr_text) if qr_text else "",
            "qr_at": int(st.get("qr_at") or 0),
            "pairing": str(st.get("pairing") or ""),
            "pairing_at": int(st.get("pairing_at") or 0),
            "error": str(st.get("error") or ""),
        }
    except Exception:
        return _idle()


def _wait_for_link_state(seconds: float = 4.0) -> dict:
    """Briefly wait for an async QR/connection event; bounded to one UI request."""
    deadline = time.monotonic() + max(0.5, seconds)
    last: dict = {}
    while time.monotonic() < deadline:
        ok, state = _proxy("GET", "/status")
        if not ok:
            return {"state": "idle", "error": str(state.get("error") or "bridge unavailable")}
        last = state
        if state.get("qr") or state.get("pairing") or state.get("state") in ("connected", "failed"):
            return state
        time.sleep(0.2)
    return last


def status() -> dict:
    """Live link state. Never spawns the engine (idle when down)."""
    with _lock:
        if _proc is None or _proc.poll() is not None or not _port:
            return _idle()
        ok, st = _proxy("GET", "/status")
        _note_proxy_result(ok, st)
        if not ok:
            return _idle({"error": str(st.get("error") or "")})
        return _public_link_state(st)


def connect(phone: str = "") -> dict:
    """Boot engine for QR linking (phone optional here, mandatory for pairing)."""
    with _lock:
        if _proc is None or _proc.poll() is not None:
            if not _spawn():
                return _idle({"error": "engine failed to start (node + bridge required)"})
        ok, st = _proxy("POST", "/connect", {"phone": phone or ""})
        if not ok:
            return _idle({"error": str(st.get("error") or "")})
        state = _wait_for_link_state()
        # A stale/corrupt saved session can immediately fall back to idle and
        # permanently suppress QR. Clear it once, then create a clean socket.
        if state.get("state") == "idle" and not state.get("qr"):
            _proxy("POST", "/disconnect", {})
            stop()
            if not _spawn():
                return _idle({"error": "engine failed to restart"})
            ok, state = _proxy("POST", "/connect", {"phone": phone or ""})
            if not ok:
                return _idle({"error": str(state.get("error") or "")})
            state = _wait_for_link_state()
        return _public_link_state(state)


def pair(phone: str) -> dict:
    """Request the 8-char link code for the agent number (mandatory)."""
    digits = "".join(c for c in str(phone or "") if c.isdigit())
    if len(digits) < 7:
        return _idle({"error": "agent number required first (7-15 digits)"})
    with _lock:
        if _proc is None or _proc.poll() is not None:
            if not _spawn():
                return _idle({"error": "engine failed to start (node + bridge required)"})
        ok, st = _proxy("POST", "/pair", {"phone": digits})
        # A bridge kept alive from a previous QR attempt can have a socket
        # that is already closed. Recreate it and retry the requested
        # pairing operation (bounded: 3 attempts max), rather than exposing
        # that transient state to the UI.
        for _ in range(2):
            if ok or "connection closed" not in str(st.get("error") or "").lower():
                break
            _proxy("POST", "/disconnect", {})
            stop()
            if not _spawn():
                break
            ok, st = _proxy("POST", "/pair", {"phone": digits})
        if not ok:
            return {
                "state": "qr",
                "phone": digits,
                "qr": False,
                "qr_data_url": "",
                "qr_at": 0,
                "pairing": "",
                "pairing_at": 0,
                "error": str(st.get("error") or ""),
            }
        try:
            return {
                "state": str(st.get("state") or "pairing"),
                "phone": digits,
                "qr": False,
                "qr_data_url": "",
                "qr_at": 0,
                "pairing": str(st.get("pairing") or ""),
                "pairing_at": int(st.get("pairing_at") or 0),
                "error": str(st.get("error") or ""),
            }
        except Exception:
            return _idle()


def inbox(take: int = 50) -> list:
    """Drain collected inbound messages (self-heals a wedged engine once). Never raises."""
    try:
        with _lock:
            if _proc is None or _proc.poll() is not None or not _port:
                return []
            path = f"/inbox?take={max(1, min(100, int(take)))}"
            ok, out = _proxy("GET", path)
            if not ok and _note_proxy_result(ok, out) and _ensure_live():
                ok, out = _proxy("GET", path)
                _note_proxy_result(ok, out)
        if not ok:
            return []
        msgs = out.get("messages")
        return msgs if isinstance(msgs, list) else []
    except Exception:
        return []


def resolve_lids(numbers: list) -> list[dict]:
    """Ask the active WhatsApp session for phone-number-to-LID mappings."""
    try:
        safe = ["".join(c for c in str(value or "") if c.isdigit())[:15] for value in (numbers or [])]
        safe = [value for value in safe if len(value) >= 7][:50]
        if not safe:
            return []
        with _lock:
            if _proc is None or _proc.poll() is not None or not _port:
                return []
            ok, out = _proxy("POST", "/resolve", {"numbers": safe})
        mappings = out.get("mappings") if ok else []
        return mappings if isinstance(mappings, list) else []
    except Exception:
        return []


def _lid_route(raw_recipient: str) -> str:
    """Resolve any recipient to a `{lid}@lid` JID. Never returns a PN route.

    The PN route arrives as an undecryptable placeholder on LID-migrated
    accounts, so it is refused instead of sending garbage: bare numbers
    and `@s.whatsapp.net` JIDs resolve through the stored phone->LID map
    (captured on first inbound message and on every settings save).
    Returns "" when no LID route exists.
    """
    raw = str(raw_recipient or "").strip()
    try:
        from gateway.store import read_config as _read_config

        mappings = _read_config()["whatsapp"].get("lid_map", {})
    except Exception:
        mappings = {}
    if raw.endswith("@lid"):
        local, _, domain = raw.partition("@")
        if local.isdigit() and 7 <= len(local) <= 30 and domain == "lid":
            return raw
        return ""
    if raw.endswith("@s.whatsapp.net"):
        local, _, domain = raw.partition("@")
        digits = "".join(c for c in local if c.isdigit())
    else:
        digits = "".join(c for c in raw if c.isdigit())
    if not digits:
        return ""
    lid = mappings.get(digits, "")
    return f"{lid}@lid" if lid else ""


def send_text(to: str, text: str, reply_to: str = "") -> tuple[bool, str]:
    """Send a text message via the linked engine. Returns (ok, error)."""
    recipient = _lid_route(to)
    text = str(text or "").strip()[:4000]
    if not recipient:
        return False, "no LID route for recipient"
    if not text:
        return False, "to + text required"
    with _lock:
        if _proc is None or _proc.poll() is not None or not _port:
            # A watchdog-dropped (wedged) engine must heal on use: one bounded
            # respawn + session resume. Without this every later send fails
            # fast with "not linked" forever and the reply never arrives.
            if not _ensure_live():
                return False, "not linked"
            if _proc is None or _proc.poll() is not None or not _port:
                return False, "not linked"
        reply_to = str(reply_to or "")[:128]
        payload = {"to": recipient, "text": text, "replyTo": reply_to}
        ok, out = _proxy("POST", "/send", payload)
        if not ok and _note_proxy_result(ok, out) and _ensure_live():
            ok, out = _proxy("POST", "/send", payload)
            _note_proxy_result(ok, out)
    if ok:
        return True, ""
    return False, str(out.get("error") or "send failed")


def send_tracked(to: str, text: str) -> tuple[bool, str, str]:
    """Send + capture the sent message id for later /edit calls.

    Returns (ok, msg_id, error). msg_id is "" when the bridge did not
    report one (edits then fall back to plain messages). Never raises."""
    try:
        recipient = _lid_route(to)
        text = str(text or "").strip()[:4000]
        if not recipient:
            return False, "", "no LID route for recipient"
        if not text:
            return False, "", "to + text required"
        with _lock:
            if _proc is None or _proc.poll() is not None or not _port:
                if not _ensure_live():
                    return False, "", "not linked"
                if _proc is None or _proc.poll() is not None or not _port:
                    return False, "", "not linked"
            ok, out = _proxy("POST", "/send", {"to": recipient, "text": text})
            if not ok and _note_proxy_result(ok, out) and _ensure_live():
                ok, out = _proxy("POST", "/send", {"to": recipient, "text": text})
                _note_proxy_result(ok, out)
        if ok:
            return True, str(out.get("id") or ""), ""
        return False, "", str(out.get("error") or "send failed")
    except Exception as e:
        return False, "", str(e)[:120]


def edit_text(to: str, msg_id: str, text: str) -> tuple[bool, str]:
    """Edit our own sent message (WhatsApp 15-min window).

    Returns (ok, error). Never raises."""
    try:
        recipient = _lid_route(to)
        text = str(text or "").strip()[:4000]
        msg_id = str(msg_id or "")
        if not recipient:
            return False, "no LID route for recipient"
        if not text or not msg_id:
            return False, "to + id + text required"
        with _lock:
            if _proc is None or _proc.poll() is not None or not _port:
                if not _ensure_live():
                    return False, "not linked"
                if _proc is None or _proc.poll() is not None or not _port:
                    return False, "not linked"
            ok, out = _proxy("POST", "/edit", {"to": recipient, "text": text, "editId": msg_id})
            if not ok:
                _note_proxy_result(ok, out)
        if ok:
            return True, ""
        return False, str(out.get("error") or "edit failed")
    except Exception as e:
        return False, str(e)[:120]


def mark_read(message_id: str) -> bool:
    """Mark one cached inbound WhatsApp message as read when enabled."""
    if not message_id or len(str(message_id)) > 128:
        return False
    with _lock:
        if _proc is None or _proc.poll() is not None or not _port:
            return False
        ok, _ = _proxy("POST", "/read", {"messageId": str(message_id)})
    return bool(ok)






def typing(to: str) -> bool:
    """Show the composing indicator in a DM. Best-effort, never raises."""
    try:
        recipient = _lid_route(to)
        if not recipient:
            return False
        with _lock:
            if _proc is None or _proc.poll() is not None or not _port:
                return False
            ok, _ = _proxy("POST", "/typing", {"to": recipient, "action": "composing"})
        return bool(ok)
    except Exception:
        return False


def numbers_match(a: str, b: str) -> bool:
    """Phone equality with last-9-digit tolerance (formatting differences)."""
    try:
        a = "".join(c for c in str(a or "") if c.isdigit())
        b = "".join(c for c in str(b or "") if c.isdigit())
        if not a or not b:
            return False
        if a == b:
            return True
        return len(a) >= 9 and len(b) >= 9 and (a.endswith(b[-9:]) or b.endswith(a[-9:]))
    except Exception:
        return False


def verify_against(number: str) -> tuple[bool, str]:
    """Enforce linked-number == agent-number. Mismatch wipes the session.

    Returns (verified, linked_digits). Unverifiable states (down, unknown
    identity, no saved number) pass through — only a PROVEN mismatch wipes.
    Never raises.
    """
    try:
        st = status()
        if st.get("state") != "connected":
            return True, ""
        linked = str(st.get("linked") or "")
        saved = "".join(c for c in str(number or "") if c.isdigit())
        if not saved or not linked:
            return True, linked
        if numbers_match(saved, linked):
            return True, linked
        disconnect()
        return False, linked
    except Exception:
        return True, ""


def disconnect() -> dict:
    """Logout, wipe session, stop engine. Never raises."""
    with _lock:
        try:
            if _proc is not None and _proc.poll() is None and _port:
                _proxy("POST", "/disconnect", {})
        except Exception:
            pass
        stop()
        return _idle()


def reset_link_attempt() -> dict:
    """Drop an unfinished QR/code attempt while preserving gateway settings."""
    with _lock:
        try:
            if _proc is not None and _proc.poll() is None and _port:
                _proxy("POST", "/disconnect", {})
        except Exception:
            pass
        stop()
        return _idle()




