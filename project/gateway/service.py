"""Gateway runtime service — auto-starts channel ingestion with the server.

Owns: Telegram long-poll thread (blocking getUpdates, no busy-poll) +
WhatsApp push wiring (the bridge POSTs wakeups/status; Python never polls
it). Idempotent start, cooperative stop. The Baileys engine itself boots
on demand (first QR/pair/send) and is untouched here.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_started = False
_stop = threading.Event()
_threads: list[threading.Thread] = []


def start(dashboard_url: str = "") -> bool:
    """Launch ingestion threads once. Never raises. Returns True if started."""
    global _started
    if _started:
        return True
    try:
        from gateway import dispatch as _dispatch
        from gateway.telegram import poller as _tg
        from gateway.whatsapp import adapter as _wa_adapter

        base = (dashboard_url or "http://127.0.0.1:7777/").rstrip("/") + "/"
        _dispatch.configure(base)
        _wa_adapter.configure_push(base + "api/gateway/whatsapp/push")
        # Single owner: drop orphan bridges, then silently resume a saved link.
        try:
            _wa_adapter.reap_orphans()
        except Exception:
            pass
        try:
            _wa_adapter.warm()
        except Exception:
            pass
        _dispatch.log_event("service", "gateway ingestion started")
        t = threading.Thread(target=_tg.run, args=(_stop,), daemon=True)
        t.start()
        _threads.append(t)
        _started = True
        return True
    except Exception:
        return False


def stop() -> None:
    try:
        _stop.set()
    except Exception:
        pass
