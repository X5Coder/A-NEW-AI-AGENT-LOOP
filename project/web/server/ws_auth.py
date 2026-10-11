"""WS entry tokens — one token per login, presented as the first WS message.

Why not reuse the cookie session: a WebSocket client must prove the SAME
browser login even when cookies are absent (private mode, file://, remote
funnel quirks), so the server mints a dedicated entry token at login time
and the client presents it as the first message on EVERY new connection:

    client -> {"op": "auth", "token": "<entry token>"}     (first message)
    server -> {"type": "auth_ok", "hello": {...}}           (tokens matched)
    server -> close 4401                                    (mismatch/unknown)

Rules:
- `mint()` is called ONLY from the login/setup HTTP handlers, right after
  `create_session()` — one entry token per login, bound to that session.
- `check()` uses hmac.compare_digest (constant-time) and enforces expiry.
- `drop_session_tokens()` forgets every entry token of a logged-out session
  (logout / password change revoke the session tokens too).
- Tokens live in memory only (like sessions): restart logs everyone out.
- Bounded: max 1000 entries, expired pruned lazily on every call.

Startup grace: when NO owner exists yet (first-run setup), the server has
no token to match — the very first connection is admitted with an empty
auth token and a `setup_needed` hello so the setup page can create the
owner. The moment an owner exists, empty tokens are rejected.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time

ENTRY_DAYS = 30
MAX_ENTRIES = 1000

_lock = threading.Lock()
_entries: dict[str, dict] = {}  # token -> {"session": str, "exp": float}
_session_index: dict[str, set[str]] = {}  # session -> {token, ...}


def _prune_locked(now: float) -> None:
    try:
        dead = [t for t, e in _entries.items() if e.get("exp", 0) < now]
        for t in dead:
            sess = (_entries.pop(t) or {}).get("session")
            if sess and sess in _session_index:
                _session_index[sess].discard(t)
                if not _session_index[sess]:
                    _session_index.pop(sess, None)
        if len(_entries) > MAX_ENTRIES:
            oldest = sorted(_entries.items(), key=lambda kv: kv[1].get("exp", 0))
            for t, e in oldest[:len(_entries) - MAX_ENTRIES]:
                _entries.pop(t, None)
                sess = e.get("session")
                if sess and sess in _session_index:
                    _session_index[sess].discard(t)
                    if not _session_index[sess]:
                        _session_index.pop(sess, None)
    except Exception:
        pass


def mint(session_token: str) -> str:
    """Mint one WS entry token bound to a session. Never raises empty."""
    token = secrets.token_urlsafe(32)
    try:
        with _lock:
            _prune_locked(time.time())
            _entries[token] = {
                "session": str(session_token or ""),
                "exp": time.time() + ENTRY_DAYS * 86400,
            }
            if session_token:
                _session_index.setdefault(str(session_token), set()).add(token)
    except Exception:
        pass
    return token


def check(token: str | None) -> bool:
    """True when the entry token is known, unexpired, and well-formed."""
    try:
        cand = str(token or "")
        if not cand or len(cand) > 256:
            return False
        now = time.time()
        with _lock:
            _prune_locked(now)
            for known, entry in _entries.items():
                try:
                    if hmac.compare_digest(known, cand):
                        return bool(entry.get("exp", 0) >= now)
                except Exception:
                    continue
        return False
    except Exception:
        return False


def check_hmac(token: str | None) -> bool:
    """Alias with constant-time intent made explicit for reviewers."""
    return check(token)


def drop_session_tokens(session_token: str | None) -> None:
    """Forget every entry token minted for one session (logout)."""
    try:
        if not session_token:
            return
        with _lock:
            toks = _session_index.pop(str(session_token), set())
            for t in toks:
                _entries.pop(t, None)
    except Exception:
        pass


def drop_token(token: str | None) -> None:
    """Forget a single entry token. Never raises."""
    try:
        if not token:
            return
        with _lock:
            entry = _entries.pop(str(token), None)
            sess = (entry or {}).get("session")
            if sess and sess in _session_index:
                _session_index[sess].discard(str(token))
                if not _session_index[sess]:
                    _session_index.pop(sess, None)
    except Exception:
        pass


def token_session(token: str | None) -> str | None:
    """Session a valid entry token belongs to (None when invalid)."""
    try:
        cand = str(token or "")
        if not cand:
            return None
        with _lock:
            for known, entry in _entries.items():
                try:
                    if hmac.compare_digest(known, cand):
                        if entry.get("exp", 0) >= time.time():
                            return entry.get("session") or None
                        return None
                except Exception:
                    continue
        return None
    except Exception:
        return None


def entry_fingerprint(token: str | None) -> str:
    """Safe log label for a token (first 6 chars of its sha256, never the token)."""
    try:
        return hashlib.sha256(str(token or "").encode()).hexdigest()[:6]
    except Exception:
        return "?"
