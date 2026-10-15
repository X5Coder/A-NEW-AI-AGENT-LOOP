"""Question pause validation: invalid questions bounce to the model, never park the UI."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent.loop.engine import _ask_user


def _recorder(answer="My PDF"):
    calls = []

    def cb(kind, payload):
        calls.append((kind, payload))
        return {"answer": answer}

    return calls, cb


def test_empty_question_rejected_without_pausing():
    calls, cb = _recorder()
    res = _ask_user("question", {"question": "   "}, cb)
    assert res["success"] is False and "Invalid question" in res["error"]
    assert calls == []


def test_missing_question_rejected_without_pausing():
    calls, cb = _recorder()
    res = _ask_user("question", {}, cb)
    assert res["success"] is False and "Invalid question" in res["error"]
    assert calls == []


def test_non_dict_args_rejected_without_pausing():
    calls, cb = _recorder()
    res = _ask_user("question", "just a string", cb)
    assert res["success"] is False and "Invalid question" in res["error"]
    assert calls == []


def test_empty_options_rejected_without_pausing():
    calls, cb = _recorder()
    res = _ask_user("question", {"question": "What is the PDF name?", "options": []}, cb)
    assert res["success"] is False and "Invalid question" in res["error"]
    assert calls == []


def test_too_many_options_rejected_without_pausing():
    calls, cb = _recorder()
    res = _ask_user("question", {"question": "Pick?", "options": ["a", "b", "c", "d", "e"]}, cb)
    assert res["success"] is False
    assert calls == []


def test_valid_essay_pauses_once_and_returns_answer():
    calls, cb = _recorder(answer="Energy Report")
    res = _ask_user("question", {"question": "What is the PDF name?"}, cb)
    assert res["success"] is True
    assert res["output"]["answer"] == "Energy Report"
    assert len(calls) == 1 and calls[0][0] == "ask"
    assert calls[0][1]["question"] == "What is the PDF name?"
    assert calls[0][1]["options"] == []


def test_valid_choices_pass_options_through():
    calls, cb = _recorder(answer="True")
    res = _ask_user("question", {"question": "Delete?", "options": ["True", "False"]}, cb)
    assert res["success"] is True
    assert calls[0][1]["options"] == ["True", "False"]
