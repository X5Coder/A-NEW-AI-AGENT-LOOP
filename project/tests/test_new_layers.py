"""New-layers tests: concurrent scheduler execution, STT fallback, chat vectors.

Safe: no network (LLM/STT/embeddings are mocked), no import-time side
effects, real Data files are snapshotted and restored. The old script
version wiped scheduler.json + conversation.json at import, deleted every
real scheduled skill, made real LLM calls, and imported modules that no
longer exist (triage) — never do that again.
"""
import json
import sys
import time
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pytest

GUARD_DB_FILES = ("scheduler.json", "todos.json", "conversation.json")
GUARD_VECTOR_FILES = ("chat_embeddings.json",)


@pytest.fixture
def guard():
    from core.paths import data_db_dir, data_vector_dir
    db, vec = data_db_dir(), data_vector_dir()
    before_db = {}
    for name in GUARD_DB_FILES:
        p = db / name
        before_db[name] = p.read_bytes() if p.is_file() else None
    before_vec = {}
    for name in GUARD_VECTOR_FILES:
        p = vec / name
        before_vec[name] = p.read_bytes() if p.is_file() else None
    proj = Path(__file__).resolve().parents[1]
    skills = proj / "skills" / "scheduled_skills"
    skills_before = set(p.name for p in skills.iterdir()) if skills.is_dir() else None
    sessions = db / "scheduler_sessions"
    sessions_before = set(p.name for p in sessions.iterdir()) if sessions.is_dir() else None
    yield
    import shutil
    for name, content in before_db.items():
        p = db / name
        if content is None:
            if p.is_file():
                p.unlink()
        else:
            p.write_bytes(content)
    for name, content in before_vec.items():
        p = vec / name
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
    import core.ai_models.aip as aip_mod
    monkeypatch.setattr(
        aip_mod, "execute",
        lambda record, canonical, kind=None: {"status": "ok", "text": "# Test Skill"})


def test_scheduler_executes_concurrently(guard, mock_skill_llm, monkeypatch):
    import time as tm
    import core.agent.loop.engine as loop_mod
    from core.scheduler.engine import check_and_execute_due_jobs
    from core.tools.scheduler.tool import execute as sch

    for i in range(3):
        sch("create", task=f"TEST concurrent job {i}", start_at="in 1m", repeat="every 1m")
    from core.paths import data_db_dir
    db = data_db_dir() / "scheduler.json"
    jobs = json.loads(db.read_text(encoding="utf-8"))
    test_jobs = [j for j in jobs if j["task"].startswith("TEST concurrent job")]
    assert len(test_jobs) == 3
    for j in test_jobs:
        j["next_run"] = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        j["status"] = "active"
    db.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")

    def fake_run(**kwargs):
        tm.sleep(0.3)
        return {"final_answer": "done", "steps": 2}

    monkeypatch.setattr(loop_mod, "run_loop", fake_run)
    start = tm.time()
    executed = check_and_execute_due_jobs(verbose=False)
    elapsed = tm.time() - start
    ours = [e for e in executed if e["job_id"] in {j["job_id"] for j in test_jobs}]
    assert len(ours) == 3
    assert elapsed < 0.85, f"expected parallel (~0.3s), took {elapsed:.2f}s"


def _make_wav(path: Path):
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 8000)


def test_stt_transcribe_wiring_without_network(guard, monkeypatch, tmp_path):
    import core.ai_models.engine as engine_mod
    import core.ai_models.store as store_mod
    from core.agent.filters.stt_correction import transcribe_audio
    # No STT model is configured in this environment; stub the lookup so the
    # test exercises local wiring (file read + call shape), never the network.
    monkeypatch.setattr(store_mod, "require_model", lambda kind: {"model_name": "TEST"})
    monkeypatch.setattr(engine_mod, "transcribe", lambda record, inputs, options=None: "TEST spoken words")
    wav = tmp_path / "t.wav"
    _make_wav(wav)
    assert transcribe_audio(wav) == "TEST spoken words"


def test_stt_correction_falls_back_without_model(guard, monkeypatch):
    import core.ai_models.store as store_mod
    from core.agent.filters.stt_correction import correct_text
    monkeypatch.setattr(store_mod, "get_text_model", lambda: None)
    clean = correct_text("امم اهلا كيف حالك امم")
    assert "اهلا" in clean
    assert "امم" not in clean
    assert correct_text("   ") == ""


def _fake_vectors(monkeypatch):
    import core.embedding.service as emb_mod

    def vec_for(text):
        t = text.lower()
        return [1.0, 0.0] if "testpython" in t else [0.0, 1.0]

    monkeypatch.setattr(emb_mod, "embed_texts", lambda texts: [vec_for(t) for t in texts])
    monkeypatch.setattr(emb_mod, "embed_query", vec_for)


def test_chat_vector_crud_and_search(guard, monkeypatch):
    _fake_vectors(monkeypatch)
    from core.agent.memory import chat_vector_store as cvs
    from core.paths import data_db_dir
    cvs.clear_all()
    conv_path = data_db_dir() / "conversation.json"
    msgs = [
        {"id": "tmsg1", "role": "user", "content": "I love testpython coding", "ts": "2026-01-01T00:00:00Z"},
        {"id": "tmsg2", "role": "assistant", "content": "testpython is great", "ts": "2026-01-01T00:00:01Z"},
        {"id": "tmsg3", "role": "user", "content": "how to cook pasta", "ts": "2026-01-01T00:00:02Z"},
    ]
    conv_path.write_text(json.dumps(msgs, ensure_ascii=False), encoding="utf-8")
    synced = cvs.ensure_synced()
    assert synced["vector_count"] == 3, synced
    res = cvs.search("testpython code", k=2)
    assert res and res[0]["message"]["id"] in ("tmsg1", "tmsg2")
    assert cvs.delete_message("tmsg2") is True
    res2 = cvs.search("testpython", k=5)
    assert all(r["message"]["id"] != "tmsg2" for r in res2)
    cvs.update_message("tmsg3", "how to cook pasta with testsauce")
    res3 = cvs.search("testsauce", k=5)
    assert any(r["message"]["id"] == "tmsg3" for r in res3)


def test_vector_chat_search_tool_uses_store(guard, monkeypatch):
    _fake_vectors(monkeypatch)
    from core.agent.memory import chat_vector_store as cvs
    from core.tools.vector_chat_search.tool import execute as vcs
    from core.paths import data_db_dir
    cvs.clear_all()
    (data_db_dir() / "conversation.json").write_text(json.dumps(
        [{"id": "tq1", "role": "user", "content": "testpython question", "ts": "2026-01-01T00:00:00Z"}],
        ensure_ascii=False), encoding="utf-8")
    cvs.ensure_synced()
    r = vcs("testpython", k=2)
    assert r["results"], "tool must return store results"
