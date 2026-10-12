"""Browser lifecycle tests: close-noop never boots, probes stay read-only.

Safe: makes no network calls, writes no Data, and never closes a live
browser. When a browser is running, the stopped-branch assertions are
skipped instead of killing the session. No import-time side effects
(pytest-collectable; the old script raised SystemExit at import).
"""
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pytest

from X5Browser import service as svc


def _skip_if_running():
    # This test must NEVER close a live browser: the stopped-branch
    # assertions only make sense while nothing runs.
    if svc.is_running(quick=True):
        pytest.skip("browser running -> stopped-branch assertions skipped (no live browser closed)")


def test_lock_singleton():
    # Lifecycle lock is a singleton (serializes start/stop across callers).
    assert svc._lifecycle_lock() is svc._lifecycle_lock()


def test_close_noop_never_boots():
    _skip_if_running()
    gen_before = svc._gen
    out = svc.call_tool("close_browser", {})
    assert out == "Browser already stopped.", out
    assert svc._gen == gen_before + 1
    assert svc._browser is None and svc._started is False


def test_explicit_lifecycle_gate():
    _skip_if_running()
    # Probes stay read-only.
    assert svc.is_running() in (True, False)
    # Any non-starter while stopped fails clearly and boots NOTHING
    # (no WSL, no units, no Chrome) on any OS.
    gen_before = svc._gen
    for _tool, _args in (("navigate", {"to": "https://www.google.com/"}),
                         ("page_snapshot", {}),
                         ("live_preview", {})):
        try:
            svc.call_tool(_tool, _args)
        except RuntimeError as e:
            assert "browser stopped" in str(e), str(e)
        else:
            raise AssertionError(f"{_tool} booted the browser while stopped")
    assert svc._browser is None and svc._started is False
    assert svc._gen == gen_before
