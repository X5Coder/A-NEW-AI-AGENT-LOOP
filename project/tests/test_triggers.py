"""Trigger logic tests: 8 approved configs, inbox/outbox guards, one-time death.

Covers the final design:
1 document_search new doc | 2 vision new image | 3 video group (analyze+transcribe+extract)
4 tts new txt | 5 read_file modified | 6 write_file modified | 7 video_download urls.txt | 8 upload ready
No LLM, no watchdog, no network. Deterministic and isolated.
"""
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from core.tools.trigger.tool import _validate_filesystem_config
from core.trigger import engine as eng
from core.paths import ensure_x5agent, workspace_root

APPROVED = [
    ("filesystem_new_file", {"watch_path": "inbox/docs", "pattern": "*.pdf"}),
    ("filesystem_new_file", {"watch_path": "inbox/media", "pattern": "*.jpg"}),
    ("filesystem_new_file", {"watch_path": "inbox/media", "pattern": "*.mp4"}),
    ("filesystem_new_file", {"watch_path": "inbox/tts", "pattern": "*.txt"}),
    ("filesystem_file_modified", {"watch_path": "inbox/docs", "pattern": "*"}),
    ("filesystem_file_modified", {"watch_path": "inbox/docs", "pattern": "*.md"}),
    ("filesystem_file_modified", {"watch_path": "inbox", "pattern": "urls.txt"}),
    ("filesystem_new_file", {"watch_path": "outbox/ready_upload", "pattern": "*"}),
]

# 1. All 8 approved configs validate
for i, (tname, cfg) in enumerate(APPROVED, 1):
    out = _validate_filesystem_config(tname, dict(cfg))
    assert out["watch_path"] == cfg["watch_path"] and out["pattern"] == cfg["pattern"], (i, out)
print("8 approved configs OK")

# 2. Forbidden guards fail clearly (no silent ignore)
for bad in [
    {"watch_path": "outbox/done", "pattern": "*"},
    {"watch_path": "outbox/done/sub", "pattern": "*"},
    {"watch_path": "../Data", "pattern": "*"},
    {"watch_path": "inbox/docs", "pattern": ""},
    {"watch_path": "", "pattern": "*"},
]:
    try:
        _validate_filesystem_config("filesystem_new_file", bad)
        raise AssertionError(f"not rejected: {bad}")
    except (ValueError, TypeError):
        pass
print("forbidden guards OK")

# 3. Resolver prefers Workspace and blocks outbox/done
ensure_x5agent()
ws = workspace_root().resolve()
resolved = eng._resolve_watch_dir("inbox/docs")
assert str(resolved).startswith(str(ws)), resolved
try:
    eng._resolve_watch_dir("outbox/done")
    raise AssertionError("outbox/done resolved, must raise")
except ValueError:
    pass
print("resolver OK")

# 4. One-time death: _delete_skill_and_trigger removes trigger + skill dir
import json
import tempfile
import core.trigger.engine as E

tmp_triggers = Path(tempfile.mktemp(prefix="trig-", suffix=".json"))
tmp_skill_base = Path(tempfile.mkdtemp(prefix="trigskills-"))
tid = "deadbeef"
skill_ref = "trigger_deadbeef_demo"
(target_skill := tmp_skill_base / skill_ref).mkdir(parents=True, exist_ok=True)
(tmp_triggers).write_text(json.dumps([{
    "trigger_id": tid, "trigger_name": "filesystem_new_file",
    "task": "demo", "config": {"watch_path": "inbox/docs"},
    "skill_ref": skill_ref, "status": "active",
}], ensure_ascii=False), encoding="utf-8")

orig_load, orig_save = E._load_triggers, E._save_triggers
orig_skill_dir = E.TRIGGER_SKILLS_DIR
try:
    E._load_triggers = lambda: json.loads(tmp_triggers.read_text(encoding="utf-8"))
    E._save_triggers = lambda tr: tmp_triggers.write_text(json.dumps(tr, ensure_ascii=False), encoding="utf-8")
    E.TRIGGER_SKILLS_DIR = tmp_skill_base
    E._delete_skill_and_trigger(tid)
    remaining = json.loads(tmp_triggers.read_text(encoding="utf-8"))
    assert remaining == [], remaining
    assert not (tmp_skill_base / skill_ref).exists(), "skill dir not deleted"
finally:
    E._load_triggers, E._save_triggers = orig_load, orig_save
    E.TRIGGER_SKILLS_DIR = orig_skill_dir
print("one-time death OK")

# 5. txt separation: docs vs tts watch different dirs
docs_dir = eng._resolve_watch_dir("inbox/docs")
tts_dir = eng._resolve_watch_dir("inbox/tts")
assert docs_dir != tts_dir, (docs_dir, tts_dir)
print("docs/tts separation OK")

print("ALL TRIGGER TESTS PASSED")
