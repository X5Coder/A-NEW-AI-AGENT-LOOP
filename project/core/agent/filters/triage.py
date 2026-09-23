"""
Triage Filter Layer - analyzes user message before main workflow to save tokens.
If simple chit-chat → answer directly (no tools, no full context).
If task/uncertain → call route_to_main_workflow tool which escalates to main agent loop with full context.
Ownership: core/agent/filters, lifecycle: per user message.
"""
from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Any

TRIAGE_PROMPT_PATH = Path(__file__).resolve().parents[2] / "agent" / "prompts" / "triage_system.md"


def _read_triage_prompt() -> str:
    if TRIAGE_PROMPT_PATH.exists():
        try:
            return TRIAGE_PROMPT_PATH.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return "You are a strict triage layer. If simple greeting, answer directly. Else call route_to_main_workflow."


def _get_api_key(explicit: str | None = None) -> str | None:
    if explicit and explicit.strip():
        return explicit.strip()
    for k in ["OPENROUTER_API_KEY","OPENROUTER_KEY","OR_API_KEY"]:
        v = os.environ.get(k)
        if v and v.strip():
            return v.strip()
    return None


def triage_user_message(user_input: str, api_key: str | None = None, model: str | None = None) -> dict[str, Any]:
    """
    Returns:
      {"action": "answer", "content": "أهلا..."}  -> simple, no main workflow needed
      {"action": "route", "tool_call_id": "...", "content": "escalated"} -> needs main workflow with full context
    """
    if not isinstance(user_input, str) or not user_input.strip():
        raise ValueError("user_input must be non-empty string")
    if len(user_input) > 2000:
        raise ValueError("user_input too long")

    system_prompt = _read_triage_prompt()
    api_key_val = _get_api_key(api_key)
    if not api_key_val:
        # No key -> fallback: heuristic - if short greeting, answer, else route
        low = user_input.strip().lower()
        greetings = {"اهلا","أهلا","مرحبا","هلا","hi","hello","hey","سلام","السلام عليكم","كيف حالك","شكرا","thanks"}
        if low in greetings or (len(low) < 15 and any(g in low for g in greetings)):
            return {"action":"answer","content": "أهلا! كيف أساعدك اليوم؟"}
        return {"action":"route","reason":"heuristic task"}

    # One tool definition
    tools = [
        {
            "type":"function",
            "function":{
                "name":"route_to_main_workflow",
                "description":"Route the user's message to the main agent workflow with full context and core tools. Use for any task needing tools, files, or complex reasoning, or when uncertain.",
                "parameters":{
                    "type":"object",
                    "properties":{
                        "reason": {"type":"string","description":"Why this needs main workflow"}
                    },
                    "required":[]
                }
            }
        }
    ]

    try:
        from core.lm.providers.openrouter.provider import chat as provider_chat
        resp = provider_chat(
            messages=[{"role":"system","content": system_prompt},{"role":"user","content": user_input.strip()}],
            tools=tools,
            model=model,
            api_key=api_key_val,
            temperature=0.1,
            max_tokens=400,
        )
        tool_calls = resp.get("tool_calls") or []
        content = resp.get("content")
        # If tool called -> route
        for tc in tool_calls:
            if tc.get("function",{}).get("name") == "route_to_main_workflow":
                return {"action":"route","tool_call_id": tc.get("id"), "reason": tc.get("function",{}).get("arguments",{}).get("reason",""), "raw": resp}
        # No tool call -> direct answer
        if isinstance(content, str) and content.strip():
            return {"action":"answer","content": content.strip(), "raw": resp}
        # Fallback if empty
        return {"action":"route","reason":"empty triage response"}
    except Exception as e:
        # On provider failure, default to routing (safer than dropping task)
        return {"action":"route","reason": f"triage provider failed: {str(e)[:100]}", "error": str(e)}
