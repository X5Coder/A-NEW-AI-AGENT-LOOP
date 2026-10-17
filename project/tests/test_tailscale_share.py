"""Tailscale public share: offline-safe contract tests (no real tailscale needed)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from web.server import tailscale_share as ts


def test_login_url_extract():
    url = "https://login.tailscale.com/admin/machines/abc123"
    assert ts._extract_login_url(f"To authenticate, visit:\n\n    {url}\n") == url
    assert ts._extract_login_url("no url here") is None


def test_status_offline_shape(monkeypatch, tmp_path):
    monkeypatch.setattr(ts, "find_tailscale", lambda: None)
    monkeypatch.setattr(ts, "_data_file", lambda: tmp_path / "tailscale.json")
    snap = ts.status(port=7777)
    assert snap["installed"] is False
    assert snap["local"] == "http://127.0.0.1:7777/"
    assert "install_url" in snap


def test_status_with_saved_url(monkeypatch, tmp_path):
    saved = tmp_path / "tailscale.json"
    saved.write_text(json.dumps({"public_url": "https://demo.tail123.ts.net"}), encoding="utf-8")
    monkeypatch.setattr(ts, "find_tailscale", lambda: None)
    monkeypatch.setattr(ts, "_data_file", lambda: saved)
    assert ts.status()["public_url"] == "https://demo.tail123.ts.net"


def test_login_status_not_logged_in(monkeypatch):
    class Proc:
        returncode = 0
        stdout = json.dumps({"BackendState": "NeedsLogin", "Self": {}})
    monkeypatch.setattr(ts, "_run", lambda *a, **k: Proc())
    assert ts.login_status("tailscale") == {"logged_in": False, "dns": None}


def test_login_status_logged_in(monkeypatch):
    class Proc:
        returncode = 0
        stdout = json.dumps({"BackendState": "Running",
                             "Self": {"DNSName": "desk.example.ts.net."}})
    monkeypatch.setattr(ts, "_run", lambda *a, **k: Proc())
    info = ts.login_status("tailscale")
    assert info == {"logged_in": True, "dns": "desk.example.ts.net"}


def test_announce_login_url_clean_box(monkeypatch, capsys):
    monkeypatch.setattr(ts, "_open_url_best_effort", lambda url: False)
    ts._announce_login_url("https://login.tailscale.com/a/xyz", attempt=1)
    out = capsys.readouterr().out
    assert "https://login.tailscale.com/a/xyz" in out
    assert "no browser" not in out.lower()
    ts._announce_login_url("https://login.tailscale.com/a/abc", attempt=2)
    out = capsys.readouterr().out
    assert "https://login.tailscale.com/a/abc" in out
    assert "fresh" in out.lower()


def test_login_interactive_renews_until_success(monkeypatch):
    calls = {"n": 0}

    def fake_once(bin_path, attempt):
        calls["n"] += 1
        return calls["n"] >= 3

    monkeypatch.setattr(ts, "_login_once", fake_once)
    monkeypatch.setattr(ts, "login_status",
                        lambda b: {"logged_in": False, "dns": None})
    assert ts.login_interactive("tailscale") is True
    assert calls["n"] == 3


def test_login_interactive_bounded(monkeypatch):
    calls = {"n": 0}

    def fake_once(bin_path, attempt):
        calls["n"] += 1
        return False

    monkeypatch.setattr(ts, "_login_once", fake_once)
    monkeypatch.setattr(ts, "login_status",
                        lambda b: {"logged_in": False, "dns": None})
    assert ts.login_interactive("tailscale") is False
    assert calls["n"] == ts.LOGIN_MAX_ATTEMPTS


def test_enable_funnel_saves_record(monkeypatch, tmp_path):
    class Proc:
        returncode = 0
        stdout = ""
    monkeypatch.setattr(ts, "_run", lambda *a, **k: Proc())
    monkeypatch.setattr(ts, "login_status",
                        lambda b: {"logged_in": True, "dns": "desk.example.ts.net"})
    saved = tmp_path / "tailscale.json"
    monkeypatch.setattr(ts, "_data_file", lambda: saved)
    assert ts.enable_funnel("tailscale", 7777) == "https://desk.example.ts.net"
    assert json.loads(saved.read_text(encoding="utf-8"))["public_url"] == \
        "https://desk.example.ts.net"
