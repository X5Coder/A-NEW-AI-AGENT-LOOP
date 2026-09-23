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

SYSTEM_PROMPT_PATH = Path(__file__).resolve().parents[2] / "agent" / "prompts" / "system.md"
STORAGE_DB = Path(__file__).resolve().parents[3] / "storage" / "database"


def _read_system_prompt() -> str:
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


def _env_block() -> str:
    now = datetime.now(timezone.utc).astimezone()
    os_info = f"{platform.system()} {platform.release()} ({platform.machine()})"
    cwd = str(Path.cwd().resolve())
    # project root
    proj = str(STORAGE_DB.parent.parent.resolve())
    return (
        f"[Environment]\n"
        f"- DateTime: {now.strftime('%Y-%m-%d %H:%M:%S %Z')} (ISO: {now.isoformat()})\n"
        f"- OS: {os_info}\n"
        f"- Python: {platform.python_version()}\n"
        f"- CWD: {cwd}\n"
        f"- Project: {proj}\n"
    )


def _memories_block() -> str:
    parts: list[str] = []
    # user_memory
    um = _load_json(STORAGE_DB / "user_memory.json", {})
    if isinstance(um, dict) and um:
        lines = [f"  - {k}: {v.get('value') if isinstance(v, dict) else str(v)[:200]}" for k, v in list(um.items())[-20:]]
        parts.append("[User Memory]\n" + "\n".join(lines))
    # agent_memory
    am = _load_json(STORAGE_DB / "agent_memory.json", {})
    if isinstance(am, dict) and am:
        lines = [f"  - {k}: {v.get('value') if isinstance(v, dict) else str(v)[:200]}" for k, v in list(am.items())[-20:]]
        parts.append("[Agent Memory]\n" + "\n".join(lines))
    # image_memory - only persistent with vision_text and all persistent
    im = _load_json(STORAGE_DB / "image_memory.json", [])
    if isinstance(im, list) and im:
        persistent = [x for x in im if x.get("is_persistent")]
        if persistent:
            lines = []
            for x in persistent[-10:]:
                vt = x.get("vision_text") or ""
                lines.append(f"  - {x.get('image_id')}: {x.get('name')} ({x.get('type')}, {x.get('width')}x{x.get('height')}) path={x.get('path')} vision='{vt[:120]}'")
            parts.append("[Image Memory - persistent, sent every request]\n" + "\n".join(lines))
            # also show with vision count
    # important_memory
    imp = _load_json(STORAGE_DB / "important_memory.json", [])
    if isinstance(imp, list) and imp:
        lines = [f"  - {x.get('id')}: [{x.get('key') or 'note'}] {x.get('content','')[:150]}" for x in imp[-20:]]
        parts.append("[Important Memory]\n" + "\n".join(lines))
    if not parts:
        return ""
    return "\n\n".join(parts)


def _chat_block(limit: int = 30) -> str:
    # conversation.json or conversation_history.json
    for cand in [STORAGE_DB / "conversation.json", STORAGE_DB / "chat.json", STORAGE_DB / "conversation_history.json"]:
        data = _load_json(cand, None)
        if isinstance(data, list) and data:
            last = data[-limit:]
            lines = []
            for m in last:
                role = m.get("role", "user")
                content = str(m.get("content",""))[:400]
                lines.append(f"  {role}: {content}")
            return "[Last 30 messages]\n" + "\n".join(lines)
    return ""


def _todo_block() -> str:
    todos = _load_json(STORAGE_DB / "todos.json", [])
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
    return "[Todo List - current plan]\n" + "\n".join(lines)


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
            name = spec.get("name")
            desc = spec.get("description","")
            inputs = spec.get("inputs", {})
            # Build JSON schema
            props: dict = {}
            required: list[str] = []
            for k, v in inputs.items():
                typ = v.get("type","string")
                # map integer -> integer etc
                json_type = "string"
                if typ == "integer":
                    json_type = "integer"
                elif typ == "boolean":
                    json_type = "boolean"
                elif typ == "array":
                    json_type = "array"
                elif typ == "number":
                    json_type = "number"
                prop: dict[str, Any] = {"type": json_type, "description": v.get("description","")}
                if typ == "array" and "items" in v:
                    prop["items"] = v["items"]
                if "enum" in v:
                    prop["enum"] = v["enum"]
                if "default" in v:
                    prop["default"] = v["default"]
                props[k] = prop
                if v.get("required"):
                    required.append(k)
            parameters: dict[str, Any] = {"type":"object","properties":props}
            if required:
                parameters["required"] = required
            tools.append({
                "type":"function",
                "function":{"name": name, "description": desc, "parameters": parameters}
            })
        except Exception:
            continue
    return tools


def build_context(user_input: str, include_todo: bool = True, isolated: bool = False, todo_snapshot: dict | None = None) -> tuple[str, str, list[dict]]:
    """
    Returns (system_prompt, user_context_content, core_tools_openai)
    User context is a single professional block to be sent as user message content (system prompt separate).
    isolated=True for scheduled large tasks: skips last-30 chat history (irrelevant after a day) and uses snapshot.
    """
    if not isinstance(user_input, str):
        raise TypeError("user_input must be string")
    system_prompt = _read_system_prompt()
    env = _env_block()
    mem = _memories_block()
    # isolated mode: no chat history, use snapshot todo
    if isolated:
        chat = ""
        if todo_snapshot and isinstance(todo_snapshot, dict):
            steps = todo_snapshot.get("steps") or []
            task = str(todo_snapshot.get("task",""))[:200]
            tid = todo_snapshot.get("id","")
            snap = f"[Scheduled Todo Snapshot - isolated, no chat history]\n- [{todo_snapshot.get('status','planned')}] {tid}: {task}\n" + "\n".join([f"  {i+1}. {s}" for i, s in enumerate(steps)])
            todo = snap
        else:
            todo = _todo_block() if include_todo else ""
    else:
        chat = _chat_block(limit=30)
        todo = _todo_block() if include_todo else ""
    parts = []
    parts.append(env)
    if mem:
        parts.append(mem)
    else:
        parts.append("[Memories: empty]")
    if isolated:
        parts.append("[Chat: excluded for isolated scheduled execution - large task reliability]")
    else:
        if chat:
            parts.append(chat)
        else:
            parts.append("[Chat: no prior messages]")
    if todo:
        parts.append(todo)
    parts.append("[Available CORE tools: tool_search, question, vector_chat_search, todo_list, scheduler, skills_search, skills_view, terminal]")
    parts.append(f"[Current User Input]\n{user_input.strip()}")
    if isolated:
        parts.append("[Mode: ISOLATED SCHEDULED EXECUTION - execute exactly as in snapshot, do not ask for clarification, complete the workflow]")
    user_content = "\n\n".join(parts)
    core_tools = _core_tools_block()
    return system_prompt, user_content, core_tools


def get_core_tools_openai() -> list[dict]:
    return _core_tools_block()
