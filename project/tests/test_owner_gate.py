"""Owner gate: login is mandatory, loopback is NOT a bypass.

Regression: a local reverse proxy (or anything else on this host) reaches
the console from 127.0.0.1, so a blanket loopback exemption let remote
callers in without a session. Only the gateway's own POST endpoints are
exempt from loopback now.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from web.server import console_auth as ca
from web.server.server import DashboardHandler


class _SimpleHeaders:
    def __init__(self, cookie):
        self._cookie = cookie

    def get(self, key, default=None):
        return self._cookie if key == "Cookie" else default


def _Req(path, cookie=None, peer="203.0.0.1", post=False):
    """A DashboardHandler without a socket: real class attribute lookup
    (the gate reads _AUTH_PUBLIC_*/_GATEWAY_LOOPBACK_* off the class),
    stubbed I/O below."""
    req = object.__new__(DashboardHandler)
    req.path = path
    req.headers = _SimpleHeaders(cookie)
    req.client_address = (peer, 1234)
    req._post = post
    req.answered = None

    def _send_json(obj, status=200):
        req.answered = ("json", status, obj)

    def _send_response(code):
        req._code = code

    def _send_header(key, value):
        pass

    def _end_headers():
        req.answered = ("redirect", getattr(req, "_code", 0))

    req._send_json = _send_json
    req.send_response = _send_response
    req.send_header = _send_header
    req.end_headers = _end_headers
    return req


def _gate(req):
    return DashboardHandler._auth_gate(req, req._post)


def _locked_down(monkeypatch):
    monkeypatch.setattr(ca, "is_setup", lambda: True)
    monkeypatch.setattr(ca, "session_valid", lambda token: token == "good-session")
    monkeypatch.setattr(ca, "session_from_cookie", lambda cookie: cookie)


def test_loopback_page_needs_login(monkeypatch):
    """THE hole: a loopback peer must still 302 without a session."""
    _locked_down(monkeypatch)
    req = _Req("/", peer="127.0.0.1", post=False)
    assert _gate(req) is True
    assert req.answered == ("redirect", 302)


def test_remote_page_needs_login(monkeypatch):
    _locked_down(monkeypatch)
    req = _Req("/", peer="203.0.0.1", post=False)
    assert _gate(req) is True
    assert req.answered == ("redirect", 302)


def test_loopback_api_needs_login(monkeypatch):
    _locked_down(monkeypatch)
    req = _Req("/api/dashboard", peer="127.0.0.1", post=False)
    assert _gate(req) is True
    assert req.answered[0] == "json" and req.answered[1] == 401


def test_gateway_endpoints_allowed_from_loopback(monkeypatch):
    _locked_down(monkeypatch)
    for path in ("/api/chat_stream", "/api/chat_answer", "/api/chat_cancel"):
        req = _Req(path, peer="127.0.0.1", post=True)
        assert _gate(req) is False, path
        assert req.answered is None, path


def test_other_api_post_blocked_from_loopback(monkeypatch):
    _locked_down(monkeypatch)
    req = _Req("/api/scheduler", peer="127.0.0.1", post=True)
    assert _gate(req) is True
    assert req.answered[0] == "json" and req.answered[1] == 401


def test_valid_session_passes_everywhere(monkeypatch):
    _locked_down(monkeypatch)
    for path, post, peer in (("/", False, "127.0.0.1"),
                             ("/api/dashboard", False, "203.0.0.1"),
                             ("/api/chat_stream", True, "127.0.0.1")):
        req = _Req(path, cookie="good-session", peer=peer, post=post)
        assert _gate(req) is False, (path, peer)
        assert req.answered is None


def test_auth_endpoints_always_public(monkeypatch):
    _locked_down(monkeypatch)
    req = _Req("/api/auth/login", peer="203.0.0.1", post=True)
    assert _gate(req) is False
    req = _Req("/login.html", peer="203.0.0.1", post=False)
    assert _gate(req) is False


def test_setup_endpoint_is_gone(monkeypatch):
    """Owner is terminal-only: /api/auth/setup passes the gate (public) so
    the handler can answer 410-gone with the terminal message."""
    _locked_down(monkeypatch)
    req = _Req("/api/auth/setup", peer="203.0.0.1", post=True)
    assert _gate(req) is False  # public route -> handler answers 410
    assert req.answered is None


def test_no_owner_blocks_everything(monkeypatch):
    """No owner: pages 302 to login, APIs 503 with owner_missing."""
    monkeypatch.setattr(ca, "is_setup", lambda: False)
    req = _Req("/", peer="127.0.0.1", post=False)
    assert _gate(req) is True
    assert req.answered == ("redirect", 302)
    req = _Req("/api/dashboard", peer="127.0.0.1", post=False)
    assert _gate(req) is True
    assert req.answered[0] == "json" and req.answered[1] == 503
    assert req.answered[2].get("owner_missing") is True
