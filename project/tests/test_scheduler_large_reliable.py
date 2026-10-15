"""Scheduler reliability tests against CURRENT behavior.

Every scheduled job (small or large) creates a Skill via the internal LLM
path and always executes isolated (no chat). No network calls here: the
interpreter is mocked. No import-time side effects: nothing runs at import,
real Data files are snapshotted and restored, created skills/sessions are
removed. The old script version of this file wiped scheduler.json at import
and made real LLM calls — never do that again.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pytest

GUARD_DB_FILES = ("scheduler.json", "todos.json", "conversation.json")


@pytest.fixture
def guard():
    from core.paths import data_db_dir
    db = data_db_dir()
    before = {}
    for name in GUARD_DB_FILES:
        p = db / name
        before[name] = p.read_bytes() if p.is_file() else None
    proj = Path(__file__).resolve().parents[1]
    skills = proj / "skills" / "scheduled_skills"
    skills_before = set(p.name for p in skills.iterdir()) if skills.is_dir() else None
    sessions = db / "scheduler_sessions"
    sessions_before = set(p.name for p in sessions.iterdir()) if sessions.is_dir() else None
    yield
    import shutil
    for name, content in before.items():
        p = db / name
        if content is None:
            if p.is_file():
                p.unlink()
        else:
            p.write_bytes(content)
    if skills_before is None:
        if skills.is_dir():
            shutil.rmtree(str(skills))
    else:
        if skills.is_dir():
            for p in skills.iterdir():
                if p.name not in skills_before:
                    shutil.rmtree(str(p)) if p.is_dir() else p.unlink()
    if sessions_before is None:
        if sessions.is_dir():
            shutil.rmtree(str(sessions))
    else:
        if sessions.is_dir():
            for p in sessions.iterdir():
                if p.name not in sessions_before:
                    p.unlink()


@pytest.fixture
def mock_skill_llm(monkeypatch):
    """Mock the interpreter (AIP path). No network, deterministic skill text."""
    import core.ai_models.aip as aip_mod

    seen = {}

    def fake_execute(record, canonical, kind=None, cancel_event=None):
        seen["kind"] = kind
        seen["canonical"] = canonical
        return {"status": "ok", "text": "# Test Skill\n\n## Steps\n1. Do the task."}

    monkeypatch.setattr(aip_mod, "execute", fake_execute)
    return seen


def _create(sch, **kwargs):
    return sch("create", **kwargs)


def test_create_small_task_builds_skill(guard, mock_skill_llm):
    from core.tools.scheduler.tool import execute as sch
    r = _create(sch, task="TEST task small ping", repeat="daily at 07:00")
    job = r["job"]
    assert r["skill_created"] is True and r["skill_ref"]
    assert job["context_mode"] == "isolated"
    assert job["skill_content_preview"].startswith("# Test Skill")
    assert mock_skill_llm["kind"] == "text"
    # Chat context + task are composed into the prompt (last-10 + task).
    user_msg = mock_skill_llm["canonical"]["messages"][1]["content"]
    assert "TEST task small ping" in user_msg


def test_create_with_matching_todo_attaches_snapshot(guard, mock_skill_llm):
    from core.tools.scheduler.tool import execute as sch
    from core.tools.todo_list.tool import execute as todo
    t = todo("TEST sales analysis", ["read file", "summarize", "send report"])
    task = "TEST please TEST sales analysis of the file and send TEST report"
    r = _create(sch, task=task, repeat="daily at 08:00", timezone="Africa/Cairo")
    job = r["job"]
    assert job["task_bundle"]["todo_snapshot"] is not None
    assert job["is_large"] is True
    proj = Path(__file__).resolve().parents[1]
    skill_data = json.loads((proj / "skills" / "scheduled_skills" / r["skill_ref"] / "skill.json").read_text(encoding="utf-8"))
    assert skill_data["instructions"].startswith("# Test Skill")
    assert "Todo plan" in mock_skill_llm["canonical"]["messages"][1]["content"]


def test_create_validation(guard, mock_skill_llm):
    from core.tools.scheduler.tool import execute as sch
    with pytest.raises(ValueError):
        _create(sch, task="   ")
    with pytest.raises(ValueError):
        _create(sch, task="x" * 2001)


def test_skill_generation_legacy_record_path(guard, monkeypatch):
    """Legacy connection-JSON records generate through the legacy engine."""
    import core.ai_models.store as store_mod
    import core.ai_models.engine as engine_mod
    from core.tools.scheduler.tool import execute as sch

    legacy = {"model_name": "m", "server_url": "http://127.0.0.1:9",
              "config_json": {"kind": "text", "version": 1}}
    monkeypatch.setattr(store_mod, "require_text_model", lambda: legacy)

    def fake_chat(record, messages, options=None):
        assert options.get("system"), "system prompt must reach legacy engine"
        return "# Legacy Skill"

    monkeypatch.setattr(engine_mod, "chat", fake_chat)
    r = _create(sch, task="TEST legacy path task")
    assert r["skill_created"] is True
    assert r["job"]["skill_content_preview"].startswith("# Legacy Skill")


def test_context_builder_isolated_vs_normal():
    from core.agent.context.builder import build_context
    sys_p, user_normal, tools = build_context("test")
    assert "messages - L6" in user_normal
    todo_snapshot = {"task": "t", "steps": ["a", "b"]}
    sys_i, user_iso, tools_i = build_context("TEST task", isolated=True, todo_snapshot=todo_snapshot)
    assert "excluded for isolated" in user_iso
    assert "Scheduled Todo Snapshot" in user_iso


def _make_due(job_id):
    from core.paths import data_db_dir
    db = data_db_dir() / "scheduler.json"
    jobs = json.loads(db.read_text(encoding="utf-8"))
    for j in jobs:
        if j["job_id"] == job_id:
            j["next_run"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            j["status"] = "active"
    db.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")


def test_due_execution_runs_skill_isolated(guard, mock_skill_llm, monkeypatch):
    import core.agent.loop.engine as loop_mod
    from core.scheduler.engine import check_and_execute_due_jobs
    from core.tools.scheduler.tool import execute as sch

    r = _create(sch, task="TEST due execution task", repeat="daily at 08:00")
    jid = r["job"]["job_id"]
    _make_due(jid)

    calls = []

    def fake_run_loop(**kwargs):
        calls.append(kwargs)
        return {"final_answer": "done", "steps": 2}

    monkeypatch.setattr(loop_mod, "run_loop", fake_run_loop)
    executed = check_and_execute_due_jobs(verbose=False)
    assert len(executed) == 1 and executed[0]["job_id"] == jid
    assert calls and calls[0].get("isolated") is True

    from core.paths import data_db_dir
    jobs = json.loads((data_db_dir() / "scheduler.json").read_text(encoding="utf-8"))
    j = next(x for x in jobs if x["job_id"] == jid)
    assert j["run_count"] == 1 and j["next_run"] is not None


def test_delete_removes_skill_and_session(guard, mock_skill_llm):
    from core.tools.scheduler.tool import execute as sch
    r = _create(sch, task="TEST delete me task")
    jid, ref = r["job"]["job_id"], r["skill_ref"]
    proj = Path(__file__).resolve().parents[1]
    assert (proj / "skills" / "scheduled_skills" / ref).is_dir()
    d = sch("delete", job_id=jid)
    assert d["skill_deleted"] is True
    assert not (proj / "skills" / "scheduled_skills" / ref).exists()


def test_update_task_regenerates_skill(guard, monkeypatch):
    import core.ai_models.aip as aip_mod
    from core.tools.scheduler.tool import execute as sch
    texts = iter(["# Skill ONE", "# Skill TWO"])

    def fake_execute(record, canonical, kind=None, cancel_event=None):
        return {"status": "ok", "text": next(texts)}

    monkeypatch.setattr(aip_mod, "execute", fake_execute)
    r = _create(sch, task="TEST original task words")
    jid, ref = r["job"]["job_id"], r["skill_ref"]
    proj = Path(__file__).resolve().parents[1]
    skill_file = proj / "skills" / "scheduled_skills" / ref / "skill.json"
    assert json.loads(skill_file.read_text(encoding="utf-8"))["instructions"].startswith("# Skill ONE")
    # update must consume the next generation (TWO), never reuse ONE
    u = sch("update", job_id=jid, task="TEST changed task words")
    assert u["skill_regenerated"] is True
    data = json.loads(skill_file.read_text(encoding="utf-8"))
    assert data["instructions"].startswith("# Skill TWO"), data["instructions"][:30]
    assert u["job"]["task_bundle"]["original_user_input"] == "TEST changed task words"
    assert u["job"]["skill_content_preview"].startswith("# Skill TWO")
    # timing-only update regenerates nothing
    u2 = sch("update", job_id=jid, repeat="daily")
    assert u2["skill_regenerated"] is False
    assert json.loads(skill_file.read_text(encoding="utf-8"))["instructions"].startswith("# Skill TWO")


def test_repeat_validation(guard, mock_skill_llm):
    from core.tools.scheduler.tool import execute as sch
    for good in ("daily", "daily at 07:00", "every 30s", "every 1m", "every 2h", "every 1d", "every day"):
        r = _create(sch, task="TEST repeat shape", repeat=good)
        assert r["job"]["repeat"] == good
        sch("delete", job_id=r["job"]["job_id"])
    for bad in ("sometimes", "daily at 25:99", "every 0m", "every -1h", "every minute", ""):
        try:
            _create(sch, task="TEST repeat shape", repeat=bad)
            raise AssertionError(f"bad repeat accepted: {bad!r}")
        except (ValueError, TypeError):
            pass


def test_engine_schedules_seconds(guard):
    from core.scheduler.engine import _compute_next_after_repeat
    from datetime import datetime, timezone
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    nxt = _compute_next_after_repeat({"repeat": "every 30s"}, base)
    assert nxt is not None and nxt.startswith("2026-01-01T12:00:30"), nxt
    assert _compute_next_after_repeat({"repeat": "every 2m"}, base).startswith("2026-01-01T12:02"), nxt
    assert _compute_next_after_repeat({"repeat": None}, base) is None
