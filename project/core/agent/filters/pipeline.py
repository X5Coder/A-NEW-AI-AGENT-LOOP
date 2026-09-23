"""
Unified Filter Pipeline: audio -> STT correction -> triage -> main loop.
Saves tokens for simple greetings, handles audio, and routes tasks to full agent loop.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .stt_correction import process_audio_record, correct_text
from .triage import triage_user_message


def handle_user_input(
    user_input: str | None = None,
    audio_path: str | Path | None = None,
    audio_bytes: bytes | None = None,
    language: str | None = "ar",
    model: str | None = None,
    api_key: str | None = None,
    max_steps: int = 500,
    verbose: bool = True,
) -> dict[str, Any]:
    """
    Pipeline:
    1. If audio provided -> transcribe via STT + correction layer (stt_correction_system.md)
    2. Triage layer (triage_system.md) with one tool route_to_main_workflow
       - If simple -> return direct answer (saves tokens, no full context)
       - If task/uncertain -> route to main loop with full context (500 steps)
    Returns dict with action, content, and optionally loop result.
    """
    # Step 1: Audio handling
    text_input = user_input or ""
    if audio_path or audio_bytes is not None:
        if audio_bytes is not None:
            from .stt_correction import process_audio_bytes
            stt_res = process_audio_bytes(audio_bytes, language=language, api_key=api_key)
        else:
            stt_res = process_audio_record(audio_path, language=language, api_key=api_key)
        raw = stt_res.get("raw_text","")
        clean = stt_res.get("clean_text","") or raw
        # Use clean text as user_input for next stage
        text_input = clean
        if verbose:
            print(f"[pipeline] STT raw: {raw[:100]} -> clean: {clean[:100]}")

    if not text_input or not text_input.strip():
        return {"action":"error","error":"No input provided"}

    # Step 2: Triage (saves tokens)
    triage = triage_user_message(text_input, api_key=api_key, model=model)
    if triage.get("action") == "answer":
        # Simple greeting -> direct answer, no main workflow, no full context
        if verbose:
            print(f"[pipeline] Triage: simple answer, saved tokens")
        return {"action":"answer","content": triage["content"], "triage": triage, "saved_tokens": True}

    # Step 3: Route to main workflow with full context
    if verbose:
        print(f"[pipeline] Triage: route to main workflow (task)")
    from core.agent.loop.engine import run_loop
    # Build full context via main loop (it will call builder with system.md + memories + last30 + env + todo + core tools)
    # For scheduled skills isolation, the scheduler engine will call run_loop with isolated=True directly, bypassing triage
    loop_result = run_loop(
        user_input=text_input,
        max_steps=max_steps,
        model=model,
        api_key=api_key,
        verbose=verbose,
    )
    return {"action":"route","loop_result": loop_result, "triage": triage, "input": text_input}
