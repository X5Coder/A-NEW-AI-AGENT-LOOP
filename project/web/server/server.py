"""
Local dashboard server — serves web/frontend/dashboard statically.

Responsibility: serve dashboard files locally for development/editing page-by-page.
- No hardcoded secrets
- Validates paths (prevents traversal)
- Bounded, single-threaded, explicit lifecycle
- Correct MIME types
"""

from __future__ import annotations

import http.server
import json
import mimetypes
import socketserver
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# Load .env quietly (no hardcoded secrets, no logging) — handles BOM
# New home: X5Agent/Data/.env (inside zip backup). Fallback legacy project/.env.
def _load_env():
    try:
        proj = Path(__file__).resolve().parents[2]
        candidates = [proj.parents[0] / "X5Agent" / "Data" / ".env", proj / ".env"]
        for env_path in candidates:
            try:
                if not env_path.exists():
                    continue
                text = env_path.read_text(encoding="utf-8-sig")
                for line in text.splitlines():
                    line = line.strip().lstrip("\ufeff")
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k, v = k.strip().lstrip("\ufeff"), v.strip().strip('"').strip("'")
                    if k and v and not __import__("os").environ.get(k):
                        __import__("os").environ[k] = v
            except Exception:
                continue
    except Exception:
        pass

_load_env()

# Resolve dashboard root strictly inside project
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_ROOT = PROJECT_ROOT / "web" / "frontend" / "dashboard"

DEFAULT_PORT = 7777
DEFAULT_HOST = "127.0.0.1"

SERVER_START = time.time()

# Chat-with-attachments limits (bounded, local dev).
# Documents allow up to 100MB (practically unlimited for real office/PDF
# files); bodies stay capped because the single-threaded server holds each
# request fully in memory and base64 inflates uploads by ~33%.
CHAT_MAX_BODY = 160 * 1024 * 1024
CHAT_MAX_ATTACHMENTS = 2
CHAT_MAX_IMAGE_BYTES = 8 * 1024 * 1024
CHAT_MAX_VIDEO_BYTES = 24 * 1024 * 1024
CHAT_MAX_TEXT_BYTES = 200 * 1024
CHAT_MAX_TEXT_CHARS = 12000
CHAT_MAX_DOC_BYTES = 100 * 1024 * 1024
WS_MAX_BODY_BYTES = 160 * 1024 * 1024
WS_MAX_FILE_BYTES = 100 * 1024 * 1024
CHAT_LOOP_STEPS = 500

STT_MAX_BODY = 12 * 1024 * 1024
STT_MAX_AUDIO_BYTES = 8 * 1024 * 1024

_IMAGE_MIMES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
_VIDEO_MIMES = {"video/mp4": "mp4", "video/webm": "webm", "video/quicktime": "mov", "video/x-matroska": "mkv"}
_DOC_MIMES = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-excel": "xls",
    "text/csv": "csv",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.ms-powerpoint": "ppt",
    "text/plain": "txt",
    "text/markdown": "md",
    "text/html": "html",
    "application/json": "json",
    "text/xml": "xml",
    "application/xml": "xml",
}
_AUDIO_MIMES = {
    "audio/webm": "webm",
    "audio/mp4": "m4a",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/ogg": "ogg",
    "audio/flac": "flac",
}

# Ensure common MIME types are registered (Windows may miss them)
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/html", ".html")


# Console version shown at the end of the sidebar (single source of truth,
# keep in sync with pyproject.toml and updates.json at the repo root).
APP_VERSION = "0.17.2"

UPDATE_REPO = "X5Coder/X5Agent"

# Source-only update job state (in-memory, one at a time, bounded lines).
_UPDATE_STATE = {"state": "idle", "lines": [], "done": False, "ok": False,
                 "restart_required": False}
_UPDATE_LOCK = threading.Lock()


def _update_log(line: str) -> None:
    try:
        with _UPDATE_LOCK:
            _UPDATE_STATE["lines"].append(str(line)[:200])
            del _UPDATE_STATE["lines"][:-50]
    except Exception:
        pass


def _updates_local_path():
    """Repo-root updates.json first, project-local copy as fallback. Never raises."""
    try:
        root_candidate = PROJECT_ROOT.parent / "updates.json"
        if root_candidate.is_file():
            return root_candidate
        return PROJECT_ROOT / "updates.json"
    except Exception:
        return PROJECT_ROOT / "updates.json"


def _read_updates_file(path):
    """Read + validate an updates.json file. Returns ((version, description, features), None) or (None, error)."""
    try:
        raw = Path(path).read_bytes()
    except Exception:
        return None, "missing"
    try:
        if len(raw) > 20000:
            return None, "file too large (max 20KB)"
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return None, "invalid JSON"
    if not isinstance(data, dict):
        return None, "must be a JSON object"
    version = data.get("version")
    if not isinstance(version, str) or not version.strip() or len(version) > 20:
        return None, 'needs a "version" string (e.g. "0.2.0")'
    description = data.get("description", "")
    if description is None:
        description = ""
    if not isinstance(description, str) or len(description) > 500:
        return None, 'needs a "description" string (max 500 chars)'
    features = data.get("features")
    if not isinstance(features, list):
        return None, 'needs a "features" list of strings'
    clean = [str(f)[:300] for f in features[:50] if str(f).strip()]
    return (version.strip(), description.strip(), clean), None


def _fetch_remote_updates_json(timeout: int = 10):
    """Fetch updates.json from GitHub raw (main, then master). Returns (parsed, error)."""
    import urllib.error as _err
    import urllib.request as _url

    rel = "updates.json"  # repo root — single file, never a folder
    last_error = "Could not reach GitHub (offline?)."
    for branch in ("main", "master"):
        url = f"https://raw.githubusercontent.com/{UPDATE_REPO}/{branch}/{rel}"
        try:
            req = _url.Request(url, headers={"User-Agent": "x5agent-console"})
            with _url.urlopen(req, timeout=timeout) as r:
                raw = r.read()
            import tempfile as _tf
            with _tf.NamedTemporaryFile(delete=False) as tmp:
                tmp.write(raw)
                tmp_path = tmp.name
            try:
                parsed, err = _read_updates_file(tmp_path)
            finally:
                try:
                    Path(tmp_path).unlink()
                except Exception:
                    pass
            if err is not None:
                return None, f"Remote updates.json is invalid ({err})."
            return parsed, None
        except _err.HTTPError as e:
            if e.code == 404:
                last_error = ("Remote updates.json not found on GitHub yet — "
                              "push updates.json (repo root) first.")
                continue
            return None, f"GitHub answered with HTTP {e.code}."
        except Exception:
            last_error = "Could not reach GitHub (offline?)."
    return None, last_error


def _is_newer_version(remote: str, local: str) -> bool:
    """True when the remote version is newer (numeric parts, fallback: different)."""
    try:
        def parts(v):
            out = []
            for chunk in str(v).strip().lstrip("v").split("."):
                digits = ""
                for ch in chunk:
                    if ch.isdigit():
                        digits += ch
                    else:
                        break
                out.append(int(digits) if digits else 0)
            return out
        r, l = parts(remote), parts(local)
        n = max(len(r), len(l))
        r += [0] * (n - len(r))
        l += [0] * (n - len(l))
        return r > l
    except Exception:
        return str(remote) != str(local)


def _local_version_info():
    """Local version + short commit — never raises, never empty version."""
    commit = None
    try:
        import subprocess as _sp

        root = PROJECT_ROOT
        if not (root / ".git").exists() and (root.parent / ".git").exists():
            root = root.parent
        proc = _sp.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(root),
                       capture_output=True, text=True, timeout=10)
        if proc.returncode == 0 and proc.stdout.strip():
            commit = proc.stdout.strip()[:12]
    except Exception:
        commit = None
    return APP_VERSION, commit


def _github_latest_info():
    """Local vs remote update state — never raises, always returns a dict.

    Single source of truth: updates.json at the repo root (one file, no folder).
    The check compares the local file with the same file on GitHub (raw)
    and shows its description + features list before updating.
    No GitHub releases needed.
    """
    local, local_commit = _local_version_info()

    def _base(**kw):
        out = {"local": local, "local_commit": local_commit, "latest": None,
               "up_to_date": None, "source": "updates.json",
               "notes": None, "error": None}
        out.update(kw)
        return out

    local_parsed, local_err = _read_updates_file(_updates_local_path())
    if local_parsed is None:
        return _base(error=f"Local updates.json is {local_err} — "
                           "expected updates.json at the repo root "
                           "with {version, description, features}.")
    local = local_parsed[0]
    remote_parsed, remote_err = _fetch_remote_updates_json()
    if remote_parsed is None:
        return _base(error=remote_err)
    remote_version, remote_description, remote_features = remote_parsed
    if not _is_newer_version(remote_version, local):
        return _base(latest=remote_version, up_to_date=True)
    blocks = []
    if remote_description:
        blocks.append(remote_description)
    blocks.extend(f"• {f}" for f in remote_features)
    return _base(latest=remote_version, up_to_date=False,
                 notes="\n".join(blocks) or None)


def _run_update_job() -> None:
    """Background: git pull --ff-only (tracked code only) + re-ensure embeddings. Never raises."""
    try:
        import subprocess as _sp

        root = PROJECT_ROOT
        if not (root / ".git").exists() and (root.parent / ".git").exists():
            root = root.parent
        _update_log(f"$ git pull --ff-only  ({root.name}/)")
        try:
            proc = _sp.run(["git", "pull", "--ff-only"], cwd=str(root),
                           capture_output=True, text=True, timeout=300)
        except FileNotFoundError:
            raise RuntimeError("git is not installed")
        for line in ((proc.stdout or "") + (proc.stderr or "")).splitlines()[:50]:
            if line.strip():
                _update_log(line.strip()[:200])
        if proc.returncode != 0:
            raise RuntimeError("git pull did not fast-forward (local changes?)")
        # Specs may have changed -> re-ensure embeddings (seed/rebuild, bounded).
        try:
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            import importlib.util as _il

            def _load(rel, name):
                spec = _il.spec_from_file_location(name, str(PROJECT_ROOT / rel))
                mod = _il.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return mod

            tools = _load("plugins/tool-discovery/search/store.py", "upd_tools").ensure_embeddings()
            trigs = _load("plugins/trigger-discovery/search/store.py", "upd_trigs").ensure_embeddings()
            _update_log(f"embeddings ready: {len(tools)} tools, {len(trigs)} triggers")
        except Exception as e:
            _update_log(f"embedding warmup skipped: {str(e)[:120]}")
        with _UPDATE_LOCK:
            _UPDATE_STATE.update({"state": "done", "done": True, "ok": True,
                                  "restart_required": True})
            _UPDATE_STATE["lines"].append("Done - restart the console to apply the new code.")
    except Exception as e:
        try:
            with _UPDATE_LOCK:
                _UPDATE_STATE.update({"state": "error", "done": True, "ok": False})
                _UPDATE_STATE["lines"].append(f"Update failed: {str(e)[:200]}")
        except Exception:
            pass


def _safe_json_load(path: Path, default):
    """Read JSON file safely — returns default on missing/invalid."""
    try:
        if not path.exists():
            return default
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return default
        data = json.loads(text)
        return data
    except Exception:
        return default


def _data_json(filename: str, default):
    # Unified reads: X5Agent/Data first (zip backup), legacy project/storage fallback.
    try:
        from core.paths import read_db_json, read_vector_json
        if filename in ("tool_embeddings.json", "chat_embeddings.json", "trigger_embeddings.json"):
            return read_vector_json(filename, default)
        return read_db_json(filename, default)
    except Exception:
        pass
    return default


def _get_dashboard_data() -> dict:
    """Collect real dashboard stats from storage — no secrets."""
    storage_db = PROJECT_ROOT / "storage" / "database"
    storage_vec = PROJECT_ROOT / "storage" / "vector"

    conversation = _data_json("conversation.json", _safe_json_load(storage_db / "conversation.json", []))
    todos = _data_json("todos.json", _safe_json_load(storage_db / "todos.json", []))
    scheduler = _data_json("scheduler.json", _safe_json_load(storage_db / "scheduler.json", []))
    # memories
    user_mem = _data_json("user_memory.json", _safe_json_load(storage_db / "user_memory.json", {}))
    agent_mem = _data_json("agent_memory.json", _safe_json_load(storage_db / "agent_memory.json", {}))
    image_mem = _data_json("image_memory.json", _safe_json_load(storage_db / "image_memory.json", {}))
    important_mem = _data_json("important_memory.json", _safe_json_load(storage_db / "important_memory.json", {}))

    tool_emb = _data_json("tool_embeddings.json", _safe_json_load(storage_vec / "tool_embeddings.json", {}))
    tools_count = 0
    if isinstance(tool_emb, dict) and "tools" in tool_emb and isinstance(tool_emb["tools"], list):
        tools_count = len(tool_emb["tools"])

    # counts — bounded & validated
    def _len(v):
        if isinstance(v, list):
            return len(v)
        if isinstance(v, dict):
            # image_memory / important_memory may be dict with list inside
            if "images" in v and isinstance(v["images"], list):
                return len(v["images"])
            if "facts" in v and isinstance(v["facts"], list):
                return len(v["facts"])
            return len(v)
        return 0

    total_messages = _len(conversation)
    # last message time if available
    last_activity = None
    if isinstance(conversation, list) and len(conversation) > 0:
        last = conversation[-1]
        if isinstance(last, dict):
            last_activity = last.get("timestamp") or last.get("created_at")

    elapsed = max(0, int(time.time() - SERVER_START))
    if elapsed < 60:
        uptime_str = f"{elapsed}s"
    elif elapsed < 3600:
        uptime_str = f"{elapsed // 60}m"
    elif elapsed < 86400:
        uptime_str = f"{elapsed // 3600}h {(elapsed % 3600) // 60}m"
    else:
        uptime_str = f"{elapsed // 86400}d {(elapsed % 86400) // 3600}h"

    import datetime as _dt

    return {
        "messages": total_messages,
        "todos": _len(todos),
        "jobs": _len(scheduler),
        "tools": tools_count,
        "user_memory": _len(user_mem),
        "agent_memory": _len(agent_mem),
        "images": _len(image_mem),
        "important": _len(important_mem),
        "last_activity": last_activity,
        "status": "operational",
        "uptime": uptime_str,
        "uptime_since": _dt.datetime.fromtimestamp(SERVER_START).astimezone().isoformat(),
        "version": APP_VERSION,
    }


ACTION_TITLES = {
    "store": ("Store", "Save new entries"),
    "update": ("Update", "Modify existing entries"),
    "delete": ("Delete", "Remove entries"),
    "list": ("List", "View all entries"),
    "get": ("Get", "View a single entry"),
    "clear_temp": ("Clear temp", "Remove temporary entries"),
    "clear_all": ("Clear all", "Remove everything"),
    "create": ("Create", "Create new items"),
}


def _get_plugins_data() -> dict:
    """Real plugins behind tool_search — scanned from plugins/*/spec.json.

    Permission = whole action. Multi-action tools list each action with a
    switch; single-permission tools list one row bound to the plugin switch.
    """
    display = {
        "image_memory": {"title": "Image Memory", "version": "v1.2.0", "user_desc": "Save and manage images with vision analysis.", "icon": "IM"},
        "important_memory": {"title": "Important Memory", "version": "v1.0.0", "user_desc": "Save important facts and notes for long-term memory.", "icon": "IM"},
        "read_file": {"title": "Read File", "version": "v1.1.0", "user_desc": "Read files safely with path validation.", "icon": "RF"},
        "write_file": {"title": "Write File", "version": "v1.1.0", "user_desc": "Create files with path validation.", "icon": "WF"},
        "search_files": {"title": "Search Files", "version": "v1.0.0", "user_desc": "Find files by name and content.", "icon": "SF"},
        "vision_analyze": {"title": "Vision Analyze", "version": "v2.0.0", "user_desc": "Analyze images with the connected AI Vision Model.", "icon": "VA"},
        "video_analyze": {"title": "Video Analyze", "version": "v1.3.0", "user_desc": "Analyze video frame by frame in parallel.", "icon": "VA"},
        "video_transcribe": {"title": "Video Transcribe", "version": "v1.2.0", "user_desc": "Extract video audio and transcribe with Whisper.", "icon": "VT"},
        "skill_create": {"title": "Skill Create", "version": "v1.0.0", "user_desc": "Create reusable skills for future use.", "icon": "SC"},
    }
    result = []
    try:
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from core.tools.registry import (
            get_plugin_state as _get_pstate,
            is_plugin_effective as _plugin_effective,
            model_for_plugin as _model_for_plugin,
        )

        _pstate = _get_pstate()
    except Exception:
        _pstate = {"plugins": {}, "models": {}}

        def _plugin_effective(pid):  # type: ignore
            return True

        def _model_for_plugin(pid):  # type: ignore
            return None

    def _off_reason(pname: str) -> str | None:
        if _pstate.get("plugins", {}).get(pname, {}).get("enabled") is False:
            return "plugin"
        try:
            if not _plugin_effective(pname) and _model_for_plugin(pname):
                return "model"
        except Exception:
            pass
        return None

    plugins_root = PROJECT_ROOT / "plugins"
    try:
        # core_plugins/ is always on and hidden from the UI — never listed here.
        categories = sorted([c for c in plugins_root.iterdir()
                             if c.is_dir() and c.name not in ("tool-discovery", "core_plugins")])
    except Exception:
        categories = []
    for cat in categories:
        try:
            tool_dirs = sorted([t for t in cat.iterdir() if t.is_dir() and t.name != "triggers"])
        except Exception:
            continue
        for tool_dir in tool_dirs:
            spec_path = tool_dir / "spec.json"
            if not spec_path.exists():
                continue
            try:
                spec = json.loads(spec_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            name = str(spec.get("name") or tool_dir.name)
            if name not in display:
                continue
            meta = display[name]
            desc = str(spec.get("description") or "")
            inputs = spec.get("inputs", {}) if isinstance(spec.get("inputs"), dict) else {}
            # Permission = whole action. Multi-action tools list each action;
            # single-permission tools list one row bound to the plugin switch.
            stored_perms = {}
            try:
                stored_perms = _pstate.get("plugins", {}).get(name, {}).get("permissions", {})
            except Exception:
                stored_perms = {}
            stored_plugin_on = True
            try:
                stored_plugin_on = _pstate.get("plugins", {}).get(name, {}).get("enabled", True) is not False
            except Exception:
                stored_plugin_on = True
            act_spec = inputs.get("action", {}) if isinstance(inputs, dict) else {}
            act_enum = []
            if isinstance(act_spec, dict) and isinstance(act_spec.get("enum"), list):
                act_enum = [str(x) for x in act_spec["enum"] if isinstance(x, str)]
            actions = []
            if act_enum:
                for aname in act_enum:
                    title, desc = ACTION_TITLES.get(aname, (aname.replace("_", " ").title(), f"{aname} operation"))
                    actions.append({
                        "name": aname,
                        "title": title,
                        "desc": desc,
                        "scope": "action",
                        "enabled": stored_perms.get(aname, True) is not False,
                    })
            else:
                actions.append({
                    "name": name,
                    "title": meta["title"],
                    "desc": meta["user_desc"],
                    "scope": "plugin",
                    "enabled": stored_plugin_on,
                })
            try:
                plugin_on = _plugin_effective(name)
            except Exception:
                plugin_on = True
            try:
                linked_model = _model_for_plugin(name)
            except Exception:
                linked_model = None
            result.append({
                "id": name,
                "title": meta["title"],
                "version": meta["version"],
                "description": meta["user_desc"],
                "long_description": desc,
                "icon": meta["icon"],
                "status": "active",
                "hasConfig": False,
                "actions": actions,
                "enabled": stored_plugin_on,
                "effective": bool(plugin_on),
                "model": linked_model,
                "off_reason": _off_reason(name),
            })
    result.sort(key=lambda x: x["id"])
    return {"count": len(result), "plugins": result}


# ---------- skills (user-authored, top-level only) ----------
# Lists project/skills/<name>/ — the same home skill_create writes to and
# skills_search / skills_view read from. System containers
# (scheduled_skills, trigger_skills) are owned by their engines and hidden.
# NOTE: skills_search embeds descriptions on the fly per call, so a newly
# created skill is searchable immediately — no separate embedding step.
_SKILLS_RESERVED = {"scheduled_skills", "trigger_skills"}
_SKILL_BODY_LIMIT = 20000


def _read_skill_summary(skill_dir: Path) -> dict | None:
    """Read one skill dir. Returns None for empty placeholders (only .gitkeep)."""
    try:
        files = [p for p in skill_dir.iterdir() if p.is_file()]
    except Exception:
        return None
    real = [p for p in files if p.name != ".gitkeep"]
    if not real:
        return None
    name = skill_dir.name
    description = ""
    preview = ""
    source = ""
    created_at = ""
    sj = skill_dir / "skill.json"
    if sj.exists():
        try:
            j = json.loads(sj.read_text(encoding="utf-8"))
            if isinstance(j, dict):
                name = str(j.get("name") or name)[:50]
                description = str(j.get("description") or "")[:500]
                body = str(j.get("instructions") or j.get("content") or "")
                preview = body[:800]
                created_at = str(j.get("created_at") or "")
                source = "skill.json"
        except Exception:
            pass
    if not source:
        for cand_name in ("SKILL.md", "README.md", "instructions.md"):
            cand = skill_dir / cand_name
            if cand.exists():
                try:
                    text = cand.read_text(encoding="utf-8")
                    if cand.suffix == ".md" and text.startswith("---"):
                        # tiny frontmatter scan: description: <...>
                        try:
                            head = text.split("---", 2)[1]
                            for line in head.splitlines():
                                if line.strip().lower().startswith("description:"):
                                    description = line.split(":", 1)[1].strip()[:500]
                                    break
                        except Exception:
                            pass
                    if not description:
                        stripped = text.strip()
                        description = stripped[:500]
                    preview = text.strip()[:800]
                    source = cand_name
                    break
                except Exception:
                    continue
    if not source:
        return None
    if not description:
        description = name
    return {
        "id": skill_dir.name,
        "name": name,
        "description": description,
        "preview": preview,
        "source": source,
        "created_at": created_at,
    }


def _get_skills_data() -> dict:
    """List user skills for the dashboard Skills page."""
    skills_root = PROJECT_ROOT / "skills"
    result: list[dict] = []
    try:
        children = sorted(skills_root.iterdir(), key=lambda p: p.name.lower())
    except Exception:
        return {"count": 0, "skills": []}
    for child in children:
        try:
            if not child.is_dir() or child.name in _SKILLS_RESERVED or child.name.startswith("."):
                continue
            summary = _read_skill_summary(child)
            if summary:
                result.append(summary)
        except Exception:
            continue
    result.sort(key=lambda x: x["name"].lower())
    return {"count": len(result), "skills": result}


# NOTE: the old per-provider cloud vault is gone. Chat models connect through
# AI Models templates (core/ai_models/store.py); each key is mirrored to
# Data/.env under its own AI_<KIND>_API_KEY name and never returned to the UI.

# No canned agent replies exist anywhere: when no model is connected the APIs
# below fail clearly (HTTP 503 / SSE error frame) and the UI shows an error
# state with a shortcut to Models. Every chat reply is written by the AI only.
NO_MODEL_ERROR = "No text model configured. Open AI Models → AI Text Model, press Test, then Save."


def _valid_env_name(env_name: str) -> bool:
    # Strict allowlist for .env keys: UPPER_SNAKE only (prevents line injection via crafted names).
    import re as _re
    return isinstance(env_name, str) and bool(_re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", env_name))


def _write_env_key(env_name: str, value: str) -> None:
    """Persist a UI-saved key into X5Agent/Data/.env (and os.environ). Fallback legacy. Never logs the value."""
    import os as _os

    if not _valid_env_name(env_name):
        return
    value = str(value or "").strip().splitlines()[0][:256] if str(value or "").strip() else ""
    if not value:
        return
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        return
    try:
        _os.environ[env_name] = value
    except Exception:
        pass
    try:
        data_env = PROJECT_ROOT.parents[0] / "X5Agent" / "Data" / ".env"
        try:
            data_env.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        env_path = data_env
        lines = env_path.read_text(encoding="utf-8-sig").splitlines() if env_path.exists() else []
        out: list[str] = []
        found = False
        for line in lines:
            s = line.strip()
            if s and not s.startswith("#") and "=" in s and s.split("=", 1)[0].strip() == env_name:
                out.append(f"{env_name}={value}")
                found = True
            else:
                out.append(line)
        if not found:
            out.append(f"{env_name}={value}")
        env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    except Exception:
        pass


def _remove_env_key(env_name: str) -> None:
    """Delete a UI-cleared key from os.environ AND X5Agent/Data/.env. Never logs anything."""
    import os as _os

    if not _valid_env_name(env_name):
        return
    try:
        _os.environ.pop(env_name, None)
    except Exception:
        pass
    try:
        data_env = PROJECT_ROOT.parents[0] / "X5Agent" / "Data" / ".env"
        if not data_env.exists():
            return
        lines = data_env.read_text(encoding="utf-8-sig").splitlines()
        out = [line for line in lines
               if not (line.strip() and not line.strip().startswith("#")
                       and "=" in line and line.split("=", 1)[0].strip() == env_name)]
        data_env.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
    except Exception:
        pass


def _chat_readiness() -> tuple:
    """Returns (ready, model, api_key). Ready only when the universal text model
    (AI Models section) is configured. No env/default fallback — the UI state is real."""
    try:
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from core.ai_models.store import get_text_model as _ai_get
        rec = _ai_get()
        if rec is not None:
            return True, (rec.get("model_name") or "text"), None
    except Exception:
        pass
    return False, "", None


def _resolve_chat_model() -> tuple:
    """Resolve (model, api_key) for chat from the AI Models store (compat shim)."""
    ready, model, _key = _chat_readiness()
    if ready:
        return model, None
    return "", None


def _get_models_data() -> dict:
    """Dashboard compat: chat-model state from the AI Models store (universal templates)."""
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    local: list = []
    cloud: list = []
    active_id = None

    default_model, _ = _resolve_chat_model()
    ready, _, _ = _chat_readiness()
    # Source of truth is the AI Models store (universal text model); the old
    # cloud vault above is kept for backward-compat reads only.
    live = {"id": "text", "name": "AI Text Model"} if ready else None
    return {
        "local": local,
        "cloud": cloud,
        "remote": {
            "id": "text",
            "name": "AI Text Model",
            "connected": bool(ready),
            "default_model": default_model or "—",
        },
        "default_model": default_model or "—",
        "model": default_model or "—",
        "provider": "AI Text Model" if ready else "",
        "active_provider": "text" if ready else None,
        "chat_ready": bool(ready),
    }


def _get_cost_data() -> dict:
    """
    REAL cost history for last 30 days - measured, never estimated.
    Tokens/requests come from Data/database/usage_log.json (every provider call logged).
    USD comes from live OpenRouter model pricing (public /models endpoint, cached 24h).
    Days with no measured calls show 0 (honest empty, no fabricated baseline).
    """
    import datetime

    days = _get_measured_days(30)
    labels = []
    dates = []
    counts = []
    tokens = []
    daily_cost = []
    pricing, pricing_state = _get_pricing()
    for row in days:
        try:
            dt = datetime.date.fromisoformat(row["date"])
            labels.append(f"{dt.month}/{dt.day}")
        except Exception:
            labels.append(row["date"][-5:])
        dates.append(row["date"])
        counts.append(int(row["requests"]))
        tokens.append(int(row["tokens_in"]) + int(row["tokens_out"]))
        cost = 0.0
        for mdl, m in (row.get("models") or {}).items():
            pp, cp = pricing.get(str(mdl), (None, None))
            if pp is None or cp is None:
                continue
            try:
                cost += int(m.get("in") or 0) * pp + int(m.get("out") or 0) * cp
            except Exception:
                continue
        daily_cost.append(round(cost, 4))

    total = round(sum(daily_cost), 4)
    max_v = max(daily_cost) if daily_cost else 0
    # X labels for axis: pick 4 points
    x_labels = [labels[0], labels[9], labels[19], labels[29]] if len(labels) == 30 else labels

    total_requests = sum(counts)
    total_tokens = sum(tokens)
    return {
        "total": total,
        "currency": "USD",
        "max": max_v,
        "daily": daily_cost,
        "counts": counts,
        "tokens": tokens,
        "labels": labels,
        "x_labels": x_labels,
        "dates": dates,
        "unit": "USD",
        "measured": True,
        "pricing": pricing_state,
        "totals": {"requests": total_requests, "tokens": total_tokens, "cost": total},
    }


def _get_measured_days(days: int) -> list[dict]:
    """Measured per-day usage from the log (Data first, legacy fallback inside)."""
    try:
        from core.lm.usage import get_daily
        rows = get_daily(days)
        if isinstance(rows, list) and rows:
            return rows
    except Exception:
        pass
    return []


_PRICING_CACHE: dict = {"at": 0.0, "data": None}
_PRICING_TTL_SECONDS = 86400
_PRICING_URL = "https://openrouter.ai/api/v1/models"


def _get_pricing() -> tuple[dict, str]:
    """
    Live per-token USD pricing {model: (prompt_price, completion_price)}.
    Source: public OpenRouter /models (no key needed), cached 24h in Data.
    Returns (pricing, state) where state is live|cached|unavailable.
    No static fallback table - unknown models simply contribute no USD (tokens still real).
    """
    import json as _json
    import time as _time
    import urllib.request as _url

    def _cache_path():
        try:
            from core.paths import data_db_file
            return data_db_file("model_pricing.json")
        except Exception:
            return PROJECT_ROOT / "storage" / "database" / "model_pricing.json"

    now = _time.time()
    cached_mem = _PRICING_CACHE.get("data")
    if isinstance(cached_mem, dict) and now - float(_PRICING_CACHE.get("at", 0)) < _PRICING_TTL_SECONDS:
        return cached_mem, "cached"
    try:
        cp = _cache_path()
        if cp.exists() and now - cp.stat().st_mtime < _PRICING_TTL_SECONDS:
            data = _json.loads(cp.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data:
                _PRICING_CACHE["at"] = now
                _PRICING_CACHE["data"] = data
                return data, "cached"
    except Exception:
        pass

    pricing: dict = {}
    try:
        req = _url.Request(_PRICING_URL, headers={"User-Agent": "X5Agent"})
        with _url.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                payload = _json.loads(resp.read().decode("utf-8"))
                for m in payload.get("data", []) or []:
                    try:
                        mid = str(m.get("id") or "")
                        pr = (m.get("pricing") or {})
                        pp = float(pr.get("prompt"))
                        cp_ = float(pr.get("completion"))
                        if mid and pp >= 0 and cp_ >= 0:
                            pricing[mid] = (pp, cp_)
                    except Exception:
                        continue
    except Exception:
        pricing = {}
    if pricing:
        try:
            cp = _cache_path()
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(_json.dumps({k: list(v) for k, v in pricing.items()}, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        try:
            _PRICING_CACHE["at"] = now
            _PRICING_CACHE["data"] = pricing
        except Exception:
            pass
        return pricing, "live"
    if isinstance(cached_mem, dict):
        return cached_mem, "cached"
    return {}, "unavailable"


def _get_balance_data() -> dict:
    """
    Balance card: universal templates bill on their own services, so there is
    no single quota to show. Returns the connective empty state (the UI points
    to AI Models). Never exposes any key.
    """
    return {"balance": None, "currency": "USD", "unlimited": False, "label": "own models"}


class _ChatError(Exception):
    """Chat request validation failure with HTTP status."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _read_request_body(rfile, headers, max_bytes: int) -> bytes:
    """Read a request body with Content-Length or chunked framing.

    Reverse proxies (Tailscale funnel included) may forward a POST with
    Transfer-Encoding: chunked and no Content-Length. The old
    Content-Length-only reads saw an empty body there, so login arrived
    with empty credentials and answered 401 even with a correct password.
    Bounded by max_bytes in both modes. Never raises (empty on failure).
    """
    try:
        length_raw = (headers.get("Content-Length") or "").strip()
    except Exception:
        length_raw = ""
    try:
        length = int(length_raw) if length_raw else 0
    except Exception:
        length = 0
    if length > 0:
        try:
            if length > max_bytes:
                return b""
            return rfile.read(length) or b""
        except Exception:
            return b""
    try:
        enc = str(headers.get("Transfer-Encoding") or "").lower()
    except Exception:
        enc = ""
    if "chunked" not in enc:
        return b""
    try:
        out = bytearray()
        while True:
            line = rfile.readline(128)
            if not line:
                break
            try:
                size = int(line.strip().split(b";", 1)[0], 16)
            except Exception:
                break
            if size <= 0:
                try:
                    rfile.readline(128)
                except Exception:
                    pass
                break
            if len(out) + size > max_bytes:
                return b""
            try:
                chunk = rfile.read(size)
            except Exception:
                break
            if not chunk:
                break
            out += chunk
            try:
                rfile.readline(8)
            except Exception:
                pass
        return bytes(out)
    except Exception:
        return b""


def _session_cookie(token: str, clear: bool = False) -> str:
    """Session Set-Cookie value: HTTPS-safe (Secure + SameSite=None).

    The funnel serves the console over HTTPS while the server sees plain
    HTTP on loopback, so the cookie must carry Secure + SameSite=None to
    survive the browser round-trip; on plain-HTTP loopback the Secure
    attribute is simply ignored by local clients.
    """
    try:
        from web.server.console_auth import COOKIE_NAME as _cookie_name
    except Exception:
        _cookie_name = "x5sess"
    if clear:
        return f"{_cookie_name}=; HttpOnly; Path=/; SameSite=None; Secure; Max-Age=0"
    return f"{_cookie_name}={token}; HttpOnly; Path=/; SameSite=None; Secure"


# Pending loop questions: qid -> answer Queue (one outstanding per loop, bounded wait)
_PENDING_QUESTIONS: dict = {}
_PQ_LOCK = threading.Lock()

# Active chat loop cancel flag (single conversation): set by /api/chat_cancel
_ACTIVE_RUN: dict = {}
_RUN_LOCK = threading.Lock()


def _live_snapshot() -> dict:
    """The browser state + its two live links, straight from the library.

    Single source for every live/control decision in the server: the library
    mints and owns the links (stable until close/restart), so the server never
    stores, repairs or forgets a URL.
    """
    try:
        from X5Browser.service import live_links
        links = live_links()
        if isinstance(links, dict):
            return {"running": bool(links.get("running")),
                    "control": str(links.get("control") or "")[:2000],
                    "preview": str(links.get("preview") or "")[:2000],
                    "owner": str(links.get("owner") or "agent")}
    except Exception:
        pass
    return {"running": False, "control": "", "preview": "", "owner": "agent"}


# A paused live handoff needs only its question metadata in memory (the loop
# thread and this dict both survive a page refresh). qid -> {tool, view_only,
# reason}. The URL is never stored: it is read from the library.
_LIVE_PAUSE: dict = {}


def _live_pause_entry() -> dict | None:
    """The still-pending live pause, or None (answered/expired loops are gone)."""
    try:
        with _PQ_LOCK:
            for qid, meta in _LIVE_PAUSE.items():
                if qid in _PENDING_QUESTIONS:
                    return {"qid": qid, **dict(meta)}
    except Exception:
        pass
    return None


def _live_pause_clear(qid: str | None = None) -> None:
    """Forget one live pause (qid) or every one of them."""
    try:
        with _PQ_LOCK:
            if qid:
                _LIVE_PAUSE.pop(qid, None)
            else:
                _LIVE_PAUSE.clear()
    except Exception:
        pass


def _live_active() -> dict | None:
    """The live card the UI should show right now, or None.

    One shape for both cases: a paused control handoff (mode control + qid, so
    the loop can resume) and a plain preview/control session (no qid).
    """
    links = _live_snapshot()
    pause = _live_pause_entry()
    if pause is not None and links.get("control"):
        # A loop is waiting on a handoff: the interactive control card, resumable.
        return {"mode": "control", "tool": pause.get("tool") or "human_control",
                "url": links["control"], "view_only": False,
                "reason": pause.get("reason") or "", "qid": pause["qid"]}
    if not links.get("running"):
        return None
    # No paused handoff: the browser simply runs. Report the link that matches
    # who owns control (interactive for the user, watch-only for the agent) and
    # let the UI show the right-edge tab — never a blocking card by surprise.
    if links.get("owner") == "user" and links.get("control"):
        return {"mode": "control", "tool": "human_control",
                "url": links["control"], "view_only": False, "reason": ""}
    if links.get("preview"):
        return {"mode": "preview", "tool": "live_preview",
                "url": links["preview"], "view_only": True}
    return None


# Live progress of the running loop: the frames already streamed to whoever
# asked (event_start/event/question/live/live_end — never tokens). A refreshed
# page replays them, so the SAME task keeps its Working block and every step
# instead of starting blank.
_RUN_FRAMES: list = []
_RUN_FRAMES_LOCK = threading.Lock()
_RUN_FRAME_CAP = 300


def _run_frame_remember(frame: dict) -> None:
    """Append one progress frame (bounded) + fan it out to re-attached tabs."""
    if not isinstance(frame, dict):
        return
    if frame.get("type") not in ("event_start", "event", "question",
                                 "live", "live_end"):
        return
    try:
        with _RUN_FRAMES_LOCK:
            _RUN_FRAMES.append(frame)
            if len(_RUN_FRAMES) > _RUN_FRAME_CAP:
                del _RUN_FRAMES[:len(_RUN_FRAMES) - _RUN_FRAME_CAP]
    except Exception:
        pass
    try:
        _events_broadcast("run", frame)
    except Exception:
        pass


def _run_frames_clear() -> None:
    """Drop the buffered progress (a run started or ended)."""
    try:
        with _RUN_FRAMES_LOCK:
            _RUN_FRAMES.clear()
    except Exception:
        pass


def _build_stream_frame(kind: str, val: Any, out: Any) -> dict | None:
    """One SSE frame dict from a raw queue item, or None (tokens/unknown).

    Single builder shared by the stream handler (to emit) and the run thread
    (to remember progress), so the buffered progress never depends on whether
    a client is still connected.
    """
    try:
        if kind == "event_start":
            _sk, _sr, _ss = (list(out) + [None, None, None])[:3] if isinstance(out, (tuple, list)) else (out, None, None)
            frame = {"type": "event_start", "event": val, "key": _sk}
            if isinstance(_sr, str) and _sr.strip():
                frame["reason"] = _sr[:200]
            if isinstance(_ss, int):
                frame["step"] = _ss
            return frame
        if kind == "token":
            if isinstance(val, int) and isinstance(out, str) and out:
                return {"type": "token", "step": val, "text": out[:2000]}
            return None
        if kind == "live":
            return {"type": "live", "live": val}
        if kind == "live_end":
            return {"type": "live_end"}
        if kind in ("stop", "ping"):
            return None
        # Completion: out is (output, key) or (output, key, media[, reason[, step]]).
        _o, _k, _m, _r, _s = (list(out) + [None, None, None, None, None])[:5] if isinstance(out, (tuple, list)) else (out, None, None, None, None)
        frame = {"type": "event", "event": val, "output": _o, "key": _k}
        if isinstance(_r, str) and _r.strip():
            frame["reason"] = _r[:200]
        if isinstance(_s, int):
            frame["step"] = _s
        if isinstance(_m, dict) and _m:
            frame["media"] = {k: v for k, v in _m.items()
                              if k in ("shot", "stream", "audio") and isinstance(v, (str, dict))}
        return frame
    except Exception:
        return None


# ---- realtime event hub (SSE push + WS push; replaces client polling) ----
# One SSE connection per open tab (legacy) + one WS socket per open tab
# (chat + pushes over the same socket). All state changes fan out from
# _events_broadcast, so N clients cost O(1) server work: singleton watchers
# (browser 10s, dashboard 30s, workspace 15s) broadcast ONLY on change;
# instant events (live/chat/workspace-API writes) broadcast at the mutation
# point.
_EVENTS_LOCK = threading.Lock()
_EVENT_SUBS: list = []
_WS_SUBS: list = []  # live WS sockets: {"send": fn, "q": Queue}
_EVENTS_SEQ = 0
_WATCHERS_STARTED = False
_BROWSER_PUSH: dict = {"running": None, "url": None}
_WS_PUSH: dict = {"version": 0}
_DASH_PUSH_AT = 0.0
_DASH_CACHE: dict = {}  # last fanned-out dashboard bundle (hello serves it, O(1))


def _events_broadcast(etype: str, payload: dict) -> None:
    """Fan one event out to SSE subscribers + live WS sockets. Never raises."""
    global _EVENTS_SEQ
    try:
        with _EVENTS_LOCK:
            _EVENTS_SEQ += 1
            frame = {"seq": _EVENTS_SEQ, "type": etype, "data": payload or {}}
            dead = []
            for sub in _EVENT_SUBS:
                try:
                    q = sub["q"]
                    if q.full():
                        try:
                            q.get_nowait()
                        except Exception:
                            pass
                    q.put_nowait(frame)
                except Exception:
                    dead.append(sub)
            for sub in dead:
                try:
                    _EVENT_SUBS.remove(sub)
                except ValueError:
                    pass
            ws_dead = []
            for sub in _WS_SUBS:
                try:
                    q = sub["q"]
                    if q.full():
                        try:
                            q.get_nowait()
                        except Exception:
                            pass
                    q.put_nowait(frame)
                except Exception:
                    ws_dead.append(sub)
            for sub in ws_dead:
                try:
                    _WS_SUBS.remove(sub)
                except ValueError:
                    pass
    except Exception:
        pass


def _ws_send(conn, obj: dict) -> bool:
    """One JSON text frame over an authed WS socket. False when dead."""
    try:
        import json as _json

        raw = _json.dumps(obj, ensure_ascii=False)
        if len(raw.encode("utf-8")) > 8 * 1024 * 1024:
            return False
        conn.send_text(raw)
        return True
    except Exception:
        return False


def _ws_chat_answer(payload: dict) -> dict:
    """Same as POST /api/chat_answer, minus HTTP (WS op + loopback direct)."""
    try:
        qid = (payload or {}).get("id")
        if not isinstance(qid, str) or not qid:
            return {"error": "question id required"}
        with _PQ_LOCK:
            q = _PENDING_QUESTIONS.get(qid)
        if q is None:
            return {"error": "question expired or unknown"}
        decision = (payload or {}).get("decision")
        answer = (payload or {}).get("answer")
        if isinstance(decision, str) and decision in ("continue", "stop", "skip", "activate"):
            q.put({"decision": decision})
        elif isinstance(answer, str) and answer.strip():
            q.put({"answer": answer.strip()[:2000]})
        else:
            return {"error": "answer or decision required"}
        _live_pause_clear(qid)
        return {"ok": True}
    except Exception as e:
        return {"error": f"answer failed: {str(e)[:200]}"}


def _ws_chat_cancel() -> dict:
    """Same as POST /api/chat_cancel, minus HTTP. Best-effort."""
    try:
        with _RUN_LOCK:
            ev = _ACTIVE_RUN.get("event")
        if ev is not None:
            try:
                ev.set()
            except Exception:
                pass
        return {"ok": True}
    except Exception as e:
        return {"error": f"cancel failed: {str(e)[:200]}"}


def _ws_start_chat(conn, send_lock, payload: dict, gw: bool = False) -> dict:
    """Start the agent loop; progress streams as frames on THIS socket.

    Same pipeline as _handle_chat_stream (validation, busy slot, readiness,
    run thread, done/error/cancelled frames) — only the transport differs:
    frames go to this socket instead of SSE, and questions resolve via the
    chat_answer WS op (or POST /api/chat_answer from loopback).
    """
    import queue as _queue
    import uuid as _uuid

    try:
        fake_self = DashboardHandler.__new__(DashboardHandler)
        loop_input, saved_paths = DashboardHandler._build_loop_input(fake_self, payload or {})
    except _ChatError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)[:200]}

    if gw:
        try:
            with _RUN_LOCK:
                busy = "event" in _ACTIVE_RUN
        except Exception:
            busy = False
        if busy:
            try:
                for _p in saved_paths or []:
                    try:
                        Path(_p).unlink(missing_ok=True)
                    except Exception:
                        pass
            except Exception:
                pass
            return {"error": "another run is active, try again shortly", "busy": True}
    else:
        try:
            with _RUN_LOCK:
                if "event" in _ACTIVE_RUN:
                    try:
                        for _p in saved_paths or []:
                            try:
                                Path(_p).unlink(missing_ok=True)
                            except Exception:
                                pass
                    except Exception:
                        pass
                    return {"error": "another run is active, try again shortly", "busy": True}
        except Exception as e:
            return {"error": str(e)[:200]}

    ready, _, _ = _chat_readiness()
    if not ready:
        try:
            for _p in saved_paths or []:
                try:
                    Path(_p).unlink(missing_ok=True)
                except Exception:
                    pass
        except Exception:
            pass
        return {"error": NO_MODEL_ERROR, "needs_model": True, "needs_kind": "text"}

    restricted = (payload or {}).get("mode", "restricted") != "free"
    events_q: _queue.Queue = _queue.Queue(maxsize=2000)
    box: dict = {}

    def _emit(obj) -> bool:
        try:
            with send_lock:
                return _ws_send(conn, obj)
        except Exception:
            return False

    def _stage(kind: str, val, out) -> None:
        frame = _build_stream_frame(kind, val, out)
        if frame is not None:
            _run_frame_remember(frame)
        try:
            events_q.put_nowait((kind, val, out))
        except Exception:
            try:
                events_q.get_nowait()
            except Exception:
                pass
            try:
                events_q.put_nowait((kind, val, out))
            except Exception:
                pass

    def _on_question(kind: str, qpayload: dict):
        qid = _uuid.uuid4().hex[:8]
        q: _queue.Queue = _queue.Queue()
        with _PQ_LOCK:
            _PENDING_QUESTIONS[qid] = q
        frame = {
            "type": "question",
            "id": qid,
            "kind": kind,
            "text": str(qpayload.get("question") or qpayload.get("command") or "")[:2000],
            "choices": [str(c)[:200] for c in (qpayload.get("options") or [])[:4]],
            "command": str(qpayload.get("command") or "")[:500],
        }
        if kind == "needs_model":
            try:
                from core.ai_models.store import KIND_TITLES as _TITLES
                _k = str(qpayload.get("kind") or "text")
                _t = str(qpayload.get("tool") or "")
                frame["needs_kind"] = _k
                frame["tool"] = _t[:40]
                frame["text"] = f"Needs {_TITLES.get(_k, _k)} (used by {_t or 'this task'})."
            except Exception:
                frame["needs_kind"] = str(qpayload.get("kind") or "text")
                frame["tool"] = str(qpayload.get("tool") or "")[:40]
        if kind == "live":
            live_view_only = bool(qpayload.get("view_only", False))
            frame["url"] = str(qpayload.get("url") or "")[:2000]
            frame["view_only"] = live_view_only
            frame["tool"] = str(qpayload.get("tool") or "human_control")[:40]
            frame["reason"] = str(qpayload.get("reason") or "")[:500]
            with _PQ_LOCK:
                _LIVE_PAUSE[qid] = {"tool": frame["tool"],
                                    "view_only": live_view_only,
                                    "reason": frame["reason"]}
        if gw and kind in ("approval", "needs_model"):
            with _PQ_LOCK:
                _PENDING_QUESTIONS.pop(qid, None)
                _LIVE_PAUSE.pop(qid, None)
            return {"decision": "continue" if kind == "approval" else "skip"}
        if gw and kind not in ("live",):
            with _PQ_LOCK:
                _PENDING_QUESTIONS.pop(qid, None)
                _LIVE_PAUSE.pop(qid, None)
            return None
        if not _emit(frame):
            with _PQ_LOCK:
                _PENDING_QUESTIONS.pop(qid, None)
                _LIVE_PAUSE.pop(qid, None)
            return None
        try:
            return q.get(timeout=600)
        except Exception:
            _emit({"type": "question-expired", "id": qid})
            return None
        finally:
            with _PQ_LOCK:
                _PENDING_QUESTIONS.pop(qid, None)
                _LIVE_PAUSE.pop(qid, None)

    def _on_live(ev: dict) -> None:
        try:
            if not isinstance(ev, dict) or not ev.get("success"):
                return
            tool = str(ev.get("tool") or "")
            if not tool:
                return
            _events_emit_browser(force=True)
            if tool in ("agent_control", "close_browser"):
                _stage("live_end", None, None)
            elif tool in ("live_preview", "show_preview", "restart_browser", "open_browser"):
                links = _live_snapshot()
                purl = links.get("preview") if isinstance(links, dict) else ""
                if isinstance(purl, str) and purl.startswith(("http://", "https://")):
                    _stage("live", {"mode": "preview", "tool": tool,
                                    "url": purl[:2000], "view_only": True}, None)
                else:
                    active = _live_active()
                    if isinstance(active, dict) and active.get("url"):
                        _stage("live", {"mode": active.get("mode"), "tool": tool,
                                        "url": active["url"], "view_only": True}, None)
            else:
                active = _live_active()
                if isinstance(active, dict) and active.get("url"):
                    _stage("live", {"mode": active.get("mode"), "tool": tool,
                                    "url": active["url"], "view_only": True}, None)
        except Exception:
            pass

    def _run() -> None:
        cancel_event = threading.Event()
        _run_frames_clear()
        with _RUN_LOCK:
            _ACTIVE_RUN["event"] = cancel_event
            _ACTIVE_RUN["input"] = loop_input[:200]
        _events_emit_chat(gateway=gw)
        try:
            from core.agent.loop.engine import run_loop

            chat_model, chat_key = _resolve_chat_model()
            box["res"] = run_loop(
                loop_input,
                max_steps=CHAT_LOOP_STEPS,
                verbose=False,
                model=chat_model,
                api_key=chat_key,
                on_event=lambda t, o=None, k=None, m=None, r=None, s=None: _stage("event", t, (o, k, m, r, s)),
                on_tool_start=lambda label, k=None, r=None, s=None: _stage("event_start", label, (k, r, s)),
                on_token=lambda step, d: _stage("token", step, d),
                restricted=restricted,
                on_question=_on_question,
                on_live_event=_on_live,
                cancel_flag=cancel_event,
            )
        except Exception as e:
            box["err"] = e
        finally:
            with _RUN_LOCK:
                if _ACTIVE_RUN.get("event") is cancel_event:
                    _ACTIVE_RUN.pop("event", None)
                _ACTIVE_RUN.pop("input", None)
            _run_frames_clear()
            _events_emit_chat(gateway=gw)
            _events_broadcast("chat_done", {"gateway": True} if gw else {})
            try:
                _events_emit_scheduler(force=True)
            except Exception:
                pass
            events_q.put(("stop", None, None))

    threading.Thread(target=_run, daemon=True).start()

    def _pump() -> None:
        try:
            while True:
                try:
                    kind, val, out = events_q.get(timeout=25)
                except Exception:
                    if not _emit({"type": "ping"}):
                        break
                    continue
                if kind == "stop":
                    break
                frame = _build_stream_frame(kind, val, out)
                if frame is None:
                    continue
                try:
                    _md = frame.get("media")
                    _stm = _md.get("stream") if isinstance(_md, dict) else None
                    _surl = _stm.get("url") if isinstance(_stm, dict) else None
                    if isinstance(_surl, str) and _surl.startswith(("http://", "https://")):
                        if _live_pause_entry() is None:
                            _events_emit_browser(force=True)
                except Exception:
                    pass
                if not _emit(frame):
                    break
            err = box.get("err")
            _live_pause_clear()
            if err is not None:
                _emit({"type": "error", "error": "LLM request failed — connect a model in AI Models",
                       "detail": str(err)[:200]})
            else:
                res = box.get("res") or {}
                if res.get("cancelled"):
                    _emit({"type": "cancelled"})
                else:
                    final = res.get("final_answer") or ""
                    _need_kind = res.get("needs_model_kind")
                    _done = {
                        "type": "done",
                        "reply": str(final),
                        "events": DashboardHandler._extract_loop_events(None, res),
                        "steps": res.get("steps", 0),
                        "ids": res.get("message_ids"),
                        "images": res.get("final_images") or [],
                        "audio": res.get("final_audio") or [],
                        "elapsed": res.get("elapsed_sec", 0),
                    }
                    if isinstance(_need_kind, str) and _need_kind:
                        _done["needs_model"] = True
                        _done["needs_kind"] = _need_kind
                    _emit(_done)
        except Exception:
            pass
        finally:
            try:
                for _p in saved_paths or []:
                    try:
                        Path(_p).unlink(missing_ok=True)
                    except Exception:
                        pass
            except Exception:
                pass

    threading.Thread(target=_pump, daemon=True).start()
    return {"started": True}


def serve_ws(conn, peer: str = "") -> None:
    """Own one upgraded /ws socket: token auth -> chat ops + pushes -> cleanup.

    First message MUST be {"op": "auth", "token": "<entry token>"}:
    match -> auth_ok + hello, then ops flow. Anything else -> close 4401.
    The socket opens when the UI loads, closes when the tab closes; pushes
    (browser/chat/run/scheduler/...) fan out from _events_broadcast on this
    same socket, so traffic is instant and bidirectional. Never raises.
    """
    import queue as _queue

    send_lock = threading.Lock()
    authed = False
    inbox = None
    stop = threading.Event()
    try:
        try:
            conn.sock.settimeout(15.0)
        except Exception:
            pass
        try:
            from web.server.ws_proto import WsError as _WsErr

            first = conn.recv_text()
        except Exception:
            return
        finally:
            try:
                conn.sock.settimeout(None)
            except Exception:
                pass
        try:
            import json as _json

            msg = _json.loads(first)
        except Exception:
            msg = None
        token = (msg or {}).get("token") if isinstance(msg, dict) else ""
        if not isinstance(msg, dict) or msg.get("op") != "auth":
            try:
                _ws_send(conn, {"type": "auth_error", "error": "auth required"})
            except Exception:
                pass
            try:
                conn.close(4401, "auth required")
            except Exception:
                pass
            return
        try:
            from web.server import ws_auth as _wa
            from web.server import console_auth as _ca

            ok = bool(_wa.check(token))
            if not ok and not _ca.is_setup() and not (token or ""):
                ok = True  # first-run setup window: admit once to create owner
        except Exception:
            ok = False
        if not ok:
            try:
                _ws_send(conn, {"type": "auth_error", "error": "invalid token"})
            except Exception:
                pass
            try:
                conn.close(4401, "auth required")
            except Exception:
                pass
            return
        authed = True
        try:
            snap = _events_snapshot()
        except Exception:
            snap = {}
        with send_lock:
            _ws_send(conn, {"type": "auth_ok", "hello": snap})

        inbox = _queue.Queue(maxsize=64)
        with _EVENTS_LOCK:
            _WS_SUBS.append({"q": inbox})

        def _reader() -> None:
            try:
                while not stop.is_set():
                    try:
                        raw = conn.recv_text()
                    except Exception:
                        break
                    try:
                        import json as _json2

                        m = _json2.loads(raw)
                    except Exception:
                        continue
                    if not isinstance(m, dict):
                        continue
                    op = m.get("op")
                    rid = m.get("id")
                    try:
                        if op == "chat_stream":
                            data = _ws_start_chat(conn, send_lock,
                                                 m.get("params") if isinstance(m.get("params"), dict) else m,
                                                 gw=False)
                        elif op == "chat_answer":
                            data = _ws_chat_answer(m.get("params") if isinstance(m.get("params"), dict) else m)
                        elif op == "chat_cancel":
                            data = _ws_chat_cancel()
                        elif op == "chat_active":
                            with _RUN_LOCK:
                                _a = "event" in _ACTIVE_RUN
                                _pv = str(_ACTIVE_RUN.get("input") or "")[:200]
                            data = {"active": bool(_a), "input": _pv}
                        elif op == "chat_frames":
                            with _RUN_LOCK:
                                _a2 = "event" in _ACTIVE_RUN
                                _pv2 = str(_ACTIVE_RUN.get("input") or "")[:200]
                            with _RUN_FRAMES_LOCK:
                                _fr = [dict(f) for f in _RUN_FRAMES]
                            data = {"active": bool(_a2), "input": _pv2, "frames": _fr}
                        elif op == "ping":
                            data = {"pong": True}
                        else:
                            data = {"error": f"unknown op: {op}"}
                    except Exception as e:
                        data = {"error": f"op failed: {str(e)[:200]}"}
                    try:
                        with send_lock:
                            if rid is not None and op != "chat_stream":
                                _ws_send(conn, {"id": rid, "ok": "error" not in data,
                                               **({"data": data} if "error" not in data else {"error": data["error"]})})
                            elif rid is not None:
                                _ws_send(conn, {"id": rid, "ok": "error" not in data,
                                               **({"data": data} if "error" not in data else data)})
                    except Exception:
                        pass
            finally:
                try:
                    stop.set()
                except Exception:
                    pass

        reader = threading.Thread(target=_reader, daemon=True)
        reader.start()
        last_ping = time.monotonic()
        last_in = time.monotonic()
        while authed:
            try:
                try:
                    item = inbox.get(timeout=5.0)
                    last_in = time.monotonic()
                except _queue.Empty:
                    item = None
                if item is not None:
                    with send_lock:
                        if not _ws_send(conn, {"type": "push", "channel": item.get("type"),
                                              "data": item.get("data") or {}}):
                            break
                    continue
                now = time.monotonic()
                if now - last_ping >= 20.0:
                    last_ping = now
                    try:
                        conn.ping()
                    except Exception:
                        break
                    if now - last_in > 75.0:
                        break
            except Exception:
                break
    finally:
        try:
            stop.set()
        except Exception:
            pass
        try:
            if inbox is not None:
                with _EVENTS_LOCK:
                    for sub in list(_WS_SUBS):
                        try:
                            if sub.get("q") is inbox:
                                _WS_SUBS.remove(sub)
                        except Exception:
                            pass
        except Exception:
            pass
        try:
            conn.close(1000, "")
        except Exception:
            pass


def _events_snapshot() -> dict:
    """Full current state for a fresh subscriber (hello event). Read-only."""
    links = _live_snapshot()
    running = bool(links.get("running"))
    active = _live_active()
    stream = None
    if running and isinstance(active, dict) and active.get("url"):
        stream = {"mode": active.get("mode"), "tool": active.get("tool"),
                  "url": active["url"][:2000], "view_only": True}
    try:
        with _RUN_LOCK:
            chat_active = "event" in _ACTIVE_RUN
            chat_input = str(_ACTIVE_RUN.get("input") or "")[:200]
    except Exception:
        chat_active, chat_input = False, ""
    dash: dict = {}
    try:
        if _DASH_CACHE:
            dash = _DASH_CACHE
        else:
            dash = {"dashboard": _get_dashboard_data(), "cost": _get_cost_data(),
                    "balance": _get_balance_data(), "models": _get_models_data()}
    except Exception:
        pass
    try:
        sched = _get_scheduler_data()
    except Exception:
        sched = {"jobs": [], "executing": []}
    gw: dict = {"state": "idle"}
    try:
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from gateway.whatsapp import adapter as _wa

        live = _wa.status()
        gw = {"state": live.get("state", "idle"), "linked": live.get("linked", ""),
              "phone": live.get("phone", ""), "qr": bool(live.get("qr")),
              "pairing": bool(live.get("pairing")), "error": live.get("error", "")}
    except Exception:
        pass
    return {"browser": {"running": running, "stream": stream},
            "chat": {"active": chat_active, "input": chat_input},
            "workspace": {"version": _WS_PUSH.get("version", 0)},
            "dashboard": dash,
            "scheduler": sched,
            "gateway": gw}


def _events_emit_browser(force: bool = False) -> None:
    """Broadcast the browser snapshot when it changed (or when forced).

    Both the running flag and the stream URL come from the library's live links:
    no persistence and no viewer/boundary healing here — the library keeps the
    session alive and its own units self-heal.
    """
    try:
        links = _live_snapshot()
        running = bool(links.get("running"))
        active = _live_active()
        url = (active["url"][:2000]
               if isinstance(active, dict) and active.get("url") else None)
        last = _BROWSER_PUSH
        if not force and last.get("running") is running and last.get("url") == url:
            return
        last["running"] = running
        last["url"] = url
        _events_broadcast("browser", {"running": running,
                                      "stream": ({"url": url} if url else None)})
    except Exception:
        pass


def _workspace_version() -> int:
    """Top-level-only mtime fingerprint (bounded, read-only).

    Folds the virtual browser-downloads folder in too, so a finished
    download bumps the version and the UI reloads on its own (monitor)."""
    try:
        from core.paths import workspace_root as _ws
        root = _ws().resolve()
    except Exception:
        return 0
    try:
        if not root.is_dir():
            return 0
        peak = 0
        count = 0
        for p in root.iterdir():
            count += 1
            if count > 500:
                break
            try:
                stamp = p.stat().st_mtime_ns
            except Exception:
                continue
            if stamp > peak:
                peak = stamp
        try:
            from core.tools.workspace.tool import library_downloads_dir as _lib_dl
            _lib = _lib_dl()
            if _lib is not None and not (root / "downloads").exists():
                _n = 0
                for _lp in _lib.iterdir():
                    _n += 1
                    if _n > 500:
                        break
                    try:
                        _s = _lp.stat().st_mtime_ns
                    except Exception:
                        continue
                    if _s > peak:
                        peak = _s
                count += _n
        except Exception:
            pass
        return (peak % 10_000_000_000) + count
    except Exception:
        return 0


def _events_emit_workspace(force: bool = False) -> None:
    """Broadcast the workspace version when it changed (or when forced)."""
    try:
        version = _workspace_version()
        if not force and _WS_PUSH.get("version") == version:
            return
        _WS_PUSH["version"] = version
        _events_broadcast("workspace", {"version": version})
    except Exception:
        pass


def _events_emit_dashboard(force: bool = False) -> None:
    """Broadcast dashboard/cost/balance/models (30s cadence at most)."""
    global _DASH_PUSH_AT, _DASH_CACHE
    try:
        import time as _time
        now = _time.monotonic()
        if not force and now - _DASH_PUSH_AT < 30.0:
            return
        _DASH_PUSH_AT = now
        bundle = {"dashboard": _get_dashboard_data(), "cost": _get_cost_data(),
                   "balance": _get_balance_data(), "models": _get_models_data()}
        _DASH_CACHE = bundle
        _events_broadcast("dashboard", bundle)
    except Exception:
        pass


def _events_emit_chat(gateway: bool = False) -> None:
    """Broadcast run status + completion (replaces chat_active/catchup polls).

    gateway=True marks channel-driven runs so open Agent tabs render the
    visitor bubble + live steps without a refresh, then reload once.
    """
    try:
        with _RUN_LOCK:
            active = "event" in _ACTIVE_RUN
            preview = str(_ACTIVE_RUN.get("input") or "")[:200]
        payload = {"active": bool(active), "input": preview}
        if gateway:
            payload["gateway"] = True
        _events_broadcast("chat", payload)
    except Exception:
        pass


# ---- scheduler push (side panel): read-only snapshot + change-only broadcast ----
# Owner: this server module. scheduler.json stays owned by core/scheduler tool +
# engine; here we only read it and fan out a small summary so the left-edge tab
# appears the moment a job exists (no client polling anywhere).
_SCHED_PUSH: dict = {"fp": None}
_SCHED_RUNNING: set = set()
_SCHED_LOCK = threading.Lock()


def _read_scheduler_jobs() -> list:
    """Read scheduler jobs (Data first, legacy fallback). Never raises."""
    try:
        jobs = _data_json("scheduler.json", None)
        if isinstance(jobs, list):
            return jobs
    except Exception:
        pass
    try:
        legacy = PROJECT_ROOT / "storage" / "database" / "scheduler.json"
        data = _safe_json_load(legacy, [])
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def _summarize_job(job: dict) -> dict:
    """Small bounded summary per job (list view). Full skill text only on detail."""
    try:
        hist = job.get("run_history") if isinstance(job.get("run_history"), list) else []
        hist_out = []
        for h in hist[-10:]:
            if not isinstance(h, dict):
                continue
            hist_out.append({
                "at": str(h.get("at") or "")[:40],
                "success": bool(h.get("success")),
                "output": str(h.get("output") or "")[:2000],
                "assistant_msg": str(h.get("assistant_msg") or "")[:16],
            })
        return {
            "job_id": str(job.get("job_id") or "")[:64],
            "task": str(job.get("task") or "")[:200],
            "status": str(job.get("status") or "active")[:20],
            "repeat": str(job.get("repeat") or "")[:120] if job.get("repeat") else "",
            "start_at": str(job.get("start_at") or "")[:120],
            "timezone": str(job.get("timezone") or "")[:64],
            "next_run": str(job.get("next_run") or "")[:40] if job.get("next_run") else None,
            "last_run": str(job.get("last_run") or "")[:40] if job.get("last_run") else None,
            "run_count": int(job.get("run_count") or 0),
            "repeat_count": job.get("repeat_count") if isinstance(job.get("repeat_count"), int) else None,
            "skill_ref": str(job.get("skill_ref") or "")[:120],
            "skill_preview": str(job.get("skill_content_preview") or "")[:500],
            "run_history": hist_out,
        }
    except Exception:
        return {}


def _get_scheduler_data() -> dict:
    """Full scheduler snapshot for the side panel (bounded, read-only)."""
    jobs = _read_scheduler_jobs()
    try:
        with _SCHED_LOCK:
            running = sorted(_SCHED_RUNNING)
    except Exception:
        running = []
    out = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        s = _summarize_job(job)
        if not s.get("job_id"):
            continue
        s["executing"] = s["job_id"] in running
        out.append(s)
        if len(out) >= 100:
            break
    return {"jobs": out, "executing": running}


def _load_job_skill_text(skill_ref: str) -> str:
    """Full skill text ("تعليمات المهمة"), jailed + bounded. Empty on any failure."""
    try:
        ref = str(skill_ref or "")
        if not ref or len(ref) > 120:
            return ""
        base = (PROJECT_ROOT / "skills" / "scheduled_skills").resolve()
        cand = (base / ref / "skill.json").resolve()
        try:
            cand.relative_to(base)
        except ValueError:
            return ""
        if not cand.is_file() or cand.stat().st_size > 65536:
            return ""
        j = json.loads(cand.read_text(encoding="utf-8"))
        if isinstance(j, dict):
            return str(j.get("instructions") or j.get("content") or "")[:12000]
    except Exception:
        pass
    return ""


def _events_emit_scheduler(force: bool = False) -> None:
    """Broadcast the scheduler snapshot when it changed (or when forced)."""
    try:
        data = _get_scheduler_data()
        fp = json.dumps(data, ensure_ascii=False, sort_keys=True)
        if not force and _SCHED_PUSH.get("fp") == fp:
            return
        _SCHED_PUSH["fp"] = fp
        _events_broadcast("scheduler", data)
    except Exception:
        pass


def _events_watchers() -> None:
    """Start the singleton watcher threads once (on first SSE subscription)."""
    global _WATCHERS_STARTED
    with _EVENTS_LOCK:
        if _WATCHERS_STARTED:
            return
        _WATCHERS_STARTED = True

    def _browser_loop() -> None:
        import time as _time
        while True:
            try:
                _events_emit_browser()
            except Exception:
                pass
            _time.sleep(10.0)

    def _slow_loop() -> None:
        import time as _time
        while True:
            try:
                _events_emit_dashboard()
                _events_emit_workspace()
                _events_emit_scheduler()
            except Exception:
                pass
            _time.sleep(15.0)

    threading.Thread(target=_browser_loop, name="x5-events-browser", daemon=True).start()
    threading.Thread(target=_slow_loop, name="x5-events-slow", daemon=True).start()


_SCHED_LOOP_STARTED = False


def _start_scheduler_loop() -> None:
    """Start the scheduler engine ticker once (60s cadence, daemon thread).

    Tracks the live executing window via engine callbacks so the panel can
    show "يتم التنفيذ" + push every state change (start/run/finish).
    Never raises, never blocks startup.
    """
    global _SCHED_LOOP_STARTED
    try:
        with _SCHED_LOCK:
            if _SCHED_LOOP_STARTED:
                return
            _SCHED_LOOP_STARTED = True
    except Exception:
        return

    def _on_start(job_id: str) -> None:
        try:
            with _SCHED_LOCK:
                _SCHED_RUNNING.add(str(job_id))
        except Exception:
            pass
        try:
            _events_emit_scheduler(force=True)
        except Exception:
            pass

    def _on_done(job_id: str, _success: bool) -> None:
        try:
            with _SCHED_LOCK:
                _SCHED_RUNNING.discard(str(job_id))
        except Exception:
            pass
        try:
            _events_emit_scheduler(force=True)
        except Exception:
            pass

    def _loop() -> None:
        import time as _time
        while True:
            try:
                if str(PROJECT_ROOT) not in sys.path:
                    sys.path.insert(0, str(PROJECT_ROOT))
                from core.scheduler.engine import check_and_execute_due_jobs
                from core.lm.usage import get_daily as _unused  # noqa: F401 (keep lm warm)
                _model, _key = _resolve_chat_model()
                executed = check_and_execute_due_jobs(
                    api_key=_key, model=_model, verbose=False,
                    on_start=_on_start, on_done=_on_done)
                if executed:
                    try:
                        _events_emit_scheduler(force=True)
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                _time.sleep(60.0)
            except Exception:
                break

    try:
        threading.Thread(target=_loop, name="x5-scheduler", daemon=True).start()
    except Exception:
        pass


def _shots_root() -> Path:
    try:
        from core.paths import data_subdir
        return data_subdir("files", "browser")
    except Exception:
        return PROJECT_ROOT.parents[0] / "X5Agent" / "Data" / "files" / "browser"


def _audio_root() -> Path:
    try:
        from core.paths import data_subdir
        return data_subdir("files", "audio")
    except Exception:
        return PROJECT_ROOT.parents[0] / "X5Agent" / "Data" / "files" / "audio"


class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    """Serves the X5Agent console: dashboard static + JSON APIs."""

    SERVER_NAME = "X5Agent"
    SERVER_DESCRIPTION = (
        "Your private AI gateway console — chat with your agent, run local tools, "
        "manage models and connect messaging channels, all on your own machine."
    )

    def version_string(self) -> str:
        return self.SERVER_NAME

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DASHBOARD_ROOT), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/ws":
            # Safety net: /ws must be upgraded in handle() (MSG_PEEK route to
            # serve_ws) before HTTP runs. If it ever lands here, the upgrade
            # was missed — answer 426 (Upgrade Required) instead of falling
            # through to the SPA shell (200 index.html), which fails the
            # browser upgrade silently with no diagnostic.
            self.send_response(426)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if self._auth_gate(is_post=False):
            return
        if parsed.path == "/api/auth/status":
            self._handle_auth_status_get()
            return
        if parsed.path == "/api/dashboard":
            self._handle_dashboard_api()
            return
        if parsed.path == "/api/cost":
            self._handle_cost_api()
            return
        if parsed.path == "/api/balance":
            self._handle_balance_api()
            return
        if parsed.path == "/api/health":
            self._send_json({"status": "ok"})
            return
        if parsed.path == "/api/conversation":
            self._handle_conversation_get()
            return
        if parsed.path == "/api/plugins":
            self._handle_plugins_api()
            return
        if parsed.path == "/api/skills":
            self._handle_skills_api()
            return
        if parsed.path == "/api/models":
            self._handle_models_api()
            return
        if parsed.path == "/api/ai_models":
            self._handle_ai_models_get()
            return
        if parsed.path == "/api/ai_models/setup_prompt":
            self._handle_ai_models_setup_prompt_get()
            return
        if parsed.path == "/api/embedding_status":
            self._handle_embedding_status_get()
            return
        if parsed.path == "/api/public_share":
            self._handle_public_share_get()
            return
        if parsed.path == "/api/update_check":
            self._handle_update_check_get()
            return
        if parsed.path == "/api/update_progress":
            self._handle_update_progress_get()
            return
        if parsed.path == "/api/model_download":
            self._send_json({"error": "Removed — configure your model in AI Models."}, status=410)
            return
        if parsed.path == "/api/requirements":
            self._send_json({"error": "Removed — configure your model in AI Models."}, status=410)
            return
        if parsed.path == "/api/requirements/status":
            self._send_json({"error": "Removed — configure your model in AI Models."}, status=410)
            return
        if parsed.path == "/api/live_state":
            self._handle_live_state_api()
            return
        if parsed.path == "/api/chat_active":
            self._handle_chat_active_api()
            return
        if parsed.path == "/api/chat_frames":
            self._handle_chat_frames_api()
            return
        if parsed.path == "/api/browser":
            self._handle_browser_api()
            return
        if parsed.path == "/api/scheduler":
            self._handle_scheduler_get()
            return
        if parsed.path == "/api/events":
            self._handle_events_api()
            return
        if parsed.path == "/api/shot":
            self._handle_shot_get(parse_qs(parsed.query).get("path", [""])[0])
            return
        if parsed.path == "/api/audio":
            self._handle_audio_get(parse_qs(parsed.query).get("path", [""])[0])
            return
        if parsed.path == "/api/workspace":
            self._handle_workspace_api(parse_qs(parsed.query).get("path", [""])[0])
            return
        if parsed.path == "/api/workspace/download":
            self._handle_workspace_download_get(parse_qs(parsed.query).get("path", [""])[0])
            return
        if parsed.path == "/api/gateway/config":
            self._handle_gateway_config_get()
            return
        if parsed.path == "/api/gateway/whatsapp/status":
            self._handle_gateway_wa_status_get()
            return
        if parsed.path == "/api/gateway/log":
            try:
                if str(PROJECT_ROOT) not in sys.path:
                    sys.path.insert(0, str(PROJECT_ROOT))
                from gateway import dispatch as _gd
                self._send_json({"events": _gd.get_log()})
            except Exception:
                self._send_json({"error": "gateway failed"}, status=500)
            return
        # SPA fallback: real routes (/agent, /models ...) serve the console shell
        # so refresh and deep-links work. Only extensionless non-API paths rewrite.
        last_seg = parsed.path.rsplit("/", 1)[-1]
        if "." not in last_seg:
            self.path = "/index.html"
        # fallback to static
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if self._auth_gate(is_post=True):
            return
        if parsed.path == "/api/auth/setup":
            self._handle_auth_setup_post()
            return
        if parsed.path == "/api/auth/login":
            self._handle_auth_login_post()
            return
        if parsed.path == "/api/auth/logout":
            self._handle_auth_logout_post()
            return
        if parsed.path == "/api/auth/password":
            self._handle_auth_password_post()
            return
        if parsed.path == "/api/chat":
            self._handle_chat_post()
            return
        if parsed.path == "/api/chat_stream":
            self._handle_chat_stream()
            return
        if parsed.path == "/api/chat_answer":
            self._handle_chat_answer_post()
            return
        if parsed.path == "/api/chat_cancel":
            self._handle_chat_cancel_post()
            return
        if parsed.path == "/api/message":
            self._handle_message_post()
            return
        if parsed.path == "/api/stt":
            self._handle_stt_post()
            return
        if parsed.path == "/api/plugin_state":
            self._handle_plugin_state_post()
            return
        if parsed.path == "/api/model_state":
            self._send_json({"error": "Removed — local models are gone; connect models in AI Models."}, status=410)
            return
        if parsed.path == "/api/ai_models/save":
            self._handle_ai_models_save_post()
            return
        if parsed.path == "/api/ai_models/test":
            self._handle_ai_models_test_post()
            return
        if parsed.path == "/api/ai_models/clear":
            self._handle_ai_models_clear_post()
            return
        if parsed.path == "/api/update_run":
            self._handle_update_run_post()
            return
        if parsed.path == "/api/model_download":
            self._send_json({"error": "Removed — configure your model in AI Models."}, status=410)
            return
        if parsed.path == "/api/requirements/download":
            self._send_json({"error": "Removed — configure your model in AI Models."}, status=410)
            return
        if parsed.path == "/api/requirements/resume":
            self._send_json({"error": "Removed — configure your model in AI Models."}, status=410)
            return
        if parsed.path == "/api/requirements/pause":
            self._send_json({"error": "Removed — configure your model in AI Models."}, status=410)
            return
        if parsed.path == "/api/doctor":
            self._send_json({"error": "Removed — configure your model in AI Models."}, status=410)
            return
        if parsed.path == "/api/providers":
            self._send_json({"error": "Removed — configure your model in AI Models."}, status=410)
            return
        if parsed.path == "/api/providers/test":
            self._send_json({"error": "Removed — configure your model in AI Models."}, status=410)
            return
        if parsed.path == "/api/skills":
            self._handle_skill_create_post()
            return
        if parsed.path == "/api/live_state":
            self._handle_live_state_post()
            return
        if parsed.path == "/api/browser":
            self._handle_browser_api()
            return
        if parsed.path == "/api/scheduler":
            self._handle_scheduler_post()
            return
        if parsed.path == "/api/workspace":
            self._handle_workspace_post()
            return
        if parsed.path == "/api/gateway/config":
            self._handle_gateway_config_post()
            return
        if parsed.path == "/api/gateway/whatsapp/connect":
            self._handle_gateway_wa_post("connect")
            return
        if parsed.path == "/api/gateway/whatsapp/pair":
            self._handle_gateway_wa_post("pair")
            return
        if parsed.path == "/api/gateway/whatsapp/disconnect":
            self._handle_gateway_wa_post("disconnect")
            return
        if parsed.path == "/api/gateway/whatsapp/reset":
            self._handle_gateway_wa_post("reset")
            return
        if parsed.path == "/api/gateway/whatsapp/reset":
            self._handle_gateway_wa_post("reset")
            return
        if parsed.path == "/api/gateway/whatsapp/reset":
            self._handle_gateway_wa_post("reset")
            return
        if parsed.path == "/api/gateway/whatsapp/push":
            self._handle_gateway_wa_push()
            return
        if parsed.path == "/api/gateway/telegram/test":
            self._handle_gateway_tg_test_post()
            return
        self.send_error(404, "Not found")

    def _handle_live_state_post(self):
        """Dismiss a persisted preview or manual take-over.

        A paused loop handoff (control WITH a pending qid) can only resume,
        never dismiss — the loop is stopped waiting for Continue. Everything
        else (preview, manual control without qid) is dismissible, so closing
        the card and refreshing never brings it back."""
        try:
            payload = self._read_json_body(10000)
        except _ChatError as e:
            self._send_json({"error": str(e)}, status=e.status)
            return
        try:
            if payload.get("clear") is True and _live_pause_entry() is None:
                # Nothing is waiting on a pause: the UI just closes its card.
                # (A pending handoff is never dismissed — the loop is paused on it.)
                _live_pause_clear()
            self._send_json({"ok": True})
        except Exception as e:
            self._send_json({"error": "clear failed", "detail": str(e)[:200]}, status=500)

    def _handle_browser_api(self):
        """GET {running} — read-only browser probe. POST {action} — open|preview|control|close."""
        try:
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            if self.command == "GET":
                links = _live_snapshot()
                running = bool(links.get("running"))
                active = _live_active()
                stream = None
                if running and isinstance(active, dict) and active.get("url"):
                    stream = {"mode": active.get("mode"), "tool": active.get("tool"),
                              "url": active["url"][:2000],
                              "view_only": bool(active.get("view_only", True))}
                self._send_json({"running": running, "stream": stream})
                return
            try:
                payload = self._read_json_body(10000)
            except _ChatError as e:
                self._send_json({"error": str(e)}, status=e.status)
                return
            action = payload.get("action")
            if action not in ("open", "preview", "control", "close"):
                self._send_json({"error": "action open|preview|control|close required"}, status=400)
                return
            from core.agent.loop.dispatcher import execute_tool
            if action == "open":
                res = execute_tool("open_browser", {})
                out = res.get("output") if isinstance(res.get("output"), dict) else {}
                if not res.get("success"):
                    detail = str(res.get("error") or (out.get("error") if isinstance(out, dict) else "") or "")[:200]
                    if "already_running" in detail:
                        links = _live_snapshot()
                        self._send_json({"ok": True, "already_running": True,
                                         "stream": _live_active()})
                        return
                    self._send_json({"error": "open failed",
                                     "detail": detail}, status=502)
                    return
                _events_emit_browser(force=True)
                self._send_json({"ok": True, "stream": _live_active()})
                return
            if action == "control":
                # Manual take-over from the preview player: stop the agent loop
                # first (same as chat cancel), then mint the CONTROL live URL
                # and remember it so refresh restores the big in-chat card.
                try:
                    with _RUN_LOCK:
                        _ev = _ACTIVE_RUN.get("event")
                    if _ev is not None:
                        try:
                            _ev.set()
                        except Exception:
                            pass
                except Exception:
                    pass
                res = execute_tool("human_control", {"reason": "Manual take-over from preview"})
                out = res.get("output") if isinstance(res.get("output"), dict) else {}
                url = out.get("url") if isinstance(out, dict) else None
                if not res.get("success") or not isinstance(url, str) or not url:
                    detail = str(res.get("error") or (out.get("error") if isinstance(out, dict) else "") or "")[:200]
                    self._send_json({"error": "control failed",
                                     "detail": detail}, status=502)
                    return
                self._send_json({"ok": True, "url": url[:2000], "view_only": False})
                return
            if action == "preview":
                res = execute_tool("live_preview", {})
                out = res.get("output") if isinstance(res.get("output"), dict) else {}
                url = out.get("url") if isinstance(out, dict) else None
                if not res.get("success") or not isinstance(url, str) or not url:
                    detail = str(res.get("error") or (out.get("error") if isinstance(out, dict) else "") or "")[:200]
                    self._send_json({"error": "preview failed",
                                     "detail": detail}, status=502)
                    return
                self._send_json({"ok": True, "url": url[:2000]})
                return
            # Close retries up to 3 times: a lingering session would truthfully
            # reappear on the next poll/refresh, so never report success early.
            _cerr = ""
            _closed = False
            for _try in range(3):
                res = execute_tool("close_browser", {})
                if not res.get("success"):
                    _cerr = str(res.get("error") or "")[:200]
                    break
                try:
                    from X5Browser.service import is_running as _browser_running
                    _still = bool(_browser_running())
                except Exception:
                    _still = False
                if not _still:
                    _closed = True
                    break
                _cerr = "browser still running"
                try:
                    time.sleep(1.0)
                except Exception:
                    break
            if not _closed:
                self._send_json({"error": "close failed", "detail": _cerr[:200]}, status=502)
                return
            _live_pause_clear()
            _events_emit_browser(force=True)
            self._send_json({"ok": True, "closed": True})
        except Exception as e:
            self._send_json({"error": "browser failed", "detail": str(e)[:200]}, status=500)

    def _handle_scheduler_get(self):
        """GET /api/scheduler — list summaries, or ?job_id=X for one job detail.

        Detail adds the full skill text ("تعليمات المهمة", bounded 12k) read
        raw from skills/scheduled_skills/<ref>/skill.json.
        """
        try:
            try:
                qs = parse_qs(urlparse(self.path).query or "")
                wanted = (qs.get("job_id") or [""])[0]
            except Exception:
                wanted = ""
            data = _get_scheduler_data()
            if not (isinstance(wanted, str) and wanted.strip()):
                self._send_json(data)
                return
            wid = wanted.strip()[:64]
            job = next((j for j in data.get("jobs", []) if j.get("job_id") == wid), None)
            if job is None:
                self._send_json({"error": "job not found"}, status=404)
                return
            full = _load_job_skill_text(str(job.get("skill_ref") or ""))
            job["full_instructions"] = full
            # The job's own branch session (never the main chat), bounded.
            try:
                if str(PROJECT_ROOT) not in sys.path:
                    sys.path.insert(0, str(PROJECT_ROOT))
                from core.scheduler.session import load_session as _sess_load
                sess = _sess_load(wid)[-50:]
                job["session"] = [{
                    "id": str(m.get("id") or "")[:16],
                    "role": "assistant" if m.get("role") == "assistant" else "user",
                    "content": str(m.get("content") or "")[:2000],
                    "ts": str(m.get("ts") or "")[:40],
                } for m in sess if isinstance(m, dict)]
            except Exception:
                job["session"] = []
            self._send_json({"job": job})
        except Exception as e:
            self._send_json({"error": "scheduler failed", "detail": str(e)[:200]}, status=500)

    def _handle_scheduler_post(self):
        """POST /api/scheduler — {action:update_skill|delete|chat, job_id, ...}.

        update_skill edits only the skill text ("تعليمات المهمة"). delete
        removes the job + its skill + its branch session (via the scheduler
        tool, the single owner). chat continues the job's own session branch
        (never the main chat). Job timing stays owned by the agent tool.
        """
        try:
            # Sized for chat-with-attachments data URLs (same family as /api/chat).
            payload = self._read_json_body(40 * 1024 * 1024)
        except _ChatError as e:
            self._send_json({"error": str(e)}, status=e.status)
            return
        try:
            action = str(payload.get("action") or "").strip()
            if action not in ("update_skill", "delete", "chat"):
                self._send_json({"error": "action update_skill|delete|chat required"}, status=400)
                return
            if action == "delete":
                jid = payload.get("job_id")
                if not isinstance(jid, str) or not jid.strip() or len(jid.strip()) > 64:
                    self._send_json({"error": "job_id required"}, status=400)
                    return
                if str(PROJECT_ROOT) not in sys.path:
                    sys.path.insert(0, str(PROJECT_ROOT))
                from core.tools.scheduler.tool import execute as _sched_execute
                try:
                    out = _sched_execute("delete", job_id=jid.strip())
                except FileNotFoundError:
                    self._send_json({"error": "job not found"}, status=404)
                    return
                except (ValueError, TypeError) as e:
                    self._send_json({"error": str(e)[:200]}, status=400)
                    return
                _events_emit_scheduler(force=True)
                self._send_json({"ok": True, "job_id": jid.strip(),
                                 "skill_deleted": bool(out.get("skill_deleted"))})
                return
            if action == "chat":
                self._handle_scheduler_chat(payload)
                return
            jid = payload.get("job_id")
            instructions = payload.get("instructions")
            if not isinstance(jid, str) or not jid.strip() or len(jid.strip()) > 64:
                self._send_json({"error": "job_id required"}, status=400)
                return
            if not isinstance(instructions, str) or not instructions.strip() or len(instructions) > 12000:
                self._send_json({"error": "instructions required (max 12000)"}, status=400)
                return
            jobs = _read_scheduler_jobs()
            job = next((j for j in jobs if isinstance(j, dict) and j.get("job_id") == jid.strip()), None)
            if job is None:
                self._send_json({"error": "job not found"}, status=404)
                return
            ref = str(job.get("skill_ref") or "")
            if not ref:
                self._send_json({"error": "job has no skill"}, status=404)
                return
            base = (PROJECT_ROOT / "skills" / "scheduled_skills").resolve()
            target = (base / ref).resolve()
            try:
                target.relative_to(base)
            except ValueError:
                self._send_json({"error": "forbidden"}, status=403)
                return
            sj = target / "skill.json"
            if not sj.is_file():
                self._send_json({"error": "skill not found"}, status=404)
                return
            try:
                data = json.loads(sj.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    data = {}
            except Exception:
                data = {}
            text = instructions.strip()
            data["instructions"] = text
            data["content"] = text
            data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            sj.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                (target / "README.md").write_text(f"# {ref}\n\n{text}\n", encoding="utf-8")
            except Exception:
                pass
            _events_emit_scheduler(force=True)
            self._send_json({"ok": True, "job_id": jid.strip(), "skill_ref": ref})
        except Exception as e:
            self._send_json({"error": "skill save failed", "detail": str(e)[:200]}, status=500)

    def _handle_scheduler_chat(self, payload: dict):
        """Continue a job's own session branch (never the main chat).

        Expects {job_id, message, attachments?, mode?}. Runs the real agent
        loop with session_key=job_id so history + persist stay inside the
        branch session file.
        """
        saved_paths: list = []
        try:
            jid = payload.get("job_id")
            if not isinstance(jid, str) or not jid.strip() or len(jid.strip()) > 64:
                self._send_json({"error": "job_id required"}, status=400)
                return
            jid = jid.strip()
            jobs = _read_scheduler_jobs()
            job = next((j for j in jobs if isinstance(j, dict) and j.get("job_id") == jid), None)
            if job is None:
                self._send_json({"error": "job not found"}, status=404)
                return
            text = payload.get("message")
            if not isinstance(text, str) or not text.strip() or len(text.strip()) > 4000:
                self._send_json({"error": "message required (max 4000)"}, status=400)
                return
            mode = payload.get("mode", "restricted")
            restricted = mode != "free"
            skill = _load_job_skill_text(str(job.get("skill_ref") or ""))
            combined = (f"[متابعة مهمة مجدولة: {str(job.get('task') or jid)}]\n"
                        f"[تعليمات المهمة]\n{skill[:2000]}\n\n"
                        f"[رسالة المستخدم]\n{text.strip()}")[:4000]
            try:
                loop_input, saved_paths = self._build_loop_input(
                    {"message": combined, "attachments": payload.get("attachments") or []})
            except _ChatError as e:
                self._send_json({"error": str(e)}, status=e.status)
                return
            ready, _, _ = _chat_readiness()
            if not ready:
                self._cleanup_paths(saved_paths)
                self._send_json({"error": NO_MODEL_ERROR, "needs_model": True}, status=503)
                return
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            from core.agent.loop.engine import run_loop
            from core.scheduler.session import history_block as _sess_hist
            bundle = job.get("task_bundle") if isinstance(job.get("task_bundle"), dict) else {}
            todo_snap = bundle.get("todo_snapshot")
            chat_model, chat_key = _resolve_chat_model()
            result = run_loop(loop_input, max_steps=80, verbose=False,
                             model=chat_model, api_key=chat_key,
                             isolated=True, todo_snapshot=todo_snap,
                             session_key=jid,
                             session_history=_sess_hist(jid, limit=12) or None,
                             restricted=restricted)
            self._send_json({
                "ok": True,
                "reply": str(result.get("final_answer") or ""),
                "steps": result.get("steps", 0),
                "elapsed": result.get("elapsed_sec", 0),
            })
        except (ValueError, RuntimeError) as e:
            self._send_json({"error": "LLM request failed", "detail": str(e)[:200]}, status=502)
        except Exception as e:
            self._send_json({"error": "scheduler chat failed", "detail": str(e)[:200]}, status=500)
        finally:
            self._cleanup_paths(saved_paths)

    def do_OPTIONS(self):
        # CORS for local dev
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _handle_dashboard_api(self):
        try:
            data = _get_dashboard_data()
            self._send_json(data)
        except Exception as e:
            self._send_json({"error": "failed to load dashboard", "detail": str(e)}, status=500)

    def _handle_cost_api(self):
        try:
            data = _get_cost_data()
            self._send_json(data)
        except Exception as e:
            self._send_json({"error": "failed to load cost", "detail": str(e)}, status=500)

    def _handle_balance_api(self):
        try:
            data = _get_balance_data()
            self._send_json(data)
        except Exception as e:
            self._send_json({"error": "failed to load balance", "detail": str(e)}, status=500)

    def _handle_live_state_api(self):
        """The live card the UI restores after a refresh.

        Read straight from the library (running + this session's links) plus the
        in-memory live pause, if a loop is waiting on one. No file, no healing.
        """
        try:
            active = _live_active()
            pause = _live_pause_entry()
            pending = bool(pause and isinstance(active, dict)
                           and active.get("qid") == pause.get("qid"))
            self._send_json({"active": active, "pending": pending})
        except Exception as e:
            self._send_json({"error": "failed to load live state", "detail": str(e)}, status=500)

    def _check_events_auth(self) -> bool:
        """SSE guard: EventSource sends cookies, so the gate normally passes.

        A cookie-less browser tab sees the gate first (302 → ES follows to
        login.html as TEXT, never JSON). Emit a 401 JSON instead so the UI
        can redirect to login.html itself. Returns True when answered.
        """
        try:
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            from web.server import console_auth as _ca
            if not _ca.is_setup():
                return False
            if _ca.session_valid(_ca.session_from_cookie(self.headers.get("Cookie"))):
                return False
            self._send_json({"error": "login required"}, status=401)
            return True
        except Exception:
            return False

    def _handle_events_api(self):
        """Realtime push channel (SSE) — replaces all client polling.

        One stream per open tab: a `hello` snapshot first, then one frame per
        state change (browser/live/chat/chat_done/dashboard/workspace) plus a
        25s heartbeat. EventSource reconnects natively; every reconnect gets
        a fresh hello, so no last-event-id bookkeeping is needed.
        Server cost is O(1): singleton watchers broadcast only on change no
        matter how many tabs listen — the SaaS-safe shape.
        """
        if self._check_events_auth():
            return
        import queue as _queue
        sub: dict = {}
        try:
            q: _queue.Queue = _queue.Queue(maxsize=64)
            sub = {"q": q}
            with _EVENTS_LOCK:
                _EVENT_SUBS.append(sub)
            _events_watchers()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
            except Exception:
                return

            def _send(obj: dict) -> bool:
                try:
                    data = ("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8")
                    self.wfile.write(data)
                    self.wfile.flush()
                    return True
                except Exception:
                    return False

            try:
                snap = _events_snapshot()
            except Exception:
                snap = {}
            if not _send({"type": "hello", "data": snap}):
                return
            while True:
                try:
                    frame = q.get(timeout=25)
                except Exception:
                    if not _send({"type": "ping"}):
                        break
                    continue
                if not _send(frame if isinstance(frame, dict) else {"type": "ping"}):
                    break
        finally:
            try:
                with _EVENTS_LOCK:
                    if sub in _EVENT_SUBS:
                        _EVENT_SUBS.remove(sub)
            except Exception:
                pass

    def _handle_shot_get(self, raw_path: str):
        """Serve one browser screenshot. Path must resolve inside Data/files/browser."""
        try:
            if not isinstance(raw_path, str) or not raw_path.strip() or len(raw_path) > 500:
                self.send_error(400, "Bad path")
                return
            root = _shots_root().resolve()
            cand = (Path(raw_path.strip()).resolve()
                    if Path(raw_path.strip()).is_absolute()
                    else (root / raw_path.strip()).resolve())
            try:
                cand.relative_to(root)
            except ValueError:
                self.send_error(403, "Forbidden")
                return
            if cand.suffix.lower() not in (".png", ".jpg", ".jpeg") or not cand.is_file():
                self.send_error(404, "Not found")
                return
            if cand.stat().st_size > 8 * 1024 * 1024:
                self.send_error(413, "Too large")
                return
            body = cand.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png" if cand.suffix.lower() == ".png" else "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "private, max-age=3600")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            try:
                self.send_error(500, "Shot failed")
            except Exception:
                pass

    def _handle_audio_get(self, raw_path: str):
        """Serve one TTS voice record. Name must resolve inside Data/files/audio."""
        try:
            if not isinstance(raw_path, str) or not raw_path.strip() or len(raw_path) > 100:
                self.send_error(400, "Bad path")
                return
            name = Path(raw_path.strip()).name
            if not name.lower().endswith(".wav") or not name or name.startswith("."):
                self.send_error(400, "Bad path")
                return
            root = _audio_root().resolve()
            cand = (root / name).resolve()
            try:
                cand.relative_to(root)
            except ValueError:
                self.send_error(403, "Forbidden")
                return
            if not cand.is_file():
                self.send_error(404, "Not found")
                return
            if cand.stat().st_size > 8 * 1024 * 1024:
                self.send_error(413, "Too large")
                return
            body = cand.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "private, max-age=3600")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            try:
                self.send_error(500, "Audio failed")
            except Exception:
                pass

    def _workspace_fs(self, raw_path: str) -> Path:
        """Resolve a user path strictly inside X5Agent/Workspace. Raises _ChatError."""
        if not isinstance(raw_path, str) or len(raw_path) > 500:
            raise _ChatError("bad path", status=400)
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from core.paths import workspace_root as _ws
        ws = _ws()
        try:
            ws.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        ws = ws.resolve()
        s = raw_path.strip().replace("\\", "/")
        target = (ws / s).resolve() if s else ws
        try:
            target.relative_to(ws)
        except ValueError:
            raise _ChatError("forbidden: outside Workspace", status=403)
        return target

    def _workspace_entries(self, target: Path) -> dict:
        try:
            children = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except Exception:
            raise _ChatError("cannot list folder", status=500)
        entries: list[dict] = []
        for p in children[:200]:
            try:
                if p.is_dir():
                    n = 0
                    trunc = False
                    try:
                        for _ in p.iterdir():
                            n += 1
                            if n >= 1001:
                                trunc = True
                                break
                    except Exception:
                        pass
                    entries.append({"name": p.name, "type": "folder",
                                    "items": n, "items_truncated": trunc})
                elif p.is_file():
                    try:
                        st = p.stat()
                        entries.append({"name": p.name, "type": "file",
                                        "size": st.st_size, "mtime": int(st.st_mtime)})
                    except Exception:
                        entries.append({"name": p.name, "type": "file"})
            except Exception:
                continue
        return {"entries": entries, "truncated": len(children) > 200}

    def _handle_workspace_api(self, raw_path: str):
        """GET /api/workspace?path= — one folder level (jailed).

        The virtual downloads/ tree (browser downloads, read-only) merges
        here: root appends it as an external folder, deeper paths list it."""
        try:
            try:
                if str(PROJECT_ROOT) not in sys.path:
                    sys.path.insert(0, str(PROJECT_ROOT))
                from core.documents.store import purge_missing
                purge_missing()
            except Exception:
                pass
            norm = (raw_path or "").strip().replace("\\", "/")
            try:
                if str(PROJECT_ROOT) not in sys.path:
                    sys.path.insert(0, str(PROJECT_ROOT))
                from core.tools.workspace.tool import (
                    split_downloads_rel as _split_dl,
                    downloads_virtual_active as _dl_active,
                    list_downloads_virtual as _list_dl,
                )
                from core.paths import workspace_root as _wsv
                _wsroot = _wsv().resolve()
                _parts = _split_dl(norm)
                if _parts is not None and _dl_active(_wsroot):
                    data = _list_dl(_parts)
                    data["path"] = "downloads" if not _parts else "downloads/" + "/".join(_parts)
                    self._send_json(data)
                    return
            except _ChatError:
                raise
            except (ValueError, FileNotFoundError) as e:
                self._send_json({"error": str(e)[:200]}, status=404)
                return
            except Exception:
                pass
            target = self._workspace_fs(raw_path or "")
            if not target.exists() or not target.is_dir():
                self._send_json({"error": "folder not found"}, status=404)
                return
            data = self._workspace_entries(target)
            try:
                from core.paths import workspace_root as _ws
                rel = str(target.resolve().relative_to(_ws().resolve())).replace("\\", "/")
                data["path"] = "" if rel == "." else rel
            except Exception:
                data["path"] = ""
            if data.get("path") == "" and not data.get("truncated"):
                # Root only: merge the virtual downloads folder (same rule as
                # the workspace tool — hidden on real-name collision).
                try:
                    from core.tools.workspace.tool import downloads_virtual_info as _dl_info
                    from core.paths import workspace_root as _wsr
                    info = _dl_info(_wsr().resolve())
                    if info is not None:
                        entries = data.get("entries") or []
                        idx = next((i for i, e in enumerate(entries)
                                    if e.get("type") != "folder"), len(entries))
                        entries.insert(idx, info)
                except Exception:
                    pass
            self._send_json(data)
        except _ChatError as e:
            self._send_json({"error": str(e)}, status=e.status)
        except Exception:
            self._send_json({"error": "workspace failed"}, status=500)

    def _handle_workspace_post(self):
        """POST /api/workspace — mkdir or upload (both jailed, bounded)."""
        import base64 as _b64
        import uuid as _uuid
        try:
            payload = self._read_json_body(WS_MAX_BODY_BYTES)
        except _ChatError as e:
            self._send_json({"error": str(e)}, status=e.status)
            return
        try:
            action = str(payload.get("action") or "").strip()
            folder = self._workspace_fs(str(payload.get("path") or ""))
            try:
                from core.tools.workspace.tool import (
                    split_downloads_rel as _split_dl2,
                    downloads_virtual_active as _dl_active2,
                )
                from core.paths import workspace_root as _wsp
                if (_split_dl2(str(payload.get("path") or "").strip().replace("\\", "/"))
                        is not None and _dl_active2(_wsp().resolve())):
                    self._send_json({"error": "read-only: browser downloads"}, status=403)
                    return
            except _ChatError:
                raise
            except Exception:
                pass
            if action == "mkdir":
                name = str(payload.get("name") or "").strip()
                # Single name only: separators are rejected and only the exact
                # dot-names are blocked (".." inside a name, e.g. my..folder,
                # is a legal filename and cannot traverse without a separator).
                if not name or len(name) > 100 or "/" in name or "\\" in name or name in (".", ".."):
                    self._send_json({"error": "invalid folder name"}, status=400)
                    return
                dest = (folder / name).resolve()
                try:
                    from core.paths import workspace_root as _ws
                    dest.relative_to(_ws().resolve())
                except ValueError:
                    self._send_json({"error": "forbidden"}, status=403)
                    return
                if dest.exists():
                    self._send_json({"error": "already exists"}, status=409)
                    return
                dest.mkdir(parents=True, exist_ok=False)
                _events_emit_workspace(force=True)
                self._send_json({"ok": True, "path": name})
                return
            if action == "upload":
                name = str(payload.get("name") or "file")[:200]
                safe = (Path(name).name or "file").replace('"', "'")
                if not safe or safe in (".", ".."):
                    self._send_json({"error": "invalid file name"}, status=400)
                    return
                data = payload.get("data")
                if not isinstance(data, str) or not data.startswith("data:") or ";base64," not in data:
                    self._send_json({"error": "data must be a data URL"}, status=400)
                    return
                try:
                    blob = _b64.b64decode(data.split(";base64,", 1)[1], validate=True)
                except Exception:
                    self._send_json({"error": "invalid base64"}, status=400)
                    return
                if not blob or len(blob) > WS_MAX_FILE_BYTES:
                    self._send_json({"error": "file too large (max 100MB)"}, status=413)
                    return
                if not folder.is_dir():
                    self._send_json({"error": "folder not found"}, status=404)
                    return
                dest = (folder / f"{_uuid.uuid4().hex[:8]}_{safe}").resolve()
                try:
                    from core.paths import workspace_root as _ws
                    dest.relative_to(_ws().resolve())
                except ValueError:
                    self._send_json({"error": "forbidden"}, status=403)
                    return
                if dest.exists():
                    self._send_json({"error": "already exists, rename first"}, status=409)
                    return
                dest.write_bytes(blob)
                _events_emit_workspace(force=True)
                self._send_json({"ok": True, "name": dest.name, "size": len(blob)})
                return
            if action == "delete":
                name = str(payload.get("name") or "").strip().replace("\\", "/")
                # Single name only (same rule as mkdir): only the exact
                # dot-names are blocked. Limit 255 covers upload-stored names
                # ("<8-hex>_" prefix + up to 200 chars = max 209).
                if not name or len(name) > 255 or "/" in name or name in (".", ".."):
                    self._send_json({"error": "invalid file name"}, status=400)
                    return
                dest = (folder / name).resolve()
                try:
                    from core.paths import workspace_root as _ws
                    ws = _ws().resolve()
                    dest.relative_to(ws)
                    rel = str(dest.relative_to(ws)).replace("\\", "/")
                except ValueError:
                    self._send_json({"error": "forbidden"}, status=403)
                    return
                # The top-level downloads folder is essential: never delete it.
                if rel == "downloads":
                    self._send_json({"error": "protected folder"}, status=403)
                    return
                if not dest.is_file() and not dest.is_dir():
                    self._send_json({"error": "file not found"}, status=404)
                    return
                was_dir = dest.is_dir()
                if was_dir:
                    import shutil
                    shutil.rmtree(dest)
                else:
                    dest.unlink()
                removed = False
                try:
                    if str(PROJECT_ROOT) not in sys.path:
                        sys.path.insert(0, str(PROJECT_ROOT))
                    from core.documents.store import delete_file_embeddings, purge_missing
                    removed = bool(delete_file_embeddings(rel))
                    # Folder delete: drop embeddings of everything that lived inside.
                    if was_dir:
                        purge_missing()
                except Exception:
                    pass
                _events_emit_workspace(force=True)
                self._send_json({"ok": True, "name": name, "embeddings_removed": removed})
                return
            self._send_json({"error": "action mkdir|upload|delete required"}, status=400)
        except _ChatError as e:
            self._send_json({"error": str(e)}, status=e.status)
        except Exception:
            self._send_json({"error": "workspace failed"}, status=500)

    def _gw_store(self):
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from gateway import store as _gwstore
        return _gwstore

    def _handle_gateway_config_get(self):
        """Masked gateway config + verified WhatsApp live state (never spawns)."""
        try:
            store = self._gw_store()
            from gateway.whatsapp import adapter as _wa
            try:
                # Resume a saved link on page load: boots the engine only
                # when session files exist, so the badge is truthful.
                _wa.warm()
            except Exception:
                pass
            live = _wa.status()
            try:
                saved = str(store.read_config()["whatsapp"]["agent_number"] or "")
                ok, linked = _wa.verify_against(saved)
                if not ok:
                    live = {"state": "mismatch", "linked": linked}
            except Exception:
                pass
            self._send_json(store.public_config(live))
        except Exception:
            self._send_json({"error": "gateway failed"}, status=500)

    def _handle_gateway_config_post(self):
        """Save channel settings. Token accepted only inside settings.token."""
        try:
            payload = self._read_json_body(10000)
            channel = str(payload.get("channel") or "").strip()
            settings = payload.get("settings")
            if not isinstance(settings, dict):
                settings = {}
            store = self._gw_store()
            if channel == "whatsapp":
                try:
                    old_number = str(store.read_config()["whatsapp"]["agent_number"] or "")
                except Exception:
                    old_number = ""
                ok, out = store.save_whatsapp(
                    settings.get("agent_number", ""),
                    settings.get("mode", "self"),
                    settings.get("allowed", []),
                    settings,
                )
                # New agent number invalidates any previous link session.
                if ok:
                    try:
                        from gateway.whatsapp import adapter as _wa

                        new_number = str((out or {}).get("agent_number") or "")
                        if old_number and new_number and old_number != new_number:
                            _wa.disconnect()
                        else:
                            mappings = _wa.resolve_lids([new_number, *((out or {}).get("allowed") or [])])
                            for mapping in mappings:
                                store.remember_lid_mapping(mapping.get("phone"), mapping.get("lid"))
                    except Exception:
                        pass
            elif channel == "telegram":
                ok, out = store.save_telegram(
                    settings.get("token", ""),
                    settings.get("username", ""),
                    settings.get("chat_ids", []),
                )
            elif channel == "disconnect":
                target = str(settings.get("target") or "").strip()
                if target == "whatsapp":
                    from gateway.whatsapp import adapter as _wa
                    _wa.disconnect()
                ok = store.clear_channel(target)
                out = {"cleared": target} if ok else "unknown channel"
            else:
                self._send_json({"error": "channel whatsapp|telegram|disconnect required"}, status=400)
                return
            if ok:
                if channel == "whatsapp" and isinstance(out, dict):
                    # LID mappings are an internal transport detail; users
                    # manage only phone numbers in the dashboard.
                    out = {key: value for key, value in out.items() if key != "lid_map"}
                self._send_json({"ok": True, "settings": out})
            else:
                self._send_json({"error": str(out)}, status=400)
        except _ChatError as e:
            self._send_json({"error": str(e)}, status=e.status)
        except Exception:
            self._send_json({"error": "gateway failed"}, status=500)

    def _wa_live_state(self) -> dict:
        """Canonical WhatsApp link state: verified, engine flags included.

        Never spawns the engine. Enforces linked-number == saved
        agent-number: a proven mismatch wipes the session and reports
        state=mismatch (never a fake link).
        """
        try:
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            from gateway.whatsapp import adapter as _wa
            from gateway import store as _store
            saved = str(_store.read_config()["whatsapp"]["agent_number"] or "")
            ok, linked = _wa.verify_against(saved)
            out = _wa.status()
            if not ok:
                out = {
                    "state": "mismatch",
                    "phone": "",
                    "linked": linked,
                    "qr": False,
                    "qr_data_url": "",
                    "qr_at": 0,
                    "pairing": "",
                    "pairing_at": 0,
                    "error": f"Linked +{linked} is not the agent number +{saved} — session wiped. Link with the agent phone.",
                }
            out["engine"] = _wa.engine_status()
            return out
        except Exception:
            return {"state": "idle", "error": "gateway failed"}

    def _gw_broadcast(self) -> None:
        """Fan the canonical link state to every dashboard tab (SSE)."""
        try:
            _events_broadcast("gateway", self._wa_live_state())
        except Exception:
            pass

    def _handle_gateway_wa_push(self):
        """Bridge event intake: wakeup (drain inbox) or status (fan-out).

        Secret-gated per-boot token, loopback-only. This replaces all
        WhatsApp polling: nothing runs until the bridge knocks.
        """
        try:
            if (self.client_address or [""])[0] not in ("127.0.0.1", "::1"):
                self._send_json({"error": "loopback only"}, status=403)
                return
            payload = self._read_json_body(10000)
            if not isinstance(payload, dict):
                payload = {}
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            from gateway.whatsapp import adapter as _wa
            if not _wa.check_push_secret(payload.get("secret")):
                self._send_json({"error": "forbidden"}, status=403)
                return
            drained = 0
            if str(payload.get("kind") or "") == "wakeup":
                try:
                    from gateway.whatsapp import listener as _lis
                    drained = _lis.drain_once()
                except Exception:
                    pass
            self._gw_broadcast()
            self._send_json({"ok": True, "drained": drained})
        except _ChatError as e:
            self._send_json({"error": str(e)}, status=e.status)
        except Exception:
            self._send_json({"error": "gateway failed"}, status=500)

    def _handle_gateway_wa_status_get(self):
        """WhatsApp link state + engine capability (never spawns engine).

        Enforces linked-number == saved agent-number: a proven mismatch
        wipes the session and reports state=mismatch (never a fake link).
        """
        try:
            self._send_json(self._wa_live_state())
        except Exception:
            self._send_json({"error": "gateway failed"}, status=500)

    def _handle_gateway_wa_post(self, action: str):
        """connect | pair | reset unfinished linking | disconnect."""
        try:
            payload = self._read_json_body(10000)
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            from gateway.whatsapp import adapter as _wa
            store = self._gw_store()
            if action == "connect":
                self._send_json(_wa.connect())
            elif action == "pair":
                number = str(store.read_config()["whatsapp"]["agent_number"] or "")
                if len(number) < 7:
                    self._send_json({"error": "save the agent number first"}, status=400)
                    return
                self._send_json(_wa.pair(number))
            elif action == "disconnect":
                _wa.disconnect()
                store.clear_channel("whatsapp")
                self._send_json({"ok": True, "state": "idle"})
            elif action == "reset":
                self._send_json(_wa.reset_link_attempt())
            else:
                self._send_json({"error": "action connect|pair|reset|disconnect required"}, status=400)
        except _ChatError as e:
            self._send_json({"error": str(e)}, status=e.status)
        except Exception:
            self._send_json({"error": "gateway failed"}, status=500)

    def _handle_gateway_tg_test_post(self):
        """Real Telegram test: getMe + test message to every saved chat ID."""
        try:
            self._read_json_body(10000)
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            from gateway import store as _store
            from gateway.telegram import adapter as _tg
            cfg = _store.read_config()
            token = _store.get_telegram_token()
            if not token:
                self._send_json({"error": "save the bot token first"}, status=400)
                return
            self._send_json(_tg.test_connection(token, cfg["telegram"]["chat_ids"]))
        except _ChatError as e:
            self._send_json({"error": str(e)}, status=e.status)
        except Exception:
            self._send_json({"error": "gateway failed"}, status=500)

    def _handle_workspace_download_get(self, raw_path: str):
        """Serve one Workspace file by name only (no preview — download).

        No size cap by requirement: files stream in 1MB chunks straight from
        disk and are never buffered in memory (GB browser downloads included).
        Virtual downloads/downloads/... paths stream from the library folder.
        A client that disconnects mid-stream ends it quietly server-side.
        """
        try:
            norm = (raw_path or "").strip().replace("\\", "/")
            ext_target = None
            try:
                if str(PROJECT_ROOT) not in sys.path:
                    sys.path.insert(0, str(PROJECT_ROOT))
                from core.tools.workspace.tool import (
                    split_downloads_rel as _split_dl3,
                    downloads_virtual_active as _dl_active3,
                    resolve_downloads_file as _resolve_dl,
                )
                from core.paths import workspace_root as _wsd
                _parts = _split_dl3(norm)
                if _parts is not None and _dl_active3(_wsd().resolve()):
                    ext_target = _resolve_dl(_parts)
            except _ChatError:
                raise
            except (ValueError, FileNotFoundError):
                try:
                    self.send_error(404, "Not found")
                except Exception:
                    pass
                return
            except Exception:
                pass
            target = ext_target if ext_target is not None else self._workspace_fs(raw_path or "")
            if not target.is_file():
                self.send_error(404, "Not found")
                return
            try:
                size = target.stat().st_size
            except Exception:
                try:
                    self.send_error(404, "Not found")
                except Exception:
                    pass
                return
            import mimetypes as _mt
            import shutil as _shutil
            mime = _mt.guess_type(target.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                with open(target, "rb") as _fh:
                    _shutil.copyfileobj(_fh, self.wfile, length=1024 * 1024)
            except (BrokenPipeError, ConnectionResetError):
                pass
        except _ChatError:
            try:
                self.send_error(403, "Forbidden")
            except Exception:
                pass
        except Exception:
            try:
                self.send_error(500, "Download failed")
            except Exception:
                pass

    def _handle_plugins_api(self):
        try:
            data = _get_plugins_data()
            self._send_json(data)
        except Exception as e:
            self._send_json({"error": "failed to load plugins", "detail": str(e)}, status=500)

    def _handle_skills_api(self):
        """GET /api/skills — list user skills, or ?name=X for full instructions."""
        try:
            from urllib.parse import parse_qs, urlparse as _up
            try:
                qs = parse_qs(_up(self.path).query or "")
                wanted = (qs.get("name") or [""])[0]
            except Exception:
                wanted = ""
            if wanted and wanted.strip():
                if str(PROJECT_ROOT) not in sys.path:
                    sys.path.insert(0, str(PROJECT_ROOT))
                from core.tools.skills_view.tool import execute as _view
                try:
                    data = _view(wanted.strip()[:100])
                except (ValueError, TypeError, FileNotFoundError) as e:
                    self._send_json({"error": str(e)[:200]}, status=404 if isinstance(e, FileNotFoundError) else 400)
                    return
                # Edit needs the COMPLETE text (skills_view truncates to 5000).
                # skill.json is read raw here, bounded — same dir _view resolved.
                try:
                    sj = Path(data.get("path") or "") / "skill.json"
                    if sj.is_file() and sj.stat().st_size <= 65536:
                        j = json.loads(sj.read_text(encoding="utf-8"))
                        if isinstance(j, dict):
                            full = str(j.get("instructions") or j.get("content") or "")
                            if full.strip():
                                data["full_instructions"] = full[:12000]
                                data["description"] = str(j.get("description") or "")[:500]
                except Exception:
                    pass
                self._send_json({"skill": data})
                return
            self._send_json(_get_skills_data())
        except Exception as e:
            self._send_json({"error": "failed to load skills", "detail": str(e)}, status=500)

    def _handle_skill_create_post(self):
        """POST /api/skills {name, content, description?} — create or update (same name overwrites)."""
        try:
            payload = self._read_json_body(_SKILL_BODY_LIMIT)
        except _ChatError as e:
            self._send_json({"error": str(e)}, status=e.status)
            return
        try:
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            from plugins.core_plugins.skill_create.tool import execute as _create
            try:
                result = _create(
                    payload.get("name"),
                    payload.get("content"),
                    payload.get("description"),
                )
            except (ValueError, TypeError) as e:
                self._send_json({"error": str(e)[:200]}, status=400)
                return
            self._send_json({"ok": True, "skill": result})
        except Exception as e:
            self._send_json({"error": "skill save failed", "detail": str(e)[:200]}, status=500)

    def _handle_models_api(self):
        # Compat shim: the Dashboard "Current Model" card reads /api/models.
        # Source of truth is now the AI Models store (universal text model).
        try:
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            from core.ai_models.store import public_text_model as _ai_public
            pub = _ai_public()
            name = str(pub.get("model_name") or "").strip() or "—"
            self._send_json({
                "local": [], "cloud": [], "remote": {
                    "id": "text", "name": name,
                    "connected": bool(pub.get("configured")),
                    "default_model": name,
                },
                "default_model": name, "model": name,
                "provider": "", "active_provider": None,
                "chat_ready": bool(pub.get("configured")),
            })
        except Exception as e:
            self._send_json({"error": "failed to load models", "detail": str(e)}, status=500)

    def _handle_ai_models_get(self):
        """GET /api/ai_models — UI-safe view of every template (keys never returned)."""
        try:
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            from core.ai_models.store import KINDS as _KINDS, public_model as _ai_public
            self._send_json({k: _ai_public(k) for k in _KINDS})
        except Exception as e:
            self._send_json({"error": "failed to load AI models", "detail": str(e)[:200]}, status=500)

    def _handle_ai_models_setup_prompt_get(self):
        """GET /api/ai_models/setup_prompt?kind=text — the per-kind section of
        models_prompts.md (single file, `## KIND:` sections)."""
        try:
            from urllib.parse import parse_qs as _pqs, urlparse as _up
            kind = _pqs(_up(self.path).query).get("kind", ["text"])[0]
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            from core.ai_models.store import KINDS as _KINDS
            from core.ai_models.models_prompts import get_prompt as _get_prompt
            if kind not in _KINDS:
                kind = "text"
            text = _get_prompt(kind)
            if not text.strip():
                raise FileNotFoundError("prompt section empty")
            self._send_json({"kind": kind, "prompt": text})
        except Exception as e:
            self._send_json({"error": "setup prompt unavailable", "detail": str(e)[:200]}, status=500)

    def _ai_record_from_payload(self, payload: dict) -> tuple:
        """Returns (kind, record). Raises _ChatError on bad kind/config shape."""
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from core.ai_models.store import KINDS as _KINDS
        kind = str(payload.get("kind") or "text")
        if kind not in _KINDS:
            raise _ChatError(f"unknown model kind '{kind}'")
        config = payload.get("parser_definition", payload.get("config_json"))
        if isinstance(config, str):
            try:
                config = json.loads(config)
            except Exception:
                raise _ChatError("Parser Definition is not valid JSON.")
        if not isinstance(config, dict):
            raise _ChatError("Parser Definition must be an object.")
        # The engine reads kind from INSIDE the config: stamp it so pasted
        # parsers (which never contain it) and old records keep working.
        config = dict(config)
        config["kind"] = kind
        api_url = str(payload.get("api_url") or "")
        if len(api_url) > 2000:
            raise _ChatError("API URL too long.")
        if api_url.lower().startswith(("javascript:", "file:", "data:", "ftp:")):
            raise _ChatError("API URL must be an http(s) path.")
        api_key = str(payload.get("api_key") or "")
        if not api_key and ("keep_key" not in payload or payload.get("keep_key")):
            # Empty key field: the dialog shows dots, not the real value.
            # Reuse the stored key so Test/Save keep working after reopening
            # the dialog (old clients send no keep_key flag at all; new ones
            # send keep_key=false only when the field was cleared on purpose).
            try:
                from core.ai_models.store import get_model as _get_stored
                _stored = _get_stored(kind)
                if isinstance(_stored, dict) and str(_stored.get("api_key") or ""):
                    api_key = str(_stored.get("api_key"))
            except Exception:
                pass
        return kind, {
            "model_name": str(payload.get("model_name") or ""),
            "server_url": str(payload.get("server_url") or ""),
            "api_url": api_url,
            "api_key": api_key,
            "config_json": config,
            "parser_definition": config,
            "kind": kind,
        }

    def _handle_ai_models_test_post(self):
        """POST /api/ai_models/test {kind, model_name, server_url, api_url, api_key, parser_definition} —
        AIP/1 parser goes through the interpreter; legacy connection JSON uses the engine.
        Never echoes the key. Marks session-tested on success."""
        try:
            payload = self._read_json_body(100000)
        except _ChatError as e:
            self._send_json({"error": str(e)}, status=e.status)
            return
        try:
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            from core.ai_models import engine as _eng
            from core.ai_models.store import mark_tested as _mark
            try:
                kind, record = self._ai_record_from_payload(payload)
            except _ChatError as e:
                self._send_json({"ok": False, "error": {
                    "category": "invalid_config", "message": str(e),
                    "technical": str(e), "copyable": str(e)}}, status=400)
                return
            cfg = record.get("parser_definition") or {}
            if isinstance(cfg, dict) and cfg.get("language") == "AIP/1":
                from core.ai_models import aip as _aip
                result = _aip.test_connection(record, kind)
            else:
                result = _eng.test_connection(record)
            if result.get("ok"):
                try:
                    _mark(kind)
                except Exception:
                    pass
            self._send_json(result)
        except Exception as e:
            self._send_json({"ok": False, "error": {
                "category": "server", "message": f"Test failed: {e}"[:300],
                "technical": str(e)[:300], "copyable": str(e)[:500]}}, status=500)

    def _handle_ai_models_save_post(self):
        """POST /api/ai_models/save {kind, model_name, server_url, api_key, config_json, force} —
        persists only after a Test pass in this session, unless force=true (explicit override)."""
        try:
            payload = self._read_json_body(100000)
        except _ChatError as e:
            self._send_json({"error": str(e)}, status=e.status)
            return
        try:
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            from core.ai_models.store import save_model as _save
            try:
                kind, record = self._ai_record_from_payload(payload)
            except _ChatError as e:
                self._send_json({"error": str(e)}, status=400)
                return
            pub = _save(
                kind,
                record["model_name"], record["server_url"], record["api_key"],
                record["config_json"],
                force=bool(payload.get("force")),
                api_url=record.get("api_url", ""),
            )
            self._send_json({"ok": True, "model": pub})
        except PermissionError as e:
            self._send_json({"error": str(e)[:300], "needs_test": True}, status=409)
        except (ValueError, RuntimeError) as e:
            self._send_json({"error": str(e)[:300]}, status=400)
        except Exception as e:
            self._send_json({"error": "save failed", "detail": str(e)[:200]}, status=500)

    def _handle_embedding_status_get(self):
        """GET /api/embedding_status — built-in resource install state for the progress bar."""
        try:
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            from core.embedding.bootstrap import detect_device, get_status
            status = get_status()
            status["device"] = detect_device()
            self._send_json(status)
        except Exception as e:
            self._send_json({"state": "unknown", "detail": str(e)[:200]}, status=500)

    def _handle_public_share_get(self):
        """GET /api/public_share — Tailscale Funnel snapshot (read-only, never enables)."""
        try:
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            from web.server.tailscale_share import status as _ts_status
            self._send_json(_ts_status(port=DEFAULT_PORT))
        except Exception as e:
            self._send_json({"installed": False, "error": str(e)[:200]}, status=500)

    def _handle_update_check_get(self):
        """GET /api/update_check — local vs remote state + notes (never raises, always 200)."""
        try:
            self._send_json(_github_latest_info())
        except Exception as e:
            local, local_commit = _local_version_info()
            self._send_json({"local": local, "local_commit": local_commit,
                             "latest": None, "up_to_date": None, "source": None,
                             "notes": None, "error": str(e)[:200]})

    def _handle_update_run_post(self):
        """POST /api/update_run — source-only update in background (git ff-only, data untouched)."""
        try:
            with _UPDATE_LOCK:
                if _UPDATE_STATE["state"] == "working":
                    self._send_json({"error": "Update already running."}, status=409)
                    return
                _UPDATE_STATE.update({"state": "working", "lines": [], "done": False,
                                      "ok": False, "restart_required": False})
            t = threading.Thread(target=_run_update_job, name="x5-update", daemon=True)
            t.start()
            self._send_json({"started": True})
        except Exception as e:
            self._send_json({"error": str(e)[:200]}, status=500)

    def _handle_update_progress_get(self):
        """GET /api/update_progress — live lines of the running/finished update."""
        try:
            with _UPDATE_LOCK:
                snap = {"state": _UPDATE_STATE["state"],
                        "lines": list(_UPDATE_STATE["lines"][-50:]),
                        "done": _UPDATE_STATE["done"], "ok": _UPDATE_STATE["ok"],
                        "restart_required": _UPDATE_STATE["restart_required"]}
            self._send_json(snap)
        except Exception as e:
            self._send_json({"error": str(e)[:200]}, status=500)

    def _handle_ai_models_clear_post(self):
        """POST /api/ai_models/clear {kind} — disconnect one template (vault + .env + session mark)."""
        try:
            payload = self._read_json_body(10000)
        except _ChatError as e:
            self._send_json({"error": str(e)}, status=e.status)
            return
        try:
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            from core.ai_models.store import KINDS as _KINDS, clear_model as _clear
            kind = str(payload.get("kind") or "text")
            if kind not in _KINDS:
                self._send_json({"error": f"unknown model kind '{kind}'"}, status=400)
                return
            _clear(kind)
            self._send_json({"ok": True})
        except Exception as e:
            self._send_json({"error": "clear failed", "detail": str(e)[:200]}, status=500)

    def _handle_conversation_get(self):
        try:
            try:
                from core.paths import data_db_file as _ddf
                storage = _ddf("conversation.json")
            except Exception:
                storage = PROJECT_ROOT / "storage" / "database" / "conversation.json"
            legacy = PROJECT_ROOT / "storage" / "database" / "conversation.json"
            data = _data_json("conversation.json", _safe_json_load(legacy, []))
            # Assign stable ids to id-less entries so the UI can edit/delete by id.
            try:
                if isinstance(data, list) and any(not isinstance(m, dict) or not m.get("id") for m in data):
                    import uuid as _uuid

                    changed = False
                    for m in data:
                        if isinstance(m, dict) and not m.get("id"):
                            m["id"] = _uuid.uuid4().hex[:8]
                            changed = True
                    if changed:
                        tmp = storage.with_suffix(".tmp")
                        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                        tmp.replace(storage)
            except Exception:
                pass
            self._send_json({"messages": data})
        except Exception as e:
            self._send_json({"error": str(e)}, status=500)

    def _cleanup_paths(self, paths) -> None:
        """Best-effort delete of temp request files — never accumulate on disk."""
        for p in paths or []:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass

    def _save_chat_attachment(self, att):
        """Validate + save one chat attachment. Returns (kind, saved_path, loop_hint_line, image_id_or_None)."""
        import base64 as _b64
        import uuid as _uuid

        if not isinstance(att, dict):
            raise TypeError("attachment must be object")
        kind = att.get("type")
        if kind not in ("image", "video", "text", "document"):
            raise ValueError("attachment type must be image, video, text or document")
        raw_name = str(att.get("name") or "file")[:200]
        safe_display = (Path(raw_name).name or "file").replace('"', "'")
        data = att.get("data")
        if not isinstance(data, str) or not data.startswith("data:") or ";base64," not in data:
            raise ValueError("attachment data must be a data URL")
        header, b64 = data.split(";base64,", 1)
        mime = header[5:].split(";")[0].strip().lower()
        if kind == "image":
            ext = _IMAGE_MIMES.get(mime)
            limit = CHAT_MAX_IMAGE_BYTES
        elif kind == "video":
            ext = _VIDEO_MIMES.get(mime)
            limit = CHAT_MAX_VIDEO_BYTES
        elif kind == "document":
            ext = _DOC_MIMES.get(mime)
            limit = CHAT_MAX_DOC_BYTES
        else:
            ext = "txt" if mime == "text/plain" else None
            limit = CHAT_MAX_TEXT_BYTES
        if not ext:
            raise ValueError(f"unsupported {kind} format: {mime or 'unknown'}")
        try:
            blob = _b64.b64decode(b64, validate=True)
        except Exception:
            raise ValueError("attachment is not valid base64")
        if not blob or len(blob) > limit:
            max_label = f"{limit // 1024}KB" if limit < 1024 * 1024 else f"{limit // (1024 * 1024)}MB"
            raise ValueError(f"{kind} size out of bounds (max {max_label})")
        subdir = {"image": "images", "video": "videos", "text": "texts", "document": "docs"}[kind]
        try:
            from core.paths import data_subdir as _ds
            target_dir = _ds("files", subdir)
        except Exception:
            target_dir = PROJECT_ROOT / "storage" / "files" / subdir
        target_dir.mkdir(parents=True, exist_ok=True)
        saved = target_dir / f"{_uuid.uuid4().hex[:12]}.{ext}"
        saved.write_bytes(blob)

        def _mirror_workspace(blob_bytes: bytes) -> str | None:
            # Best-effort mirror: every chat upload lives in Workspace too so it
            # shows with other files + jailed model tools can read it.
            # Returns Workspace-relative file_id or None. Never raises.
            try:
                from core.paths import workspace_root as _ws
                ws = _ws()
                ws.mkdir(parents=True, exist_ok=True)
                ws_resolved = ws.resolve()
                candidate = (ws / f"{_uuid.uuid4().hex[:8]}_{safe_display}").resolve()
                try:
                    candidate.relative_to(ws_resolved)
                except ValueError:
                    return None
                if not candidate.exists():
                    candidate.write_bytes(blob_bytes)
                return str(candidate.relative_to(ws_resolved)).replace("\\", "/")
            except Exception:
                return None

        if kind == "document":
            # The Workspace copy is what document_search indexes (file_id).
            file_id = _mirror_workspace(blob) or safe_display
            ref = f"- document: name=\"{safe_display}\" file_id=\"{file_id}\""
            return kind, saved, ref, file_id

        if kind == "image":
            # Temp entry so the loop resolves it via image_id; auto-cleared at loop end
            from core.memory.image_memory.service import store_image

            entry = store_image(name=safe_display, path=str(saved), is_persistent=False, is_temp=True)
            ws_file = _mirror_workspace(blob)
            if ws_file:
                ref = f"- image: name=\"{safe_display}\" image_id=\"{entry['image_id']}\" file_id=\"{ws_file}\""
            else:
                ref = f"- image: name=\"{safe_display}\" image_id=\"{entry['image_id']}\""
            return kind, saved, ref, entry["image_id"]
        elif kind == "video":
            ws_file = _mirror_workspace(blob)
            if ws_file:
                ref = f"- video: name=\"{safe_display}\" video_path=\"{saved}\" file_id=\"{ws_file}\""
            else:
                ref = f"- video: name=\"{safe_display}\" video_path=\"{saved}\""
            return kind, saved, ref, None
        else:
            try:
                text_content = blob.decode("utf-8")
            except Exception:
                raise ValueError("text file must be UTF-8")
            if not text_content.strip():
                raise ValueError("text file is empty")
            ws_file = _mirror_workspace(blob)
            if ws_file:
                ref = f"- text: name=\"{safe_display}\" chars=\"{len(text_content)}\" file_id=\"{ws_file}\""
            else:
                ref = f"- text: name=\"{safe_display}\" chars=\"{len(text_content)}\""
            return kind, saved, ref, text_content

    def _extract_loop_events(self, result) -> list:
        """Collect tool-call events + output excerpts for the UI (bounded, deduped)."""
        from core.agent.loop.engine import _event_media, _preview_event, _summarize_output, _tool_reason

        events: list[dict] = []
        seen: set[str] = set()
        try:
            log = result.get("tool_calls_log") or []
        except Exception:
            return events
        for entry in log:
            if not isinstance(entry, dict):
                continue
            res = entry.get("result") or {}
            text = None
            if isinstance(res, dict) and res.get("success"):
                out = res.get("output") or {}
                if isinstance(out, dict) and out.get("event"):
                    text = str(out["event"])
            if not text:
                # Failures keep the friendly tool label too — the error itself
                # travels as the expandable output (arrow), not as the label.
                try:
                    text = _preview_event(entry.get("tool"), entry.get("arguments"))
                except Exception:
                    text = str(entry.get("tool") or "tool")
            if text and text not in seen and len(events) < 12:
                seen.add(text)
                try:
                    excerpt = _summarize_output(res, entry.get("tool")) if isinstance(res, dict) else None
                except TypeError:
                    try:
                        excerpt = _summarize_output(res) if isinstance(res, dict) else None
                    except Exception:
                        excerpt = None
                except Exception:
                    excerpt = None
                item: dict = {"event": text[:80], "output": excerpt}
                try:
                    _rsn = entry.get("reason") or _tool_reason(entry.get("tool"), entry.get("arguments"))
                    if isinstance(_rsn, str) and _rsn.strip():
                        item["reason"] = _rsn[:200]
                except Exception:
                    pass
                # Blocking path must carry the screenshot like the live stream does.
                try:
                    media = _event_media(entry.get("tool"), res if isinstance(res, dict) else {})
                    shot = media.get("shot") if isinstance(media, dict) else None
                    if isinstance(shot, str) and shot:
                        item.setdefault("media", {})["shot"] = shot[:500]
                    audio = media.get("audio") if isinstance(media, dict) else None
                    if isinstance(audio, str) and audio:
                        item.setdefault("media", {})["audio"] = audio[:100]
                except Exception:
                    pass
                events.append(item)
        return events

    def _handle_stt_post(self):
        """Transcribe voice to raw text via local Whisper. Direct output, no correction layer."""
        import base64 as _b64
        import uuid as _uuid

        try:
            raw = _read_request_body(self.rfile, self.headers, STT_MAX_BODY)
            if not raw:
                try:
                    too_big = int(self.headers.get("Content-Length", 0) or 0) > STT_MAX_BODY
                except Exception:
                    too_big = False
                if too_big:
                    self._send_json({"error": "audio too large (max 12MB)"}, status=413)
                else:
                    self._send_json({"error": "invalid json"}, status=400)
                return
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except Exception:
                self._send_json({"error": "invalid json"}, status=400)
                return
            if not isinstance(payload, dict):
                self._send_json({"error": "invalid payload"}, status=400)
                return

            data = payload.get("audio")
            if not isinstance(data, str) or not data.startswith("data:") or ";base64," not in data:
                self._send_json({"error": "audio must be a data URL"}, status=400)
                return
            header, b64 = data.split(";base64,", 1)
            mime = header[5:].split(";")[0].strip().lower()
            ext = _AUDIO_MIMES.get(mime)
            if not ext:
                self._send_json({"error": f"unsupported audio format: {mime or 'unknown'}"}, status=400)
                return
            try:
                blob = _b64.b64decode(b64, validate=True)
            except Exception:
                self._send_json({"error": "audio is not valid base64"}, status=400)
                return
            if not blob or len(blob) > STT_MAX_AUDIO_BYTES:
                self._send_json({"error": "audio size out of bounds (max 8MB)"}, status=400)
                return

            language = payload.get("language")
            if language is not None and not isinstance(language, str):
                self._send_json({"error": "language must be string"}, status=400)
                return

            try:
                from core.paths import data_subdir as _ds2
                audio_dir = _ds2("files", "audio")
            except Exception:
                audio_dir = PROJECT_ROOT / "storage" / "files" / "audio"
            audio_dir.mkdir(parents=True, exist_ok=True)
            saved = audio_dir / f"{_uuid.uuid4().hex[:12]}.{ext}"
            saved.write_bytes(blob)
            try:
                if str(PROJECT_ROOT) not in sys.path:
                    sys.path.insert(0, str(PROJECT_ROOT))
                from core.ai_models.media import read_audio
                from core.ai_models.store import require_model
                from core.ai_models.engine import transcribe

                record = require_model("stt")  # fixed refusal + model link when missing
                blob2, mime2 = read_audio(saved)
                text = transcribe(record, {"audio_bytes": blob2, "mime": mime2})
            except RuntimeError as e:
                self._send_json({"error": str(e)[:300], "needs_model": True}, status=503)
                return
            finally:
                try:
                    saved.unlink(missing_ok=True)
                except Exception:
                    pass

            self._send_json({"text": text, "empty": not bool(text.strip())})
        except (FileNotFoundError, RuntimeError) as e:
            self._send_json({"error": "STT failed — model unavailable", "detail": str(e)[:200]}, status=502)
        except Exception as e:
            self._send_json({"error": "transcription failed", "detail": str(e)[:200]}, status=500)

    def _read_json_body(self, max_bytes: int) -> dict:
        """Read + validate a JSON request body. Raises _ChatError on failure."""
        raw = _read_request_body(self.rfile, self.headers, max_bytes)
        if not raw and max_bytes > 0:
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
            except Exception:
                length = 0
            if length > max_bytes:
                raise _ChatError(f"payload too large (max {max_bytes // (1024 * 1024)}MB)", status=413)
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            raise _ChatError("invalid json")
        if not isinstance(data, dict):
            raise _ChatError("invalid payload")
        return data

    def _build_loop_input(self, payload: dict):
        """Validate chat payload, save attachments, return (loop_input, saved_paths)."""
        msg = str(payload.get("message") or payload.get("text") or "").strip()
        if len(msg) > 4000:
            raise _ChatError("message too long (max 4000)")
        raw_attachments = payload.get("attachments") or []
        if not isinstance(raw_attachments, list) or len(raw_attachments) > CHAT_MAX_ATTACHMENTS:
            raise _ChatError(f"attachments must be a list of max {CHAT_MAX_ATTACHMENTS}")
        if not msg and not raw_attachments:
            raise _ChatError("message required")

        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))

        # Save attachments to disk (validated) and register temp image entries
        saved_paths: list = []
        saved_meta: list = []
        attach_lines: list[str] = []
        text_blocks: list[str] = []
        doc_files: list[str] = []
        try:
            for att in raw_attachments:
                _kind, _saved, _ref, _iid = self._save_chat_attachment(att)
                saved_paths.append(_saved)
                saved_meta.append((_kind, _iid if _kind == "image" else None))
                attach_lines.append(_ref)
                if _kind == "text" and isinstance(_iid, str):
                    shown = _iid[:CHAT_MAX_TEXT_CHARS]
                    if len(_iid) > CHAT_MAX_TEXT_CHARS:
                        shown += "\n...[truncated]"
                    text_blocks.append(shown)
                if _kind == "document" and isinstance(_iid, str) and _iid.strip():
                    doc_files.append(_iid.strip()[:500])
        except (ValueError, TypeError) as e:
            self._cleanup_paths(saved_paths)
            raise _ChatError(f"invalid attachment: {e}")

        # Resolve @image1.. labels to real image_ids (same order the UI numbers them)
        import re as _re

        img_no = 0
        for _kind, _iid in saved_meta:
            if _kind == "image" and _iid:
                img_no += 1
                msg = _re.sub(rf"@image{img_no}(?![0-9])", f"@{_iid}", msg)

        if attach_lines:
            if msg:
                text_part = msg
            elif all(line.startswith("- image:") for line in attach_lines):
                text_part = "Describe this image in detail."
            else:
                text_part = "Analyze the attached file(s)."
            loop_input = text_part + "\n\n[Attached files]\n" + "\n".join(attach_lines)
            for i, block in enumerate(text_blocks, 1):
                loop_input += f"\n\n[Attached text file {i} content]\n```text\n{block}\n```"
            if doc_files:
                # Priority slot: chat-uploaded documents are ALSO in Workspace
                # (see the Workspace block above) and are listed again here so
                # the agent reaches for document_search first for this message.
                prio = "\n".join(
                    f"- file_id=\"{f}\": search it with "
                    f"document_search(file_id=\"{f}\", query=<question>, top_k=<1..20>)"
                    for f in doc_files[:2]
                )
                loop_input += ("\n\n[Attached documents — priority, also in Workspace]\n"
                               + prio)
        else:
            loop_input = msg
        if attach_lines:
            # Workspace mirror landed on disk — bump the version so the
            # Workspace page shows the new file without waiting for the slow poll.
            try:
                _events_emit_workspace(force=True)
            except Exception:
                pass
        return loop_input, saved_paths

    def _handle_plugin_state_post(self):
        """Toggle a plugin or its optional permissions. Required permissions reject disabling."""
        try:
            payload = self._read_json_body(10000)
        except _ChatError as e:
            self._send_json({"error": str(e)}, status=e.status)
            return
        try:
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            from core.tools.registry import known_plugin_ids, set_permission_enabled, set_plugin_enabled

            plugin = payload.get("plugin")
            if not isinstance(plugin, str) or plugin not in known_plugin_ids():
                self._send_json({"error": "unknown plugin"}, status=400)
                return
            if "enabled" in payload:
                if not isinstance(payload["enabled"], bool):
                    self._send_json({"error": "enabled must be boolean"}, status=400)
                    return
                set_plugin_enabled(plugin, payload["enabled"])
            perms = payload.get("permissions") or {}
            if not isinstance(perms, dict):
                self._send_json({"error": "permissions must be object"}, status=400)
                return
            if perms:
                if str(PROJECT_ROOT) not in sys.path:
                    sys.path.insert(0, str(PROJECT_ROOT))
                from core.tools.registry import plugin_actions

                allowed = plugin_actions(plugin)
                for pname, pval in perms.items():
                    if not isinstance(pval, bool):
                        self._send_json({"error": f"invalid permission: {pname}"}, status=400)
                        return
                    if allowed:
                        if pname not in allowed:
                            self._send_json({"error": f"invalid permission: {pname}"}, status=400)
                            return
                    else:
                        self._send_json({"error": "single-permission tool uses the plugin switch"}, status=400)
                        return
                for pname, pval in perms.items():
                    set_permission_enabled(plugin, pname, pval)
            self._send_json({"ok": True})
        except ValueError as e:
            self._send_json({"error": str(e)[:200]}, status=400)
        except Exception as e:
            self._send_json({"error": "state save failed", "detail": str(e)[:200]}, status=500)

    def _handle_chat_answer_post(self):
        """Deliver the user's answer to a paused loop question (ask or approval).

        The answer resumes the run: the loop's own completion event carries
        it into the question step's arrow (Q + A) and work continues — it is
        never posted as a user message and never restarts the loop.
        """
        try:
            payload = self._read_json_body(10000)
        except _ChatError as e:
            self._send_json({"error": str(e)}, status=e.status)
            return
        try:
            qid = payload.get("id")
            if not isinstance(qid, str) or not qid:
                self._send_json({"error": "question id required"}, status=400)
                return
            with _PQ_LOCK:
                q = _PENDING_QUESTIONS.get(qid)
            if q is None:
                self._send_json({"error": "question expired or unknown"}, status=410)
                return
            decision = payload.get("decision")
            answer = payload.get("answer")
            if isinstance(decision, str) and decision in ("continue", "stop"):
                q.put({"decision": decision})
            elif isinstance(decision, str) and decision in ("skip", "activate"):
                q.put({"decision": decision})
            elif isinstance(answer, str) and answer.strip():
                q.put({"answer": answer.strip()[:2000]})
            else:
                self._send_json({"error": "answer or decision required"}, status=400)
                return
            # Resuming a live handoff ends its pause entry.
            _live_pause_clear(qid)
            self._send_json({"ok": True})
        except Exception as e:
            self._send_json({"error": "answer failed", "detail": str(e)[:200]}, status=500)

    def _handle_chat_active_api(self):
        """Whether an agent loop is currently running (+ its input preview).

        Lets a refreshed page resume the running state instead of losing it."""
        try:
            with _RUN_LOCK:
                active = "event" in _ACTIVE_RUN
                preview = str(_ACTIVE_RUN.get("input") or "")[:200]
            self._send_json({"active": bool(active), "input": preview})
        except Exception as e:
            self._send_json({"error": "failed to load chat state", "detail": str(e)}, status=500)

    def _handle_chat_frames_api(self):
        """Progress frames of the running loop, for a page that just refreshed.

        Returns the steps/questions already streamed so the UI can rebuild the
        Working block + its steps, then keep following live run pushes."""
        try:
            with _RUN_LOCK:
                active = "event" in _ACTIVE_RUN
                preview = str(_ACTIVE_RUN.get("input") or "")[:200]
            with _RUN_FRAMES_LOCK:
                frames = [dict(f) for f in _RUN_FRAMES]
            self._send_json({"active": bool(active), "input": preview,
                             "frames": frames})
        except Exception as e:
            self._send_json({"error": "failed to load run frames", "detail": str(e)}, status=500)

    def _handle_chat_cancel_post(self):
        """Cancel the active chat loop (edit-during-generation). Best-effort."""
        try:
            with _RUN_LOCK:
                ev = _ACTIVE_RUN.get("event")
            if ev is not None:
                try:
                    ev.set()
                except Exception:
                    pass
            self._send_json({"ok": True})
        except Exception as e:
            self._send_json({"error": "cancel failed", "detail": str(e)[:200]}, status=500)

    def _handle_message_post(self):
        """Edit or delete a chat message (+ its embedding stays in sync)."""
        try:
            payload = self._read_json_body(10000)
        except _ChatError as e:
            self._send_json({"error": str(e)}, status=e.status)
            return
        try:
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            from core.agent.memory import chat_vector_store as cvs

            action = payload.get("action")
            mid = payload.get("id")
            if action not in ("edit", "delete") or not isinstance(mid, str) or not mid.strip():
                self._send_json({"error": "action edit|delete + id required"}, status=400)
                return
            if action == "delete":
                found = cvs.delete_message(mid.strip())
                if not found:
                    self._send_json({"error": "message not found"}, status=404)
                    return
                self._send_json({"ok": True})
                return
            content = payload.get("content")
            if not isinstance(content, str) or not content.strip() or len(content) > 4000:
                self._send_json({"error": "content required (max 4000)"}, status=400)
                return
            try:
                cvs.update_message(mid.strip(), content.strip())
            except (ValueError, FileNotFoundError) as e:
                self._send_json({"error": str(e)[:200]}, status=404)
                return
            self._send_json({"ok": True})
        except Exception as e:
            self._send_json({"error": "message update failed", "detail": str(e)[:200]}, status=500)

    def _handle_chat_post(self):
        try:
            try:
                payload = self._read_json_body(CHAT_MAX_BODY)
                loop_input, saved_paths = self._build_loop_input(payload)
            except _ChatError as e:
                self._send_json({"error": str(e)}, status=e.status)
                return

            # No routable provider active (or no key saved): fail clearly with an
            # error, never a canned reply. Every chat reply is written by the AI only.
            ready, _, _ = _chat_readiness()
            if not ready:
                self._cleanup_paths(saved_paths)
                self._send_json({"error": NO_MODEL_ERROR, "needs_model": True, "needs_kind": "text"}, status=503)
                return

            # Run the REAL agent loop (tools, memory, vision) — no direct-LLM shortcut.
            # The loop persists the conversation, syncs embeddings, and clears temp images.
            try:
                from core.agent.loop.engine import run_loop

                chat_model, chat_key = _resolve_chat_model()
                result = run_loop(loop_input, max_steps=CHAT_LOOP_STEPS, verbose=False, model=chat_model, api_key=chat_key)
            except (ValueError, RuntimeError) as e:
                self._send_json(
                    {"error": "LLM request failed — connect a model in AI Models", "detail": str(e)[:200]},
                    status=502,
                )
                return
            finally:
                # Temp request files are single-use — never accumulate on disk
                self._cleanup_paths(saved_paths)

            events = self._extract_loop_events(result)
            final = result.get("final_answer") or ""
            audio_list = result.get("final_audio") or []
            _need_kind = result.get("needs_model_kind")
            _out = {
                "reply": str(final),
                "events": events,
                "steps": result.get("steps", 0),
                "model": result.get("model_used", ""),
                "ids": result.get("message_ids"),
                "images": result.get("final_images") or [],
                "audio": audio_list,
                "elapsed": result.get("elapsed_sec", 0),
            }
            if isinstance(_need_kind, str) and _need_kind:
                _out["needs_model"] = True
                _out["needs_kind"] = _need_kind
            self._send_json(_out)
        except Exception as e:
            self._send_json({"error": "chat failed", "detail": str(e)}, status=500)

    def _handle_chat_stream(self):
        """Same as /api/chat but streams tool-call events live (SSE) as the loop runs.

        Gateway runs (source=whatsapp|telegram|gateway) share this exact
        pipeline: one execution, same session context, live on the Agent page
        with real SSE animation (steps + typed reply). They run unrestricted;
        ask stays unanswered and approvals auto-continue, but live control
        handoffs pause for a channel "continue" answer. A busy session
        answers 409 so the channel waits briefly, then falls back locally.
        """
        import queue as _queue

        try:
            payload = self._read_json_body(CHAT_MAX_BODY)
            loop_input, saved_paths = self._build_loop_input(payload)
        except _ChatError as e:
            self._send_json({"error": str(e)}, status=e.status)
            return

        gw = str(payload.get("source") or "").strip().lower() in ("whatsapp", "telegram", "gateway")
        if gw:
            # Gateway runs arrive on loopback only (same container, cookie-less):
            # refuse them from any remote peer — the owner gate already exempted
            # loopback above, and anything else claiming gateway is spoofed.
            try:
                peer = self.client_address[0] if self.client_address else ""
            except Exception:
                peer = ""
            if peer not in ("127.0.0.1", "::1"):
                self._cleanup_paths(saved_paths)
                self._send_json({"error": "login required"}, status=401)
                return
            try:
                with _RUN_LOCK:
                    busy = "event" in _ACTIVE_RUN
            except Exception:
                busy = False
            if busy:
                self._cleanup_paths(saved_paths)
                self._send_json({"error": "another run is active, try again shortly"}, status=409)
                return

        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
        except Exception:
            self._cleanup_paths(saved_paths)
            return

        restricted = payload.get("mode", "restricted") != "free"

        # No routable provider active (or no key saved): fail clearly with an
        # SSE error frame, never a canned reply. Every chat reply is written by the AI only.
        ready, _, _ = _chat_readiness()
        if not ready:
            self._cleanup_paths(saved_paths)
            try:
                data = ("data: " + json.dumps({
                    "type": "error",
                    "error": NO_MODEL_ERROR,
                    "needs_model": True,
                    "needs_kind": "text",
                }, ensure_ascii=False) + "\n\n").encode("utf-8")
                self.wfile.write(data)
                self.wfile.flush()
            except Exception:
                pass
            return

        events_q: _queue.Queue = _queue.Queue(maxsize=2000)
        box: dict = {}
        write_lock = threading.Lock()

        def _emit(obj) -> bool:
            try:
                data = ("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8")
                with write_lock:
                    self.wfile.write(data)
                    self.wfile.flush()
                return True
            except Exception:
                return False

        def _stage(kind: str, val, out) -> None:
            """Record progress + enqueue the frame for the client.

            Recording happens HERE (the run thread), never in _emit, so a
            refreshed/disconnected page never stops the progress buffer; the
            queue is bounded and drops the oldest frame instead of growing.
            """
            frame = _build_stream_frame(kind, val, out)
            if frame is not None:
                _run_frame_remember(frame)
            try:
                events_q.put_nowait((kind, val, out))
            except Exception:
                try:
                    events_q.get_nowait()
                except Exception:
                    pass
                try:
                    events_q.put_nowait((kind, val, out))
                except Exception:
                    pass

        def _on_question(kind: str, qpayload: dict):
            import uuid as _uuid

            qid = _uuid.uuid4().hex[:8]
            q: _queue.Queue = _queue.Queue()
            with _PQ_LOCK:
                _PENDING_QUESTIONS[qid] = q
            frame = {
                "type": "question",
                "id": qid,
                "kind": kind,
                "text": str(qpayload.get("question") or qpayload.get("command") or "")[:2000],
                "choices": [str(c)[:200] for c in (qpayload.get("options") or [])[:4]],
                "command": str(qpayload.get("command") or "")[:500],
            }
            if kind == "needs_model":
                # Model-activation pause: which kind + which tool, so the UI can
                # show Skip / Activate and deep-link to the right dialog.
                try:
                    from core.ai_models.store import KIND_TITLES as _TITLES
                    _k = str(qpayload.get("kind") or "text")
                    _t = str(qpayload.get("tool") or "")
                    frame["needs_kind"] = _k
                    frame["tool"] = _t[:40]
                    frame["text"] = f"Needs {_TITLES.get(_k, _k)} (used by {_t or 'this task'})."
                except Exception:
                    frame["needs_kind"] = str(qpayload.get("kind") or "text")
                    frame["tool"] = str(qpayload.get("tool") or "")[:40]
            if kind == "live":
                # Handoff pause: remember ONLY its metadata (the URL is read from
                # the library when needed) so a refresh restores the card.
                live_view_only = bool(qpayload.get("view_only", False))
                frame["url"] = str(qpayload.get("url") or "")[:2000]
                frame["view_only"] = live_view_only
                frame["tool"] = str(qpayload.get("tool") or "human_control")[:40]
                frame["reason"] = str(qpayload.get("reason") or "")[:500]
                with _PQ_LOCK:
                    _LIVE_PAUSE[qid] = {"tool": frame["tool"],
                                        "view_only": live_view_only,
                                        "reason": frame["reason"]}
            if not _emit(frame):
                with _PQ_LOCK:
                    _PENDING_QUESTIONS.pop(qid, None)
                    _LIVE_PAUSE.pop(qid, None)
                return None
            try:
                return q.get(timeout=600)
            except Exception:
                _emit({"type": "question-expired", "id": qid})
                return None
            finally:
                with _PQ_LOCK:
                    _PENDING_QUESTIONS.pop(qid, None)
                    _LIVE_PAUSE.pop(qid, None)

        def _on_gateway_question(kind: str, qpayload: dict):
            # Gateway runs pause ONLY for live control handoffs (the channel
            # answers them with "continue"). Ask stays unanswered and
            # approvals auto-continue — today's behavior, never a stall.
            # Missing-model pauses auto-skip so gateway runs never stall.
            try:
                if kind == "live":
                    return _on_question(kind, qpayload)
                if kind == "approval":
                    return {"decision": "continue"}
                if kind == "needs_model":
                    return {"decision": "skip"}
            except Exception:
                pass
            return None

        def _on_live(ev: dict) -> None:
            # live_preview start / agent_control / close_browser / restart:
            # the library owns the links, so just push the fresh state.
            try:
                if not isinstance(ev, dict) or not ev.get("success"):
                    return
                tool = str(ev.get("tool") or "")
                if not tool:
                    return
                _events_emit_browser(force=True)
                if tool in ("agent_control", "close_browser"):
                    _stage("live_end", None, None)
                elif tool in ("live_preview", "show_preview", "restart_browser", "open_browser"):
                    # Watch-only surface: the floating player must render the
                    # PREVIEW token link, never the control one (control opens
                    # as a handoff card / edge tab through its own path).
                    links = _live_snapshot()
                    purl = links.get("preview") if isinstance(links, dict) else ""
                    if isinstance(purl, str) and purl.startswith(("http://", "https://")):
                        _stage("live", {"mode": "preview",
                                        "tool": tool,
                                        "url": purl[:2000],
                                        "view_only": True}, None)
                    else:
                        active = _live_active()
                        if isinstance(active, dict) and active.get("url"):
                            _stage("live", {"mode": active.get("mode"),
                                            "tool": tool,
                                            "url": active["url"],
                                            "view_only": True}, None)
                else:
                    active = _live_active()
                    if isinstance(active, dict) and active.get("url"):
                        _stage("live", {"mode": active.get("mode"),
                                        "tool": tool,
                                        "url": active["url"],
                                        "view_only": True}, None)
            except Exception:
                pass

        def _run():
            cancel_event = threading.Event()
            _run_frames_clear()
            with _RUN_LOCK:
                _ACTIVE_RUN["event"] = cancel_event
                _ACTIVE_RUN["input"] = loop_input[:200]
            _events_emit_chat(gateway=gw)
            try:
                from core.agent.loop.engine import run_loop

                chat_model, chat_key = _resolve_chat_model()
                box["res"] = run_loop(
                    loop_input,
                    max_steps=CHAT_LOOP_STEPS,
                    verbose=False,
                    model=chat_model,
                    api_key=chat_key,
                    on_event=lambda t, o=None, k=None, m=None, r=None, s=None: _stage("event", t, (o, k, m, r, s)),
                    on_tool_start=lambda label, k=None, r=None, s=None: _stage("event_start", label, (k, r, s)),
                    on_token=lambda step, d: _stage("token", step, d),
                    restricted=restricted,
                    on_question=_on_gateway_question if gw else _on_question,
                    on_live_event=_on_live,
                    cancel_flag=cancel_event,
                )
            except Exception as e:
                box["err"] = e
            finally:
                with _RUN_LOCK:
                    if _ACTIVE_RUN.get("event") is cancel_event:
                        _ACTIVE_RUN.pop("event", None)
                    _ACTIVE_RUN.pop("input", None)
                _run_frames_clear()
                _events_emit_chat(gateway=gw)
                _events_broadcast("chat_done", {"gateway": True} if gw else {})
                try:
                    _events_emit_scheduler(force=True)
                except Exception:
                    pass
                events_q.put(("stop", None, None))

        threading.Thread(target=_run, daemon=True).start()

        try:
            while True:
                try:
                    kind, val, out = events_q.get(timeout=25)
                except Exception:
                    if not _emit({"type": "ping"}):
                        break
                    continue
                if kind == "stop":
                    break
                frame = _build_stream_frame(kind, val, out)
                if frame is None:
                    continue
                # Any completion carrying a watch link says a live session is
                # running (e.g. open_browser attaches one via auto-preview):
                # refresh the browser snapshot so the edge tab appears even if
                # the first push was missed. A paused control handoff is never
                # overwritten here.
                try:
                    _md = frame.get("media")
                    _stm = _md.get("stream") if isinstance(_md, dict) else None
                    _surl = _stm.get("url") if isinstance(_stm, dict) else None
                    if isinstance(_surl, str) and _surl.startswith(("http://", "https://")):
                        if _live_pause_entry() is None:
                            _events_emit_browser(force=True)
                except Exception:
                    pass
                if not _emit(frame):
                    break
            err = box.get("err")
            # A finished loop can never resume a control handoff — drop it.
            # Preview state survives (the browser may still stream).
            _live_pause_clear()
            if err is not None:
                _emit({"type": "error", "error": "LLM request failed — connect a model in AI Models", "detail": str(err)[:200]})
            else:
                res = box.get("res") or {}
                if res.get("cancelled"):
                    _emit({"type": "cancelled"})
                else:
                    final = res.get("final_answer") or ""
                    _need_kind = res.get("needs_model_kind")
                    _done = {
                        "type": "done",
                        "reply": str(final),
                        "events": self._extract_loop_events(res),
                        "steps": res.get("steps", 0),
                        "ids": res.get("message_ids"),
                        "images": res.get("final_images") or [],
                        "audio": res.get("final_audio") or [],
                        "elapsed": res.get("elapsed_sec", 0),
                    }
                    if isinstance(_need_kind, str) and _need_kind:
                        _done["needs_model"] = True
                        _done["needs_kind"] = _need_kind
                    _emit(_done)
        except Exception:
            pass
        finally:
            self._cleanup_paths(saved_paths)

    def _send_json(self, obj, status=200, headers=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Access-Control-Allow-Origin", "*")
        try:
            for key, value in (headers or {}).items():
                self.send_header(str(key), str(value))
        except Exception:
            pass
        self.end_headers()
        self.wfile.write(body)

    # ---------- console owner gate (first-run setup, then cookie sessions) ----------
    # Always public (no owner needed to reach them):
    _AUTH_PUBLIC_GET = frozenset({
        "/api/health", "/api/auth/status", "/api/auth/login", "/api/auth/setup",
        "/login.html",
    })
    _AUTH_PUBLIC_POST = frozenset({
        "/api/auth/login", "/api/auth/setup",
    })
    # Internal gateway dispatcher (same machine, cookie-less) may POST only
    # these endpoints from loopback. NOTHING else is exempt from login: the
    # Tailscale funnel also arrives via 127.0.0.1, so a blanket loopback
    # bypass would let the whole internet in without a session.
    _GATEWAY_LOOPBACK_POST = frozenset({
        "/api/chat_stream", "/api/chat_answer", "/api/chat_cancel",
    })

    def _auth_gate(self, is_post: bool) -> bool:
        """Enforce the owner gate. Returns True when the request was answered.

        No owner yet: pages go to the setup screen, APIs stay open (first-run
        window — set the owner immediately). Owner set: everything except the
        small public list needs a valid session cookie (APIs 401, pages 302).
        """
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            public = self._AUTH_PUBLIC_POST if is_post else self._AUTH_PUBLIC_GET
            if path in public:
                return False
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            from web.server import console_auth as _ca
            if not _ca.is_setup():
                if is_post or path.startswith("/api/"):
                    return False
                try:
                    peer = self.client_address[0] if self.client_address else ""
                except Exception:
                    peer = ""
                if peer in ("127.0.0.1", "::1"):
                    return False  # internal loopback (gateway dispatcher)
                self.send_response(302)
                self.send_header("Location", "/login.html")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return True
            if _ca.session_valid(_ca.session_from_cookie(self.headers.get("Cookie"))):
                return False
            try:
                peer = self.client_address[0] if self.client_address else ""
            except Exception:
                peer = ""
            if (peer in ("127.0.0.1", "::1") and is_post
                    and path in self._GATEWAY_LOOPBACK_POST):
                return False  # internal gateway dispatcher only (narrow set above)
            if path.startswith("/api/"):
                self._send_json({"error": "login required"}, status=401)
            else:
                self.send_response(302)
                self.send_header("Location", "/login.html")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
            return True
        except Exception:
            return False

    def _read_json_body(self, max_bytes=65536):
        """Read a small JSON body (auth forms only). Returns {} on any failure."""
        try:
            raw = _read_request_body(self.rfile, self.headers, max_bytes)
            if not raw:
                return {}
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _handle_auth_status_get(self):
        """GET /api/auth/status — always public: is first-run setup needed?"""
        try:
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            from web.server import console_auth as _ca
            self._send_json({"setup_needed": not _ca.is_setup(),
                             "email": _ca.owner_email()})
        except Exception as e:
            self._send_json({"error": str(e)[:200]}, status=500)

    def _handle_auth_setup_post(self):
        """POST /api/auth/setup {email,password} — once only, then signs in."""
        try:
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            from web.server import console_auth as _ca
            data = self._read_json_body()
            try:
                email = _ca.setup_owner(str(data.get("email") or ""),
                                        str(data.get("password") or ""))
            except RuntimeError:
                self._send_json({"error": "owner already set"}, status=409)
                return
            except ValueError as e:
                self._send_json({"error": str(e)[:200]}, status=400)
                return
            token = _ca.create_session()
            try:
                from web.server import ws_auth as _wa
                entry = _wa.mint(token)
            except Exception:
                entry = ""
            self._send_json({"ok": True, "email": email, "ws_token": entry}, headers={
                "Set-Cookie": _session_cookie(token)})
        except Exception as e:
            self._send_json({"error": str(e)[:200]}, status=500)

    def _handle_auth_login_post(self):
        """POST /api/auth/login {email,password} — verifies, then signs in."""
        try:
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            from web.server import console_auth as _ca
            if not _ca.is_setup():
                self._send_json({"error": "no owner yet — create it first"}, status=409)
                return
            data = self._read_json_body()
            if not _ca.verify_owner(str(data.get("email") or ""),
                                    str(data.get("password") or "")):
                self._send_json({"error": "wrong email or password"}, status=401)
                return
            token = _ca.create_session()
            try:
                from web.server import ws_auth as _wa
                entry = _wa.mint(token)
            except Exception:
                entry = ""
            self._send_json({"ok": True, "ws_token": entry}, headers={
                "Set-Cookie": _session_cookie(token)})
        except Exception as e:
            self._send_json({"error": str(e)[:200]}, status=500)

    def _handle_auth_logout_post(self):
        """POST /api/auth/logout — drops this session (always ok)."""
        try:
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            from web.server import console_auth as _ca
            sess = _ca.session_from_cookie(self.headers.get("Cookie"))
            _ca.drop_session(sess)
            try:
                from web.server import ws_auth as _wa
                _wa.drop_session_tokens(sess)
            except Exception:
                pass
            self._send_json({"ok": True}, headers={
                "Set-Cookie": _session_cookie("", clear=True)})
        except Exception as e:
            self._send_json({"error": str(e)[:200]}, status=500)

    def _handle_auth_password_post(self):
        """POST /api/auth/password {old_password,new_password} — authed only."""
        try:
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            from web.server import console_auth as _ca
            email = _ca.owner_email()
            if not email:
                self._send_json({"error": "no owner yet"}, status=409)
                return
            data = self._read_json_body()
            try:
                _ca.change_password(email, str(data.get("old_password") or ""),
                                    str(data.get("new_password") or ""))
            except ValueError as e:
                self._send_json({"error": str(e)[:200]}, status=400)
                return
            try:
                from web.server import ws_auth as _wa
                _wa.drop_session_tokens(_ca.session_from_cookie(self.headers.get("Cookie")))
            except Exception:
                pass
            self._send_json({"ok": True})
        except Exception as e:
            self._send_json({"error": str(e)[:200]}, status=500)

    def translate_path(self, path: str) -> str:
        # Use parent logic then validate inside DASHBOARD_ROOT
        # Strip query for safety
        clean = path.split("?")[0].split("#")[0]
        resolved = super().translate_path(clean)
        try:
            # Ensure resolved path is inside dashboard root
            Path(resolved).resolve().relative_to(DASHBOARD_ROOT.resolve())
        except ValueError:
            # Outside allowed directory -> map to not-found sentinel
            return str(DASHBOARD_ROOT / "__forbidden__")
        return resolved

    def end_headers(self):
        # Prevent caching during development for static assets too.
        # getattr: parse_request() may have failed on garbage bytes
        # (internet scanners) leaving self.path unset - send_error()
        # still calls us, and must not crash on top of a 400.
        if not getattr(self, "path", "").startswith("/api/"):
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def log_message(self, format, *args):
        # Clean log to stdout with prefix (guarded: detached pythonw has no stdout)
        try:
            out = sys.stdout
            if out is not None and not getattr(out, "closed", False):
                out.write(f"[dashboard] {self.address_string()} - {format % args}\n")
        except Exception:
            pass

    def handle(self):
        """Route GET /ws to serve_ws BEFORE the HTTP handler runs.

        Peek-only detection (MSG_PEEK, never consumes): WS upgrades go to
        serve_ws (module level, same file) on this same socket; everything
        else keeps the normal HTTP dispatch. The HTTP handler never sees
        /ws bytes.
        """
        try:
            import socket as _socket

            try:
                peer = str(self.client_address[0]) if self.client_address else ""
            except Exception:
                peer = ""
            try:
                self.request.settimeout(3.0)
                # Wait for the full request line: proxies (funnel included)
                # may deliver the upgrade headers in several TCP segments, and
                # a single peek() can return a partial line that misroutes
                # /ws to the HTTP handler (upgrade never completes, wss fails).
                import time as _time
                peek = b""
                deadline = _time.monotonic() + 3.0
                while True:
                    try:
                        chunk = self.request.recv(4096, _socket.MSG_PEEK)
                    except Exception:
                        break
                    if chunk:
                        peek = chunk[:8192]
                    if (not chunk or b"\r\n" in peek or b"\n" in peek
                            or len(chunk) >= 8192
                            or _time.monotonic() >= deadline):
                        break
                    try:
                        _time.sleep(0.01)
                    except Exception:
                        break
            except Exception:
                peek = b""
            finally:
                try:
                    self.request.settimeout(None)
                except Exception:
                    pass
            try:
                line = (peek or b"").split(b"\r\n", 1)[0].decode("latin-1")
                parts = line.strip().split()
                is_ws = (len(parts) >= 2 and parts[0].upper() == "GET"
                         and parts[1].split("?", 1)[0] == "/ws")
            except Exception:
                is_ws = False
            if is_ws:
                try:
                    from web.server.ws_proto import WsConnection

                    conn = WsConnection(self.request, peer)
                    conn.handshake()
                except Exception:
                    try:
                        self.request.close()
                    except Exception:
                        pass
                    return
                try:
                    serve_ws(conn, peer)
                except Exception:
                    pass
                finally:
                    try:
                        self.request.close()
                    except Exception:
                        pass
                return
        except Exception:
            pass
        super().handle()

    def handle_error(self, request, client_address):
        # A vanished client (cancelled fetch, closed tab, dead scanner probe)
        # is routine noise, not a server bug: stay silent instead of dumping
        # a traceback for their disconnect. Real errors still print.
        try:
            exc_type = sys.exc_info()[0]
            if exc_type is not None and issubclass(exc_type, (BrokenPipeError, ConnectionResetError)):
                return
        except Exception:
            pass
        try:
            super().handle_error(request, client_address)
        except Exception:
            pass


class ThreadedServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


def _warmup_models() -> None:
    """Bundled embedding resources first, then best-effort tool warmup.

    The bundled embedding model (project/embedding_model/, offline) is
    checked BEFORE serving; device (GPU/CPU) is detected once and cached.
    Everything else connects through AI Models templates, so there is
    nothing else to preload.
    Never fails loudly: startup continues, tools refuse clearly until ready.
    """
    try:
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        try:
            from core.embedding.bootstrap import ensure_embedding_model

            ensure_embedding_model()
        except Exception as e:
            print(f"[WARN] Embedding resources not ready: {e}", file=sys.stderr)
        try:
            from core.tools.tool_search.tool import execute as _tool_search

            _tool_search("warmup")
        except Exception:
            pass
    except Exception:
        pass


threading.Thread(target=_warmup_models, daemon=True).start()


def _enable_public_share_bg(port: int) -> None:
    """Background Funnel enable after the port answers (best-effort, never raises)."""
    import socket as _socket

    for _ in range(30):
        try:
            probe = _socket.create_connection(("127.0.0.1", port), timeout=2.0)
            try:
                probe.close()
            except Exception:
                pass
            break
        except Exception:
            time.sleep(1)
    try:
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from web.server.tailscale_share import ensure_public
        snap = ensure_public(port=port, open_browser=False)
        if snap.get("public_url"):
            print(f"  PUBLIC console (anyone can open): {snap['public_url']}")
            _print_final_report(port, snap["public_url"])
    except Exception as e:
        print(f"[WARN] Public share failed: {e}", file=sys.stderr)


def _print_final_report(port: int, public_url: str) -> None:
    """Clear screen + the final 3-line report (Web Server Local/Public/Running).

    Runs after funnel succeeds: everything the user needs, nothing else.
    Skips the clear when stdout is not a terminal (logs stay intact).
    Never raises.
    """
    try:
        local = f"http://127.0.0.1:{port}/"
        cleared = False
        try:
            import shutil as _shutil
            import subprocess as _sp

            if sys.stdout.isatty() and _shutil.which("clear"):
                _sp.run(["clear"], timeout=5)
                cleared = True
        except Exception:
            pass
        if not cleared:
            print()
        print("=" * 70)
        print(f"  Web Server (Local):  {local}")
        print(f"  Web Server (Public): {public_url}")
        print("  Server Running Now")
        print("=" * 70)
    except Exception:
        pass


def run(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, public: bool = False) -> int:
    if not DASHBOARD_ROOT.exists():
        print(f"[ERROR] Dashboard root not found: {DASHBOARD_ROOT}", file=sys.stderr)
        return 1

    # Validate host/port
    if not isinstance(port, int) or not (1 <= port <= 65535):
        print(f"[ERROR] Invalid port: {port}", file=sys.stderr)
        return 1

    # Single-owner guard: Windows SO_REUSEADDR lets a second process bind the
    # same port, which would split traffic (and the browser) between two
    # owners. Refuse to start when someone already answers here. Probe
    # loopback (connecting to 0.0.0.0 is invalid and never answers).
    try:
        import socket as _socket
        _probe = _socket.create_connection(("127.0.0.1", port), timeout=2.0)
        try:
            _probe.close()
        except Exception:
            pass
        print(f"[ERROR] Already running at http://{host}:{port}/ — not starting a second copy.", file=sys.stderr)
        return 1
    except Exception:
        pass

    # Live links live in the library only (see X5Browser LiveLinks): a previous
    # process cannot leave one behind, so there is nothing to clear at boot.

    # Gateway ingestion (Telegram poller + WhatsApp push wiring): daemon
    # thread for Telegram only; WhatsApp arrives via bridge push events and
    # agent runs automatically as channel messages arrive.
    try:
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from gateway.service import start as _gw_start

        _gw_start(f"http://{host}:{port}/")
    except Exception as e:
        print(f"[WARN] Gateway ingestion failed to start: {e}", file=sys.stderr)

    # Scheduler engine ticker (due jobs run in background, isolated mode).
    try:
        _start_scheduler_loop()
    except Exception as e:
        print(f"[WARN] Scheduler loop failed to start: {e}", file=sys.stderr)

    # WS sockets are detected in ThreadedServer.handle (MSG_PEEK on the raw
    # socket, before HTTP runs) and served by serve_ws above. Broadcasts fan
    # out to WS tabs inside _events_broadcast itself — no bridge needed.
    # Plain wss works automatically behind the funnel: TLS terminates above us.

    try:
        with ThreadedServer((host, port), DashboardHandler) as httpd:
            url = f"http://{host}:{port}/"
            print("=" * 70)
            print(f"  X5Agent — your private AI gateway console")
            print(f"  {DashboardHandler.SERVER_DESCRIPTION}")
            print(f"  Serving: {DASHBOARD_ROOT}")
            print(f"  URL:     {url}")
            if public:
                print(f"  Public:  enabling Tailscale Funnel ...")
                threading.Thread(target=_enable_public_share_bg, args=(port,),
                                 name="x5-public-share", daemon=True).start()
            print(f"  Press Ctrl+C to stop")
            print("=" * 70)
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print("\n[INFO] Shutting down...")
                httpd.shutdown()
                return 0
            finally:
                try:
                    from gateway.service import stop as _gw_stop

                    _gw_stop()
                except Exception:
                    pass
    except OSError as e:
        import errno as _errno

        if e.errno in (_errno.EADDRINUSE, 98, 10013):
            # Port held by a stale/wedged server (answers nothing but keeps
            # the socket): only freeing it helps, never another start attempt.
            print(f"[ERROR] Port {port} is busy but nothing answers on it "
                  f"(stale server holding the socket).", file=sys.stderr)
            print('  Free it, then start again:  pkill -f "web.server.server"; x5agent',
                  file=sys.stderr)
        else:
            print(f"[ERROR] Failed to bind {host}:{port} — {e}", file=sys.stderr)
            print("  Try: python -m web.server.server --port 7777", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="X5Agent local server")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Host to bind (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to bind (default 7777)")
    parser.add_argument("--public", action="store_true",
                        help="Also expose via Tailscale Funnel (permanent public https URL)")
    args = parser.parse_args()

    # Validate host is loopback for safety (no 0.0.0.0 exposure by default)
    if args.host not in ("127.0.0.1", "localhost"):
        print(f"[WARN] Binding to non-loopback host {args.host} — ensure firewall", file=sys.stderr)

    raise SystemExit(run(host=args.host, port=args.port, public=args.public))
