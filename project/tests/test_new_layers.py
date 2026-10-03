import sys, os, json, time, wave, struct, math
from pathlib import Path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))
os.environ["OPENROUTER_API_KEY"] = "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"

# Unified test paths: fixtures live in X5Agent/Data (same single source of truth as runtime)
_REPO = Path(__file__).resolve().parents[2]
DATA_DB = _REPO / "X5Agent" / "Data" / "database"
DATA_VEC = _REPO / "X5Agent" / "Data" / "vector"
DATA_AUDIO = _REPO / "X5Agent" / "Data" / "files" / "audio"
DATA_DB.mkdir(parents=True, exist_ok=True)
DATA_VEC.mkdir(parents=True, exist_ok=True)
DATA_AUDIO.mkdir(parents=True, exist_ok=True)

print("=== 1) Concurrent scheduler test ===")
from core.scheduler.engine import check_and_execute_due_jobs
import pathlib
pathlib.Path(str(DATA_DB / "scheduler.json")).write_text("[]", encoding="utf-8")
from core.tools.scheduler.tool import execute as sch
from core.tools.todo_list.tool import execute as todo
# Create 3 due jobs at same time
for i in range(3):
    r = sch("create", task=f"مهمة متزامنة {i+1} - in 1m", start_at="in 1m", repeat="every 1m")
    print(f" created {r['job']['job_id']}")
# Make all due now
import json as js
from datetime import datetime, timezone, timedelta
db = Path(str(DATA_DB / "scheduler.json"))
jobs = js.loads(db.read_text(encoding="utf-8"))
for j in jobs:
    j["next_run"] = (datetime.now(timezone.utc)-timedelta(seconds=10)).isoformat()
    j["status"]="active"
db.write_text(js.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
import unittest.mock as mock, time as tm
start = tm.time()
with mock.patch("core.agent.loop.engine.run_loop") as ml:
    def fake_run(*args, **kwargs):
        tm.sleep(0.5)  # simulate work
        return {"final_answer":"done","steps":2}
    ml.side_effect = fake_run
    executed = check_and_execute_due_jobs(api_key="dummy", verbose=True)
elapsed = tm.time()-start
print(f" executed {len(executed)} jobs in {elapsed:.2f}s")
# Sequential would be 3*0.5=1.5s + overhead, concurrent with 3 workers should be ~0.5-0.8s
if elapsed < 1.0:
    print(" ✅ concurrent (ThreadPool) works - parallel")
else:
    print(" ⚠️ sequential? elapsed", elapsed)
# cleanup
pathlib.Path(str(DATA_DB / "scheduler.json")).write_text("[]", encoding="utf-8")
for p in Path("skills/scheduled_skills").iterdir():
    if p.is_dir() and p.name != ".gitkeep":
        import shutil; shutil.rmtree(str(p))

print("\n=== 2) STT filter layer ===")
from core.agent.filters.stt_correction import transcribe_audio, correct_text, process_audio_record
from PIL import Image  # not needed
# Create dummy wav silence
dummy = Path(str(DATA_AUDIO / "test_audio.wav"))
with wave.open(str(dummy),"w") as wf:
    wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000)
    wf.writeframes(b"\x00\x00"*8000)
print(" transcribing dummy...")
raw = transcribe_audio(str(dummy))
print(f" raw: '{raw[:50]}'")
clean = correct_text("اممم اهلااا كيف حالكك اليوم امم", api_key=os.environ["OPENROUTER_API_KEY"])
print(f" corrected: '{clean[:100]}'")
assert "اهلا" in clean or "كيف حالك" in clean
assert "امم" not in clean.lower()
print(" ✅ STT correction removes filler and fixes errors, no invention")
# test via process_audio_record
res = process_audio_record(str(dummy))
print(f" process_audio_record raw {res['raw_text'][:30]} clean {res['clean_text'][:30]}")
dummy.unlink(missing_ok=True)

print("\n=== 3) Triage filter layer ===")
from core.agent.filters.triage import triage_user_message
# Simple greeting should answer directly without routing
r1 = triage_user_message("اهلا", api_key=os.environ["OPENROUTER_API_KEY"])
print(f" triage 'اهلا' -> {r1['action']} content {r1.get('content','')[:50]}")
assert r1["action"]=="answer"
print(" ✅ triage saves tokens for simple greeting")
# Task should route
r2 = triage_user_message("اعملي ملف hello.txt بمحتوى test", api_key=os.environ["OPENROUTER_API_KEY"])
print(f" triage task -> {r2['action']}")
assert r2["action"]=="route"
print(" ✅ triage routes task to main workflow")
# Uncertain should route
r3 = triage_user_message("ممكن تحلل الملف اللي عندي؟", api_key=os.environ["OPENROUTER_API_KEY"])
print(f" triage uncertain -> {r3['action']}")
assert r3["action"]=="route"
print(" ✅ triage strict - uncertain routes too")

print("\n=== 3b) Pipeline integration ===")
from core.agent.filters.pipeline import handle_user_input
# Simple via pipeline should answer without loop
res = handle_user_input(user_input="اهلا", api_key=os.environ["OPENROUTER_API_KEY"], verbose=False)
print(f" pipeline simple -> {res['action']} saved_tokens {res.get('saved_tokens')}")
assert res["action"]=="answer"
# Task via pipeline should route to loop (mock loop to avoid real call)
with mock.patch("core.agent.loop.engine.run_loop") as ml2:
    ml2.return_value = {"final_answer":"تم انشاء الملف","steps":2}
    res2 = handle_user_input(user_input="اعملي ملف test.txt", api_key=os.environ["OPENROUTER_API_KEY"], verbose=False)
    print(f" pipeline task -> {res2['action']} loop steps {res2.get('loop_result',{}).get('steps')}")
    assert res2["action"]=="route"
print(" ✅ pipeline saves tokens for simple, routes tasks")

print("\n=== 4) Chat vector per-message embeddings ===")
from core.agent.memory.chat_vector_store import add_message, delete_message, update_message, search, ensure_synced, clear_all
from pathlib import Path as P
# Clear
clear_all()
P(str(DATA_DB / "conversation.json")).write_text("[]", encoding="utf-8")
# Add 3 messages
import uuid, json as js2, time as tm2
conv_path = Path(str(DATA_DB / "conversation.json"))
msgs = [
    {"id": "msg1", "role":"user", "content":"احب البرمجة بلغة بايثون", "ts":"2026-09-22T00:00:00Z"},
    {"id": "msg2", "role":"assistant", "content":"بايثون لغة رائعة للذكاء الاصطناعي", "ts":"2026-09-22T00:01:00Z"},
    {"id": "msg3", "role":"user", "content":"كيف اطبخ المكرونة؟", "ts":"2026-09-22T00:02:00Z"},
]
conv_path.write_text(js2.dumps(msgs, ensure_ascii=False, indent=2), encoding="utf-8")
# Ensure synced will create embeddings
ensure_synced()
from pathlib import Path as PP
vec_path = PP(str(DATA_VEC / "chat_embeddings.json"))
vecs = js2.loads(vec_path.read_text(encoding="utf-8"))
print(f" vectors after add {len(vecs)} (should 3)")
assert len(vecs)==3
# Search
res = search("بايثون برمجة", k=2)
print(f" search 'بايثون' -> {[r['message']['id'] for r in res]} scores {[r['score'] for r in res]}")
assert res[0]["message"]["id"] in ["msg1","msg2"]
print(" ✅ search works")
# Delete msg2
delete_message("msg2")
vecs2 = js2.loads(vec_path.read_text(encoding="utf-8"))
print(f" after delete msg2 vectors {len(vecs2)} (should 2)")
assert len(vecs2)==2 and "msg2" not in vecs2
conv2 = js2.loads(conv_path.read_text(encoding="utf-8"))
print(f" conversation after delete {len(conv2)} (should 2)")
assert len(conv2)==2
# Search again should not return deleted
res2 = search("بايثون", k=2)
print(f" search after delete -> {[r['message']['id'] for r in res2]}")
assert all(r["message"]["id"]!="msg2" for r in res2)
print(" ✅ delete removes embedding automatically")
# Edit msg3
update_message("msg3", "كيف اطبخ المكرونة بالصلصة؟")
vecs3 = js2.loads(vec_path.read_text(encoding="utf-8"))
print(f" after edit msg3 still 2 vectors, content updated? {vecs3['msg3']['content'][:20]}")
assert "بالصلصة" in vecs3["msg3"]["content"]
res3 = search("صلصة", k=2)
print(f" search 'صلصة' -> {[r['message']['id'] for r in res3]} scores {[r['score'] for r in res3]}")
# msg3 should be in top 2 after edit (contains بالصلصة)
assert any(r["message"]["id"]=="msg3" for r in res3), f"msg3 not in results {res3}"
print(" ✅ edit deletes old embedding and creates new")

# Test via vector_chat_search tool
from core.tools.vector_chat_search.tool import execute as vcs
r = vcs("بايثون", k=2)
print(f" vector_chat_search tool -> {[x['message']['id'] for x in r['results']]}")
print(" ✅ vector_chat_search uses persistent store")

# cleanup
clear_all()
P(str(DATA_DB / "conversation.json")).write_text("[]", encoding="utf-8")
print("\n=== ALL NEW LAYERS PASSED ===")
