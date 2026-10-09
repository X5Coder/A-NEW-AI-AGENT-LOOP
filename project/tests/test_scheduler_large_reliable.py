import sys, json, os
from pathlib import Path
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))
os.environ.setdefault("OPENROUTER_API_KEY", "test-dummy-key")

# Unified test paths: fixtures live in X5Agent/Data (same single source of truth as runtime)
_REPO = Path(__file__).resolve().parents[2]
DATA_DB = _REPO / "X5Agent" / "Data" / "database"
DATA_DB.mkdir(parents=True, exist_ok=True)

print("=== TEST 1: مهمة صغيرة (light) ===")
from core.tools.scheduler.tool import execute as sch
# clear
sch("list")  # just to init
from pathlib import Path as P
# clean scheduler
P(str(DATA_DB / "scheduler.json")).write_text("[]", encoding="utf-8")
# small task: no todo
r = sch("create", task="ابعتلي رسالة واتس صباح الخير", repeat="daily at 07:00")
print(f" small is_large={r['is_large']} bundle={r['bundle_created']} skill={r['skill_ref']}")
assert r["is_large"]==False and r["bundle_created"]==False
print(f" next_run {r['job']['next_run']} context_mode {r['job']['context_mode']}")
assert r["job"]["context_mode"]=="light"
jid_small = r["job"]["job_id"]
print(" ✅ small task light mode correct")

print("\n=== TEST 2: مهمة كبيرة مع todo (isolated + skill) ===")
from core.tools.todo_list.tool import execute as todo
# create large todo
t = todo("تحليل مبيعات يومي شامل", ["اقرأ ملف sales.csv", "حللي البيانات", "أنشئ تقرير PDF", "أرسل التقرير واتساب"])
print(f" todo created {t['todo']['id']}")
# now schedule large task that matches todo
large_task = "كل يوم الساعة 8 الصبح حللي ملف sales.csv وطلعي تقرير مفصل وابعتيه واتساب مع تحليل شامل للاتجاهات"
r2 = sch("create", task=large_task, repeat="daily at 08:00", timezone="Africa/Cairo")
print(f" large is_large={r2['is_large']} bundle={r2['bundle_created']} skill={r2['skill_ref']}")
print(f" bundle todo {r2['job']['task_bundle']['todo_snapshot']['id'] if r2['job']['task_bundle'] else None}")
print(f" context_mode {r2['job']['context_mode']} next_run {r2['job']['next_run']}")
assert r2["is_large"]==True
assert r2["bundle_created"]==True
assert r2["skill_ref"] is not None
assert r2["job"]["context_mode"]=="isolated"
jid_large = r2["job"]["job_id"]
# verify skill exists
skill_path = project_root / "skills" / r2["skill_ref"]
print(f" skill dir exists {skill_path.exists()} skill.json { (skill_path/'skill.json').exists() }")
assert skill_path.exists()
skill_data = json.loads((skill_path/"skill.json").read_text(encoding="utf-8"))
print(f" skill content preview {skill_data['content'][:80]}")
print(" ✅ large task isolated + skill correct")

print("\n=== TEST 3: context builder isolated vs normal ===")
from core.agent.context.builder import build_context
sys_p, user_normal, tools = build_context("test")
print(f" normal contains chat? {'Last 30 messages' in user_normal}")
sys_i, user_iso, tools_i = build_context(large_task, isolated=True, todo_snapshot=t["todo"])
print(f" isolated contains chat excluded? {'excluded for isolated' in user_iso}")
print(f" isolated contains snapshot? {'Todo Snapshot' in user_iso}")
assert "excluded for isolated" in user_iso
assert "Todo Snapshot" in user_iso
print(" ✅ context builder isolated ok")

print("\n=== TEST 4: scheduler engine - due execution (mocked loop) ===")
# Make large job due now: set next_run to past
import json as js
from datetime import datetime, timezone, timedelta
db = Path(str(DATA_DB / "scheduler.json"))
jobs = js.loads(db.read_text(encoding="utf-8"))
for j in jobs:
    if j["job_id"]==jid_large:
        j["next_run"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        j["status"]="active"
print(f" set large job due past {jobs[1]['next_run'] if len(jobs)>1 else 'x'}")
db.write_text(js.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
# Mock run_loop to avoid real API
import unittest.mock as mock
with mock.patch("core.agent.loop.engine.run_loop") as mock_loop:
    mock_loop.return_value = {"final_answer": "تم تنفيذ تحليل المبيعات وإرسال التقرير", "steps": 5}
    from core.scheduler.engine import check_and_execute_due_jobs
    executed = check_and_execute_due_jobs(api_key="dummy", verbose=True)
    print(f" executed {len(executed)} jobs")
    assert len(executed)==1 and executed[0]["job_id"]==jid_large
    # verify mock called with isolated=True
    call_kwargs = mock_loop.call_args[1]
    print(f" mock called isolated={call_kwargs.get('isolated')} todo_snapshot id {call_kwargs.get('todo_snapshot',{}).get('id') if call_kwargs.get('todo_snapshot') else None}")
    assert call_kwargs.get("isolated")==True
    print(" ✅ engine used isolated mode for large task")
# verify next_run updated to tomorrow
jobs2 = js.loads(db.read_text(encoding="utf-8"))
j = next(x for x in jobs2 if x["job_id"]==jid_large)
print(f" next_run updated {j['next_run']} run_count {j['run_count']} last_run {j['last_run']}")
assert j["run_count"]==1 and j["next_run"] is not None
print(" ✅ next_run recomputed for daily")

print("\n=== TEST 5: small task due execution (light mode) ===")
# make small due
jobs = js.loads(db.read_text(encoding="utf-8"))
for j in jobs:
    if j["job_id"]==jid_small:
        j["next_run"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        j["status"]="active"
db.write_text(js.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
with mock.patch("core.agent.loop.engine.run_loop") as mock_loop2:
    mock_loop2.return_value = {"final_answer": "صباح الخير واتس", "steps": 2}
    executed = check_and_execute_due_jobs(api_key="dummy", verbose=True)
    print(f" executed {len(executed)}")
    # small should be light
    call_kwargs = mock_loop2.call_args[1]
    print(f" light isolated flag {call_kwargs.get('isolated')}")
    assert call_kwargs.get("isolated")==False or call_kwargs.get("isolated") is None
    print(" ✅ light mode correct")

print("\n=== TEST 6: real loop with isolated large task (optional, needs API) ===")
# Skipped without a real API key: dummy keys must never trigger network calls.
if os.environ.get("OPENROUTER_API_KEY", "test-dummy-key") in ("", "test-dummy-key", "dummy"):
    print(" ⏭️ skipped (no real OPENROUTER_API_KEY in env)")
else:
    from core.agent.loop.engine import run_loop
    # Use a simple large-like input but isolated
    res = run_loop(user_input="هذه مهمة مجدولة كبيرة: حلل ملف sales.csv - الخطوات: 1- اقرأ الملف 2- لخص", max_steps=4, api_key=os.environ["OPENROUTER_API_KEY"], verbose=False, isolated=True, todo_snapshot=t["todo"])
    print(f" real isolated loop steps {res['steps']} answer {res['final_answer'][:100]}")
    print(" ✅ real isolated loop works")

# cleanup
import shutil
# keep skill for demonstration but clean scheduler
# remove test jobs
jobs = [j for j in js.loads(db.read_text(encoding="utf-8")) if j["job_id"] not in [jid_small, jid_large]]
db.write_text(js.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
# clean todos: keep but remove test todo? leave
print("\n=== ALL SCHEDULER RELIABILITY TESTS PASSED ===")
print("الخلاصة: النظام البسيط (إنشاء bundle+skill تلقائيًا داخل scheduler بدون أداة إضافية للنموذج) هو الأكثر اعتمادية للمهام الكبيرة، معزول بدون شات، مع next_run دقيق.")
