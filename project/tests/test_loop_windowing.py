"""Loop unit-window + missing-model skip tests (no LLM, deterministic)."""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent.loop.engine import (
    _UNIT_LIMIT,
    _model_skipped_result,
    _outgoing_messages,
)


def _unit(i: int, tools: int = 1) -> list[dict]:
    assistant = {"role": "assistant", "content": f"reply {i}",
                 "tool_calls": [{"id": f"c{i}-{k}", "name": "t"} for k in range(tools)]}
    unit = [assistant]
    for k in range(tools):
        unit.append({"role": "tool", "tool_call_id": f"c{i}-{k}", "content": f"out {i}-{k}"})
    return unit


def test_window_keeps_seed_plus_last_30_units_with_linkage():
    seed = [{"role": "system", "content": "sys"}, {"role": "user", "content": "ctx"}]
    units = [_unit(i) for i in range(35)]
    out = _outgoing_messages(seed, units)
    assert out[:2] == seed
    assert len(out) == 2 + 30 * 2  # 30 units x (assistant + tool)
    assert out[2]["content"] == "reply 5"  # oldest kept
    assert out[3]["tool_call_id"] == "c5-0"  # call/output stay together
    assert out[-2]["content"] == "reply 34"


def test_single_reply_unit_counts_as_one_message():
    seed = [{"role": "system", "content": "s"}]
    units = [[{"role": "assistant", "content": f"r{i}"}] for i in range(40)]
    out = _outgoing_messages(seed, units)
    assert len(out) == 1 + 30
    assert out[1]["content"] == "r10"


def test_short_history_passes_through_untouched():
    seed = [{"role": "system", "content": "s"}]
    units = [_unit(0), _unit(1)]
    out = _outgoing_messages(seed, units)
    assert len(out) == 1 + 4


def test_outgoing_never_raises_on_garbage():
    assert isinstance(_outgoing_messages(None, None), list)  # type: ignore[arg-type]
    assert _outgoing_messages([{"role": "system"}], "nope") == [{"role": "system"}]  # type: ignore[arg-type]


def test_unit_limit_is_30():
    assert _UNIT_LIMIT == 30


def test_skip_result_never_stops_loop_and_links_model_page():
    res = _model_skipped_result("vision_analyze", {"prompt": "x"}, "vision")
    assert res["success"] is True
    out = res["output"]
    assert out["skipped"] is True
    assert out["needs_model_kind"] == "vision"
    assert "/ai-models" in out["message"]
    assert "vision_analyze" in out["event"]


def test_skip_result_never_raises():
    res = _model_skipped_result("", None, "stt")
    assert res["success"] is True
    assert res["output"]["needs_model_kind"] == "stt"
