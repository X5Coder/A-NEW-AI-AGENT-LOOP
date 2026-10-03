"""
Context builder - assembles full professional context each loop iteration.
Single responsibility: build system prompt + memories + chat + env + todo + core tools into messages.
"""
from __future__ import annotations

import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SYSTEM_PROMPT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "system.md"
CORE_IDENTITY_PATH = Path(__file__).resolve().parents[3] / "prompts" / "core_identity.md"
GENERAL_MISSION_PATH = Path(__file__).resolve().parents[3] / "prompts" / "general_mission.md"
POLICY_PATH = Path(__file__).resolve().parents[3] / "prompts" / "policy.md"
STORAGE_DB = Path(__file__).resolve().parents[3] / "storage" / "database"
# X5Agent/Data/database (new location, zip-backup). Read Data first, fallback legacy.
X5AGENT_DATA_DB = Path(__file__).resolve().parents[4] / "X5Agent" / "Data" / "database"


def _read_text_file(path: Path) -> str:
    try:
        if path.exists():
            text = path.read_text(encoding="utf-8").strip()
            return text if text else ""
        return ""
    except Exception:
        return ""


def _read_system_prompt() -> str:
    # Layer order L1+L2+L3: core_identity + general_mission + policy.
    # Fallback to legacy system.md if new layered files are missing.
    l1 = _read_text_file(CORE_IDENTITY_PATH)
    l2 = _read_text_file(GENERAL_MISSION_PATH)
    l3 = _read_text_file(POLICY_PATH)
    if l1 or l2 or l3:
        parts = [p for p in [l1, l2, l3] if p]
        return "\n\n".join(parts).strip()
    try:
        if SYSTEM_PROMPT_PATH.exists():
            text = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
            return text if text else "You are a helpful AI agent."
        return "You are a helpful AI agent."
    except Exception:
        return "You are a helpful AI agent."


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if data is not None else default
    except Exception:
        return default


def _load_db_json(filename: str, default: Any) -> Any:
    # Prefer X5Agent/Data/database (backup), fallback legacy storage/database.
    try:
        new_path = X5AGENT_DATA_DB / filename
        if new_path.exists():
            data = _load_json(new_path, None)
            if data is not None and data != {} and data != []:
                return data
    except Exception:
        pass
    return _load_json(STORAGE_DB / filename, default)


def _ensure_x5agent_best_effort() -> None:
    # Ensure sandbox dirs + one-time legacy->Data migration (idempotent, never overwrites Data).
    try:
        import sys as _sys
        _proj = Path(__file__).resolve().parents[3]
        if str(_proj) not in _sys.path:
            _sys.path.insert(0, str(_proj))
        from core.paths import ensure_x5agent, migrate_legacy_to_data_once
        ensure_x5agent()
        migrate_legacy_to_data_once()
        return
    except Exception:
        pass
    try:
        ws = Path(__file__).resolve().parents[4] / "X5Agent" / "Workspace"
        data = Path(__file__).resolve().parents[4] / "X5Agent" / "Data"
        for d in [ws, data, data / "database", data / "vector",
                  data / "files" / "images", data / "files" / "audio",
                  data / "files" / "videos", data / "skills",
                  data / "configs" / "plugin_configs"]:
            try:
                d.mkdir(parents=True, exist_ok=True)
            except Exception:
                continue
    except Exception:
        pass


def _workspace_path_str() -> str:
    try:
        ws = (Path(__file__).resolve().parents[4] / "X5Agent" / "Workspace").resolve()
        return str(ws)
    except Exception:
        return "X5Agent/Workspace"


def _browser_state_str() -> str:
    # Live browser state for the main context (read-only probe, never starts it).
    # Control/preview ownership comes straight from the library's session links
    # (no URL: stream links stay redacted from the model, the UI renders them).
    try:
        import sys as _sys
        _proj = Path(__file__).resolve().parents[3]
        if str(_proj) not in _sys.path:
            _sys.path.insert(0, str(_proj))
        from X5Browser.service import live_links as _links
        links = _links()
    except Exception:
        return "unknown"
    if not isinstance(links, dict) or not links.get("running"):
        return "stopped"
    if links.get("owner") == "user":
        return ("running (CONTROL live active — user is driving; page refs may be "
                "stale, re-snapshot after resume)")
    return "running (preview live active — user watches only, agent keeps control)"


def _env_block() -> str:
    # L4 system_info: time + OS + exact Workspace path + jail notice.
    _ensure_x5agent_best_effort()
    now = datetime.now(timezone.utc).astimezone()
    os_info = f"{platform.system()} {platform.release()} ({platform.machine()})"
    ws = _workspace_path_str()
    proj = str(STORAGE_DB.parent.parent.resolve())
    return (
        f"[System Info - L4]\n"
        f"- DateTime: {now.strftime('%Y-%m-%d %H:%M:%S %Z')} (ISO: {now.isoformat()})\n"
        f"- OS: {os_info}\n"
        f"- Python: {platform.python_version()}\n"
        f"- You are inside: {ws}\n"
        f"- This Workspace folder is your device. Do EVERYTHING here (experiments, files, folders, terminal).\n"
        f"- FORBIDDEN to exit Workspace by any method (.., absolute paths, symlinks, shell tricks). "
        f"Any path outside Workspace is BLOCKED automatically.\n"
        f"- System Core (read-only): {proj} - never write it. Data (X5Agent/Data) via memory/config tools only.\n"
        f"- Browser: {_browser_state_str()} (when running, a live preview bar is shown to the user automatically).\n"
        f"- If the user asks whether the browser runs, answer DIRECTLY from that state line only — "
        f"do not call any browser tool just to check (get_page_info is read-only and never boots it). "
        f"If they ask to open/start it, call open_browser (it boots on demand). "
        f"Never call open_browser on your own: when a browser tool fails with 'browser stopped', "
        f"tell the user it is stopped instead of opening it.\n"
    )


def _workspace_block() -> str:
    # Workspace top level only: folders as folder/ with item counts, files
    # with sizes. The agent enters folders with the workspace tool (list).
    # Bounded: first 100 entries, counts capped — never walks inside.
    try:
        ws = (Path(__file__).resolve().parents[4] / "X5Agent" / "Workspace").resolve()
    except Exception:
        return ""
    try:
        if not ws.is_dir():
            return "[Workspace - your work device: empty]"
        children = sorted(
            (p for p in ws.iterdir() if p.is_dir() or p.is_file()),
            key=lambda p: (not p.is_dir(), p.name.lower()),
        )
    except Exception:
        return ""
    if not children:
        return "[Workspace - your work device: empty]"
    lines = []
    for p in children[:100]:
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
                    n = 0
                lines.append(f"  folder: {p.name}/ ({n}{'+' if trunc else ''} items — list it to enter)")
            else:
                try:
                    size = p.stat().st_size
                except Exception:
                    size = 0
                lines.append(f"  file: {p.name} ({size} bytes)")
        except Exception:
            continue
    more = "" if len(children) <= 100 else f"\n  ... +{len(children) - 100} more (list Workspace to browse)"
    return ("[Workspace - your work device, top level only - L4]\n"
            "  Every file, upload, or project lives here. One new folder per project/task.\n"
            + "\n".join(lines) + more)


def _memories_block() -> str:
    # L5: all memories (user/agent/image-persistent/important). Chat last30 is L6 separate.
    parts: list[str] = []
    # user_memory
    um = _load_db_json("user_memory.json", {})
    if isinstance(um, dict) and um:
        lines = [f"  - {k}: {v.get('value') if isinstance(v, dict) else str(v)[:200]}" for k, v in list(um.items())[-20:]]
        parts.append("[User Memory - L5]\n" + "\n".join(lines))
    # agent_memory
    am = _load_db_json("agent_memory.json", {})
    if isinstance(am, dict) and am:
        lines = [f"  - {k}: {v.get('value') if isinstance(v, dict) else str(v)[:200]}" for k, v in list(am.items())[-20:]]
        parts.append("[Agent Memory - L5]\n" + "\n".join(lines))
    # image_memory - only persistent with vision_text and all persistent
    im = _load_db_json("image_memory.json", [])
    if isinstance(im, list) and im:
        persistent = [x for x in im if x.get("is_persistent")]
        if persistent:
            lines = []
            for x in persistent[-10:]:
                vt = x.get("vision_text") or ""
                lines.append(f"  - {x.get('image_id')}: {x.get('name')} ({x.get('type')}, {x.get('width')}x{x.get('height')}) path={x.get('path')} vision='{vt[:120]}'")
            parts.append("[Image Memory - persistent, sent every request - L5]\n" + "\n".join(lines))
            # also show with vision count
    # important_memory
    imp = _load_db_json("important_memory.json", [])
    if isinstance(imp, list) and imp:
        lines = [f"  - {x.get('id')}: [{x.get('key') or 'note'}] {x.get('content','')[:150]}" for x in imp[-20:]]
        parts.append("[Important Memory - L5]\n" + "\n".join(lines))
    if not parts:
        return ""
    return "\n\n".join(parts)


def _chat_block(limit: int = 30) -> str:
    # L6a: last 30 messages - explicit layer, never skipped except isolated mode.
    for cand in [X5AGENT_DATA_DB / "conversation.json", STORAGE_DB / "conversation.json", STORAGE_DB / "chat.json", STORAGE_DB / "conversation_history.json"]:
        data = _load_json(cand, None)
        if isinstance(data, list) and data:
            last = data[-limit:]
            lines = []
            for m in last:
                role = m.get("role", "user")
                content = str(m.get("content",""))[:400]
                lines.append(f"  {role}: {content}")
            return "[Last 30 messages - L6]\n" + "\n".join(lines)
    return ""


def _documents_block() -> str:
    # Documents metadata: Workspace files MarkItDown can convert + index state.
    # Bounded (first 20), read-only, never embeds — indexing happens lazily
    # inside document_search. Also purges orphan embeddings best-effort so a
    # file deleted from Workspace never leaves stale vectors behind.
    try:
        import sys as _sys
        _proj = Path(__file__).resolve().parents[3]
        if str(_proj) not in _sys.path:
            _sys.path.insert(0, str(_proj))
        from core.documents.store import list_documents
        docs = list_documents()
    except Exception:
        return ""
    if not docs:
        return ""
    lines = []
    for d in docs[:20]:
        try:
            state = f"{d.get('chunk_count', 0)} chunks" if d.get("indexed") else "not indexed yet"
            lines.append(f"  - {d.get('file_id')}: {state} ({d.get('size', 0)} bytes)")
        except Exception:
            continue
    if not lines:
        return ""
    more = "" if len(docs) <= 20 else f"\n  ... +{len(docs) - 20} more"
    return ("[Documents metadata - searchable via document_search - L4]\n"
            "  Supported: PDF, Word, Excel, PowerPoint, csv/txt/md/html/json/xml.\n"
            "  Search one file with document_search(file_id, query, top_k).\n"
            + "\n".join(lines) + more)


def _todo_block() -> str:
    todos = _load_db_json("todos.json", [])
    if not isinstance(todos, list) or not todos:
        return ""
    # bounded: show last 10
    lines = []
    for t in todos[-10:]:
        task = str(t.get("task",""))[:120]
        steps = t.get("steps")
        status = t.get("status","")
        tid = t.get("id","")
        lines.append(f"  - [{status}] {tid}: {task}")
        if isinstance(steps, list) and steps:
            for i, s in enumerate(steps, 1):
                lines.append(f"      {i}. {str(s)[:80]}")
    return "[Todo List - current plan - L6]\n" + "\n".join(lines)


def _triggers_block() -> str:
    triggers = _load_db_json("triggers.json", [])
    if not isinstance(triggers, list) or not triggers:
        return ""
    # Only active triggers
    active = [t for t in triggers if t.get("status") in (None, "active")]
    if not active:
        return ""
    lines = []
    for t in active[-10:]:
        tid = t.get("trigger_id","")
        tname = t.get("trigger_name","")
        task = str(t.get("task",""))[:80]
        skill = t.get("skill_ref","")
        cfg = t.get("config") or {}
        cfg_str = json.dumps(cfg, ensure_ascii=False)[:100] if isinstance(cfg, dict) and cfg else ""
        lines.append(f"  - {tid} [{tname}] task: {task} skill: {skill} config: {cfg_str}")
    return "[Active Triggers - background, one-time, will die after firing]\n" + "\n".join(lines) + "\n  (Use trigger tool to edit/delete, trigger_search to find more)"


def openai_tool_from_spec(name: str, description: str, inputs: dict) -> dict:
    """Convert a spec.json-style (name, description, inputs) into an OpenAI function tool."""
    props: dict = {}
    required: list[str] = []
    for k, v in (inputs or {}).items():
        if not isinstance(v, dict):
            continue
        typ = v.get("type", "string")        # map spec types to JSON schema types
        json_type = "string"
        if typ == "integer":
            json_type = "integer"
        elif typ == "boolean":
            json_type = "boolean"
        elif typ == "array":
            json_type = "array"
        elif typ == "number":
            json_type = "number"
        prop: dict[str, Any] = {"type": json_type, "description": v.get("description", "")}
        if typ == "array" and "items" in v:
            prop["items"] = v["items"]
        if "enum" in v:
            prop["enum"] = v["enum"]
        if "default" in v:
            prop["default"] = v["default"]
        props[k] = prop
        if v.get("required"):
            required.append(k)
    # Optional Reason node on EVERY tool: the model may explain why it calls
    # this tool (shown when the user expands the step). Never required; the
    # engine strips it before execution so tools never see it.
    if "reason" not in props:
        props["reason"] = {
            "type": "string",
            "description": "Why you call this tool (max 15 words, English). Shown in the UI when the user expands this step.",
        }
    parameters: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        parameters["required"] = required
    return {
        "type": "function",
        "function": {"name": name, "description": description or "", "parameters": parameters},
    }


def _core_tools_block() -> list[dict]:
    """Return core tools OpenAI-format specs."""
    tools: list[dict] = []
    core_root = Path(__file__).resolve().parents[2] / "tools"
    for d in sorted(core_root.iterdir()):
        if not d.is_dir():
            continue
        spec_path = d / "spec.json"
        if not spec_path.exists():
            continue
        try:
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            tools.append(openai_tool_from_spec(spec.get("name"), spec.get("description", ""), spec.get("inputs", {})))
        except Exception:
            continue
    return tools


def build_context(user_input: str, include_todo: bool = True, isolated: bool = False, todo_snapshot: dict | None = None) -> tuple[str, str, list[dict]]:
    """
    8-layer context order (fixed):
      L1 core_identity.md + L2 general_mission.md + L3 policy.md -> system role
      L4 system_info (time + Workspace path + jail) -> user content
      L5 memories (user/agent/image-persistent/important) -> user content
      L6 chat last30 + todo + triggers (explicit, never merged into L5) -> user content
      L7 available core tools list -> user content
      L8 current user input (ALWAYS last) -> user content
    Returns (system_prompt, user_context_content, core_tools_openai)
    isolated=True for scheduled large tasks: skips last-30 chat history and uses snapshot.
    """
    if not isinstance(user_input, str):
        raise TypeError("user_input must be string")
    # L1+L2+L3
    system_prompt = _read_system_prompt()
    # L4
    env = _env_block()
    ws_block = _workspace_block()
    docs_block = _documents_block()
    # L5
    mem = _memories_block()
    # L6: chat + todo + triggers (explicit separate layer)
    if isolated:
        chat = ""
        if todo_snapshot and isinstance(todo_snapshot, dict):
            steps = todo_snapshot.get("steps") or []
            task = str(todo_snapshot.get("task",""))[:200]
            tid = todo_snapshot.get("id","")
            snap = f"[Scheduled Todo Snapshot - isolated, no chat history - L6]\n- [{todo_snapshot.get('status','planned')}] {tid}: {task}\n" + "\n".join([f"  {i+1}. {s}" for i, s in enumerate(steps)])
            todo = snap
        else:
            todo = _todo_block() if include_todo else ""
    else:
        chat = _chat_block(limit=30)
        todo = _todo_block() if include_todo else ""
    parts = []
    parts.append(env)
    if ws_block:
        parts.append(ws_block)
    if docs_block:
        parts.append(docs_block)
    if mem:
        parts.append(mem)
    else:
        parts.append("[Memories L5: empty]")
    if isolated:
        parts.append("[Chat L6: excluded for isolated scheduled execution - large task reliability]")
    else:
        if chat:
            parts.append(chat)
        else:
            parts.append("[Chat L6: no prior messages]")
    if todo:
        parts.append(todo)
    # Active triggers metadata + skill name (so AI can edit/delete via same trigger tool)
    triggers_block = _triggers_block()
    if triggers_block:
        parts.append(triggers_block)
    # L7
    parts.append("[Available CORE tools L7: tool_search, trigger_search, trigger, question, vector_chat_search, document_search, todo_list, scheduler, skills_search, skills_view, terminal, wait, workspace]")
    # L8 ALWAYS last (with bridge: re-anchor attention after the long L4-L7 context)
    parts.append(f"[Bridge] Based on all layers above, execute this task.\n\n[Current User Input - L8]\n{user_input.strip()}")
    if isolated:
        parts.append("[Mode: ISOLATED SCHEDULED EXECUTION - execute exactly as in snapshot, do not ask for clarification, complete the workflow]")
    user_content = "\n\n".join(parts)
    core_tools = _core_tools_block()
    return system_prompt, user_content, core_tools


def get_core_tools_openai() -> list[dict]:
    return _core_tools_block()
