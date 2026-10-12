"""Auto-memory tests: trigger cadence, extract, organize/dedup.

Safe: no network (the LLM entry is mocked), no import-time side effects,
real Data files are snapshotted and restored. The old script version of
this file wiped conversation.json + memories at import and required a real
API key — never do that again.
"""
import json
import sys
import time
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pytest

GUARD_DB_FILES = ("conversation.json", "user_memory.json", "agent_memory.json",
                  ".auto_memory_last_trigger")


@pytest.fixture
def guard():
    from core.paths import data_db_dir
    db = data_db_dir()
    before = {}
    for name in GUARD_DB_FILES:
        p = db / name
        before[name] = p.read_bytes() if p.is_file() else None
    yield
    for name, content in before.items():
        p = db / name
        if content is None:
            if p.is_file():
                p.unlink()
        else:
            p.write_bytes(content)


@pytest.fixture
def mock_llm(monkeypatch):
    """Mock the single LLM entry of auto_memory. No network."""
    import core.ai_models.engine as engine_mod

    def fake_chat(record, messages, options=None):
        content = messages[-1]["content"] if messages else ""
        if "facts" in content and "key" in content:
            # organize call (memory JSON in, deduped JSON out)
            return json.dumps({"facts": [{"key": "name", "value": "TESTNAME"}]},
                              ensure_ascii=False)
        return json.dumps({"facts": [{"key": "name", "value": "TESTNAME"},
                                      {"key": "city", "value": "TESTCITY"}]},
                          ensure_ascii=False)

    monkeypatch.setattr(engine_mod, "chat", fake_chat)


def _write_conversation(n):
    from core.paths import data_db_dir
    conv = [{"id": f"t{i}", "role": "user" if i % 2 == 0 else "assistant",
             "content": f"TEST message {i}", "ts": "2026-01-01T00:00:00Z"}
            for i in range(n)]
    (data_db_dir() / "conversation.json").write_text(
        json.dumps(conv, ensure_ascii=False), encoding="utf-8")
    (data_db_dir() / ".auto_memory_last_trigger").write_text("0", encoding="utf-8")


def test_should_trigger_every_50(guard):
    from core.memory.auto_memory import _should_trigger
    _write_conversation(49)
    assert _should_trigger() is False
    _write_conversation(50)
    assert _should_trigger() is True


def test_extract_stores_facts(guard, mock_llm):
    from core.memory import auto_memory as am
    _write_conversation(50)
    out = am.trigger_now_for_test()
    assert out["user_stored"] >= 1
    from core.memory.user_memory.service import list_all
    mem = list_all()
    vals = [v.get("value") if isinstance(v, dict) else str(v) for v in mem.values()]
    assert "TESTNAME" in vals


def test_organize_deduplicates(guard, mock_llm):
    from core.memory import auto_memory as am
    from core.memory.user_memory.service import set_value, list_all
    set_value("name", "TESTNAME")
    set_value("name_dup", "TESTNAME")
    before = list_all()
    assert len(before) == 2
    count = am._organize_memory("user")
    assert count == 1
    after = list_all()
    assert len(after) == 1


def test_no_retrigger_without_50_new(guard, mock_llm):
    from core.memory import auto_memory as am
    _write_conversation(50)
    am.trigger_now_for_test()
    from core.memory.auto_memory import _should_trigger
    assert _should_trigger() is False


def test_background_trigger_marks_done(guard, mock_llm):
    from core.memory import auto_memory as am
    from core.paths import data_db_dir
    _write_conversation(100)
    res = am.maybe_trigger_auto_memory()
    assert res is not None and res["triggered_at"] == 100
    marker = data_db_dir() / ".auto_memory_last_trigger"
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            if int(marker.read_text(encoding="utf-8").strip()) == 100:
                break
        except Exception:
            pass
        time.sleep(0.2)
    else:
        raise AssertionError("background auto-memory did not mark 100 in time")


def test_parse_facts_json_robust():
    from core.memory.auto_memory import _parse_facts_json
    assert _parse_facts_json('{"facts": [{"key": "a", "value": "b"}]}') == [{"key": "a", "value": "b"}]
    assert _parse_facts_json('```json\n{"facts": []}\n```') == []
    assert _parse_facts_json("not json at all") == []
