"""
Auto Memory Service - triggers every 50 messages.
1. Extract facts about user/agent from last 50 messages (with chat)
2. Store in user_memory/agent_memory
3. Organize memory (deduplicate) using only system prompt (no chat) - second request
Ownership: core/memory, lifecycle: called after _persist_conversation, bounded, async in thread.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

def _db_dirs() -> tuple[Path, Path]:
    # Unified: Data first, legacy fallback (read-only).
    legacy = Path(__file__).resolve().parents[2] / "storage" / "database"
    try:
        import sys as _sys
        _proj = Path(__file__).resolve().parents[2]
        _root = _proj if _proj.name == "project" else Path(__file__).resolve().parents[3]
        if str(_root) not in _sys.path:
            _sys.path.insert(0, str(_root))
        from core.paths import data_db_dir as _dd
        return _dd(), legacy
    except Exception:
        return legacy, legacy


def _resolve_db(name: str) -> Path:
    data_db, legacy_db = _db_dirs()
    cand = data_db / name
    if cand.exists():
        return cand
    return legacy_db / name


STORAGE_DB, _LEGACY_DB = _db_dirs()
PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

# Prompt files
USER_EXTRACT_PROMPT = PROMPTS_DIR / "user_memory_extract_system.md"
USER_ORGANIZE_PROMPT = PROMPTS_DIR / "user_memory_organize_system.md"
AGENT_EXTRACT_PROMPT = PROMPTS_DIR / "agent_memory_extract_system.md"
AGENT_ORGANIZE_PROMPT = PROMPTS_DIR / "agent_memory_organize_system.md"

TRIGGER_EVERY = 50  # every 50 messages


def _read_prompt(path: Path) -> str:
    if path.exists():
        try:
            return path.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return ""


def _load_conversation() -> list[dict]:
    for p in (_resolve_db("conversation.json"), _LEGACY_DB / "conversation.json"):
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            continue
    return []


def _should_trigger() -> bool:
    conv = _load_conversation()
    n = len(conv)
    # Trigger only when n is multiple of 50 and n > 0, and we haven't triggered for this batch before
    # Use a marker file to avoid double-trigger (marker lives in Data - writes go to Data)
    marker = STORAGE_DB / ".auto_memory_last_trigger"
    legacy_marker = _LEGACY_DB / ".auto_memory_last_trigger"
    last = 0
    for cand in (marker, legacy_marker):
        if cand.exists():
            try:
                last = int(cand.read_text(encoding="utf-8").strip())
                break
            except Exception:
                continue
    if n >= TRIGGER_EVERY and n % TRIGGER_EVERY == 0 and n != last:
        return True
    # Also handle case where n just crossed 50 but not exactly multiple due to 2 messages per turn (user+assistant)
    # We store 2 messages per loop, so counts are even. 50 is even, so it will hit exactly.
    # If somehow missed, check if n - last >= 50
    if n - last >= TRIGGER_EVERY:
        return True
    return False


def _mark_triggered(n: int) -> None:
    marker = STORAGE_DB / ".auto_memory_last_trigger"
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(n), encoding="utf-8")
    except Exception:
        pass


MEMORY_MODEL = "deepseek/deepseek-chat"  # pinned: v4.1-flash returns empty on Arabic repetitive input


def _call_llm(system_prompt: str, user_content: str, api_key: str | None = None) -> str:
    from core.ai_models.store import require_text_model
    from core.ai_models.engine import chat as universal_chat
    _record = require_text_model()  # refuses clearly when no text model configured
    last_err: Exception | None = None
    for attempt in range(1, 4):
        try:
            content = universal_chat(
                _record,
                [{"role": "user", "content": user_content}],
                {"system": system_prompt},
            )
        except Exception as e:
            last_err = e
            if attempt == 3:
                raise
            time.sleep(attempt)
            continue
        if isinstance(content, str) and content.strip():
            return content.strip()
        last_err = RuntimeError("LLM returned empty for auto memory")
        if attempt < 3:
            time.sleep(attempt)
    raise RuntimeError(f"LLM returned empty for auto memory after 3 tries: {last_err}")


def _parse_facts_json(text: str) -> list[dict]:
    """Extract facts list from LLM JSON output, robust to markdown fences."""
    import re
    # Try to find JSON block
    # Remove markdown fences
    t = text.strip()
    # Find first { and last }
    # Try direct json
    try:
        data = json.loads(t)
        if isinstance(data, dict) and "facts" in data and isinstance(data["facts"], list):
            return data["facts"]
    except Exception:
        pass
    # Try extract JSON inside ```json ... ```
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", t, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            if isinstance(data, dict) and "facts" in data:
                return data["facts"]
        except Exception:
            pass
    # Try find first { to last }
    m2 = re.search(r"\{.*\"facts\".*\}", t, re.DOTALL)
    if m2:
        try:
            data = json.loads(m2.group(0))
            if isinstance(data, dict) and "facts" in data:
                return data["facts"]
        except Exception:
            pass
    return []


def _extract_and_store(target: str, last_50: list[dict]) -> int:
    """target: user or agent, returns number of new facts stored."""
    if target not in ("user","agent"):
        raise ValueError("target must be user or agent")
    system_path = USER_EXTRACT_PROMPT if target == "user" else AGENT_EXTRACT_PROMPT
    system_prompt = _read_prompt(system_path)
    if not system_prompt:
        raise FileNotFoundError(f"Prompt not found: {system_path}")
    # Build user content: last 50 messages
    lines = []
    for m in last_50[-50:]:
        role = m.get("role","user")
        content = str(m.get("content",""))[:200]
        lines.append(f"{role}: {content}")
    user_content = "\n".join(lines) if lines else "No messages"
    # Call LLM (with chat)
    raw = _call_llm(system_prompt, user_content)
    facts = _parse_facts_json(raw)
    if not isinstance(facts, list):
        facts = []
    # Store each fact
    stored = 0
    if target == "user":
        from core.memory.user_memory.service import set_value
        for f in facts[:20]:  # bounded
            if not isinstance(f, dict):
                continue
            k = str(f.get("key") or f.get("name") or "").strip()
            v = str(f.get("value") or f.get("content") or "").strip()
            if not k or not v:
                continue
            if len(k) > 100 or len(v) > 500:
                continue
            try:
                set_value(k, v)
                stored += 1
            except Exception:
                continue
    else:
        from core.memory.agent_memory.service import set_value
        for f in facts[:20]:
            if not isinstance(f, dict):
                continue
            k = str(f.get("key") or "").strip()
            v = str(f.get("value") or "").strip()
            if not k or not v:
                continue
            try:
                set_value(k, v)
                stored += 1
            except Exception:
                continue
    return stored


def _organize_memory(target: str) -> int:
    """Organize memory via second request (no chat, only system prompt + current memory). Returns deduplicated count."""
    if target not in ("user","agent"):
        raise ValueError("target")
    system_path = USER_ORGANIZE_PROMPT if target == "user" else AGENT_ORGANIZE_PROMPT
    system_prompt = _read_prompt(system_path)
    if not system_prompt:
        raise FileNotFoundError(f"Prompt not found: {system_path}")
    # Load current memory
    if target == "user":
        from core.memory.user_memory.service import list_all as list_user, clear_all as clear_user, set_value as set_user
        mem = list_user()
        # Convert to facts list for LLM
        facts = [{"key": k, "value": (v.get("value") if isinstance(v, dict) else str(v))} for k,v in mem.items()]
    else:
        from core.memory.agent_memory.service import list_all as list_agent, clear_all as clear_agent, set_value as set_agent
        mem = list_agent()
        facts = [{"key": k, "value": (v.get("value") if isinstance(v, dict) else str(v))} for k,v in mem.items()]
    if not facts:
        return 0
    user_content = json.dumps({"facts": facts}, ensure_ascii=False, indent=2)
    # Call LLM with ONLY system prompt + memory (no chat)
    raw = _call_llm(system_prompt, user_content)
    organized = _parse_facts_json(raw)
    if not isinstance(organized, list) or not organized:
        # If LLM returned empty or invalid, keep original
        return 0
    # Replace memory with organized version (deduplicated, no new info)
    if target == "user":
        clear_user()
        for f in organized[:200]:
            k = str(f.get("key") or "").strip()
            v = str(f.get("value") or "").strip()
            if not k or not v:
                continue
            try:
                set_user(k, v)
            except Exception:
                continue
    else:
        clear_agent()
        for f in organized[:200]:
            k = str(f.get("key") or "").strip()
            v = str(f.get("value") or "").strip()
            if not k or not v:
                continue
            try:
                set_agent(k, v)
            except Exception:
                continue
    return len(organized)


def maybe_trigger_auto_memory(api_key: str | None = None) -> dict | None:
    """
    Called after conversation persisted. If 50-message boundary reached, runs:
    1. Extract user facts from last 50 (with chat)
    2. Store in user_memory
    3. Organize user_memory (without chat) - second request
    4. Same for agent_memory (extract + organize)
    Runs in background thread to not block loop.
    Returns None if not triggered, or dict with counts if triggered.
    """
    if not _should_trigger():
        return None
    conv = _load_conversation()
    n = len(conv)
    last_50 = conv[-50:]

    # Run in background thread so loop not blocked
    import threading

    def background():
        try:
            # User extract + organize
            u_stored = _extract_and_store("user", last_50)
            # Small delay
            time.sleep(1)
            u_organized = _organize_memory("user")
            # Agent extract + organize
            time.sleep(1)
            a_stored = _extract_and_store("agent", last_50)
            time.sleep(1)
            a_organized = _organize_memory("agent")
            print(f"[auto_memory] Triggered at {n} msgs: user +{u_stored}→{u_organized}, agent +{a_stored}→{a_organized}")
        except Exception as e:
            print(f"[auto_memory] failed at {n}: {e}")
        finally:
            _mark_triggered(n)

    t = threading.Thread(target=background, daemon=True, name="auto-memory")
    t.start()
    # Mark immediately to avoid double trigger before background finishes? But we mark in background finally, so use pending
    # For now, mark with n-1 to allow background to mark n
    return {"triggered_at": n, "status": "background started"}


def trigger_now_for_test(api_key: str | None = None) -> dict:
    """Synchronous version for tests: runs extract+organize immediately."""
    conv = _load_conversation()
    last_50 = conv[-50:] if len(conv) >= 50 else conv
    if not last_50:
        # Create dummy if empty for test
        last_50 = [{"role":"user","content":"اسمي أحمد وأحب بايثون وأعيش في القاهرة"}]
    # User
    u_stored = _extract_and_store("user", last_50)
    u_organized = _organize_memory("user")
    a_stored = _extract_and_store("agent", last_50)
    a_organized = _organize_memory("agent")
    # Mark
    _mark_triggered(len(conv))
    return {"user_stored": u_stored, "user_organized": u_organized, "agent_stored": a_stored, "agent_organized": a_organized}
