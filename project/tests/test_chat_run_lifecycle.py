"""STOP then refresh must not reconnect a cancelled run."""
from __future__ import annotations

import sys
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from web.server import server as S  # noqa: E402


def _reset() -> None:
    with S._RUN_LOCK:
        S._ACTIVE_RUN.clear()
        S._CANCEL_EVENTS.clear()
    S._run_frames_clear()


def test_stop_reports_inactive_while_thread_still_busy():
    _reset()
    ev = threading.Event()
    with S._RUN_LOCK:
        S._ACTIVE_RUN["event"] = ev
        S._ACTIVE_RUN["input"] = "do the task"
        S._ACTIVE_RUN["id"] = id(ev)
        S._CANCEL_EVENTS.add(ev)
    S._run_frame_remember({"type": "event_start", "event": "Planning task", "key": "k1"})
    live, preview = S._chat_status()
    assert live is True
    assert preview == "do the task"
    S._mark_chat_cancelled()
    live2, preview2 = S._chat_status()
    assert live2 is False
    assert preview2 == ""
    assert S._chat_thread_busy() is True
    assert ev.is_set()
    with S._RUN_FRAMES_LOCK:
        assert S._RUN_FRAMES == []
    _reset()


def test_old_thread_finally_does_not_wipe_new_run():
    _reset()
    old = threading.Event()
    new = threading.Event()
    old_id = id(old)
    new_id = id(new)
    with S._RUN_LOCK:
        S._ACTIVE_RUN["event"] = old
        S._ACTIVE_RUN["input"] = "old"
        S._ACTIVE_RUN["id"] = old_id
        S._CANCEL_EVENTS.add(old)
    S._mark_chat_cancelled()
    with S._RUN_LOCK:
        S._ACTIVE_RUN["event"] = new
        S._ACTIVE_RUN["input"] = "new"
        S._ACTIVE_RUN["id"] = new_id
        S._ACTIVE_RUN.pop("cancelled", None)
        S._CANCEL_EVENTS.add(new)
    S._run_frame_remember({"type": "event_start", "event": "Writing · a.txt", "key": "n1"})
    assert S._release_chat_run(old_id) is False
    live, preview = S._chat_status()
    assert live is True
    assert preview == "new"
    with S._RUN_FRAMES_LOCK:
        assert len(S._RUN_FRAMES) == 1
    assert S._release_chat_run(new_id) is True
    live2, _ = S._chat_status()
    assert live2 is False
    _reset()


def test_release_clears_resume_id():
    """A finished Continue must not leave a stale resume_id for later refreshes."""
    _reset()
    ev = threading.Event()
    rid = id(ev)
    with S._RUN_LOCK:
        S._ACTIVE_RUN["event"] = ev
        S._ACTIVE_RUN["input"] = ""
        S._ACTIVE_RUN["input_full"] = ""
        S._ACTIVE_RUN["id"] = rid
        S._ACTIVE_RUN["resume_id"] = "abc123"
        S._CANCEL_EVENTS.add(ev)
    assert S._release_chat_run(rid) is True
    with S._RUN_LOCK:
        assert "resume_id" not in S._ACTIVE_RUN
    _reset()


def test_is_cancelled_never_raises():
    from core.agent.loop.engine import _is_cancelled

    assert _is_cancelled(None) is False
    ev = threading.Event()
    assert _is_cancelled(ev) is False
    ev.set()
    assert _is_cancelled(ev) is True
    assert _is_cancelled(object()) is False


def test_clip_resume_units_bounds_and_skips_junk():
    from core.agent.loop.engine import _clip_resume_units

    units = [
        [{"role": "assistant", "content": "x", "tool_calls": [{"id": "c1"}]}],
        [{"role": "tool", "tool_call_id": "c1", "content": "y" * 5000}],
        "nope",
        [{"role": "user", "content": "ignore"}],
    ]
    out = _clip_resume_units(units)
    assert len(out) == 2
    assert out[0][0]["role"] == "assistant"
    assert out[1][0]["role"] == "tool"
    assert out[1][0]["content"].endswith("truncated")
    assert _clip_resume_units(None) == []
    assert _clip_resume_units("bad") == []  # type: ignore[arg-type]


def test_units_from_steps_tells_model_not_to_repeat():
    from core.agent.loop.engine import _units_from_steps

    units = _units_from_steps([
        {"event": "Terminal Command", "reason": "Render charts"},
        {"event": "Writing · make_charts.py"},
    ])
    assert len(units) == 1
    text = units[0][0]["content"]
    assert "do not repeat" in text
    assert "Terminal Command" in text
    assert _units_from_steps([]) == []
    assert _units_from_steps(None) == []


def test_run_failure_label_points_at_models_only_for_a_missing_model():
    from core.ai_models.store import missing_message

    missing = S._run_failure_payload(RuntimeError(missing_message("text")))
    assert "AI Models" in missing["error"]
    internal = S._run_failure_payload(
        SyntaxError("unterminated string literal (detected at line 693)"))
    assert "AI Models" not in internal["error"], (
        "a broken module must never be reported as a missing model")
    assert "693" in internal["detail"], "the real cause stays visible"
    plain = S._run_failure_payload(RuntimeError("tool blew up"))
    assert plain["error"] == "the agent run failed"
