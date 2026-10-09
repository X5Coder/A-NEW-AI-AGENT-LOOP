"""Console owner auth: setup-once gate, pbkdf2 verify, sessions (no I/O outside tmp)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from web.server import console_auth as ca


def _tmp(tmp_path: Path) -> Path:
    return tmp_path / "console_auth.json"


def test_setup_then_verify(tmp_path):
    p = _tmp(tmp_path)
    assert ca.is_setup(p) is False
    assert ca.setup_owner("Owner@Example.com", "SecretPass1", p) == "owner@example.com"
    assert ca.is_setup(p) is True
    assert ca.owner_email(p) == "owner@example.com"
    assert ca.verify_owner("owner@example.com", "SecretPass1", p) is True
    assert ca.verify_owner("owner@example.com", "wrong-pass", p) is False
    assert ca.verify_owner("other@example.com", "SecretPass1", p) is False


def test_no_plaintext_password_stored(tmp_path):
    p = _tmp(tmp_path)
    ca.setup_owner("a@b.co", "SecretPass1", p)
    assert "SecretPass1" not in p.read_text(encoding="utf-8")


def test_setup_refuses_second_owner(tmp_path):
    p = _tmp(tmp_path)
    ca.setup_owner("a@b.co", "SecretPass1", p)
    with pytest.raises(RuntimeError):
        ca.setup_owner("c@d.co", "OtherPass2", p)


def test_setup_validates_input(tmp_path):
    with pytest.raises(ValueError):
        ca.setup_owner("not-an-email", "SecretPass1", _tmp(tmp_path))
    with pytest.raises(ValueError):
        ca.setup_owner("a@b.co", "short", _tmp(tmp_path))


def test_change_password(tmp_path):
    p = _tmp(tmp_path)
    ca.setup_owner("a@b.co", "OldPass11", p)
    with pytest.raises(ValueError):
        ca.change_password("a@b.co", "wrong-old", "NewPass22", p)
    with pytest.raises(ValueError):
        ca.change_password("a@b.co", "OldPass11", "short", p)
    ca.change_password("a@b.co", "OldPass11", "NewPass22", p)
    assert ca.verify_owner("a@b.co", "NewPass22", p) is True
    assert ca.verify_owner("a@b.co", "OldPass11", p) is False


def test_sessions_lifecycle():
    token = ca.create_session()
    assert ca.session_valid(token) is True
    assert ca.session_valid("bogus") is False
    assert ca.session_valid(None) is False
    ca.drop_session(token)
    assert ca.session_valid(token) is False


def test_cookie_parse():
    assert ca.session_from_cookie("x5sess=abc123; other=1") == "abc123"
    assert ca.session_from_cookie("other=1") is None
    assert ca.session_from_cookie(None) is None
