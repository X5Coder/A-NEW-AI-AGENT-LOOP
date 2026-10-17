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
    assert ca.setup_owner("Owner@Gmail.com", "SecretPass1", p) == "owner@gmail.com"
    assert ca.is_setup(p) is True
    assert ca.owner_email(p) == "owner@gmail.com"
    assert ca.verify_owner("owner@gmail.com", "SecretPass1", p) is True
    assert ca.verify_owner("owner@gmail.com", "wrong-pass", p) is False
    assert ca.verify_owner("other@gmail.com", "SecretPass1", p) is False


def test_no_plaintext_password_stored(tmp_path):
    p = _tmp(tmp_path)
    ca.setup_owner("a@gmail.com", "SecretPass1", p)
    assert "SecretPass1" not in p.read_text(encoding="utf-8")


def test_setup_refuses_second_owner(tmp_path):
    p = _tmp(tmp_path)
    ca.setup_owner("a@gmail.com", "SecretPass1", p)
    with pytest.raises(RuntimeError):
        ca.setup_owner("c@gmail.com", "OtherPass2", p)


def test_owner_email_is_an_english_gmail_address():
    assert ca.email_problem("kareem.diaa+work@gmail.com") is None
    assert ca.email_problem("Kareem@Gmail.com") is None, "case is normalised"
    for bad in ("kareem@yahoo.com", "kareem@gmail.co", "kareem@gmail.com.eg",
                "بريد@gmail.com", "not-an-email", "", ".hidden@gmail.com"):
        assert ca.email_problem(bad), f"{bad!r} must be refused"


def test_owner_password_is_english_only():
    assert ca.password_problem("Kareem1234##") is None
    assert ca.password_problem("short") == "password too short (min 8)"
    assert ca.password_problem("") == "password is required"
    for bad in ("كلمةالسر123", "pass\tword", "pass word"):
        problem = ca.password_problem(bad)
        assert problem and "English" in problem, f"{bad!r} must be refused"


def test_setup_validates_input(tmp_path):
    with pytest.raises(ValueError):
        ca.setup_owner("not-an-email", "SecretPass1", _tmp(tmp_path))
    with pytest.raises(ValueError):
        ca.setup_owner("a@gmail.com", "short", _tmp(tmp_path))
    with pytest.raises(ValueError):
        ca.setup_owner("a@yahoo.com", "SecretPass1", _tmp(tmp_path))
    with pytest.raises(ValueError):
        ca.setup_owner("a@gmail.com", "كلمةالسر123", _tmp(tmp_path))


def test_change_password(tmp_path):
    p = _tmp(tmp_path)
    ca.setup_owner("a@gmail.com", "OldPass11", p)
    with pytest.raises(ValueError):
        ca.change_password("a@gmail.com", "wrong-old", "NewPass22", p)
    with pytest.raises(ValueError):
        ca.change_password("a@gmail.com", "OldPass11", "short", p)
    with pytest.raises(ValueError):
        ca.change_password("a@gmail.com", "OldPass11", "كلمةالسر123", p)
    ca.change_password("a@gmail.com", "OldPass11", "NewPass22", p)
    assert ca.verify_owner("a@gmail.com", "NewPass22", p) is True
    assert ca.verify_owner("a@gmail.com", "OldPass11", p) is False


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
