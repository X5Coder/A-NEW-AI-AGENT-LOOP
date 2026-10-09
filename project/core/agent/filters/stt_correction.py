"""
STT Filter Layer - audio -> text -> correction.
Ownership: core/agent/filters, lifecycle: transcribe -> correct -> return clean text.
"""
from __future__ import annotations

import os
import wave
import tempfile
from pathlib import Path
from typing import Any

CORRECTION_PROMPT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "stt_correction_system.md"


def _read_correction_prompt() -> str:
    if CORRECTION_PROMPT_PATH.exists():
        try:
            return CORRECTION_PROMPT_PATH.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return "You are a text correction filter. Do NOT invent. Only correct linguistic errors and remove filler. Output clean text only."


def transcribe_audio(audio_path: str | Path, language: str | None = "ar") -> str:
    """
    Transcribe audio file via the connected AI Speech to Text model.
    Refuses with a fixed message (+ model link) when none is connected.
    (language is accepted for compatibility; the service's language, if any,
    is fixed inside its Connection JSON.)
    """
    from core.ai_models.media import read_audio
    from core.ai_models.store import require_model
    from core.ai_models.engine import transcribe
    record = require_model("stt")  # fixed refusal + model link when missing
    blob, mime = read_audio(audio_path)
    return transcribe(record, {"audio_bytes": blob, "mime": mime})


def correct_text(raw_text: str, api_key: str | None = None) -> str:
    """
    Correction layer: LLM with stt_correction_system.md, strict - no invention.
    Returns clean text. If api_key missing or LLM fails, returns raw_text cleaned minimally.
    """
    if not isinstance(raw_text, str):
        raise TypeError("raw_text must be string")
    if not raw_text.strip():
        return ""
    # Minimal fallback cleaning without LLM (remove filler)
    def minimal_clean(t: str) -> str:
        import re
        t = re.sub(r"\b(امم|مم|اه|اااه|umm|uh)\b", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s+", " ", t).strip()
        return t

    system_prompt = _read_correction_prompt()
    try:
        from core.ai_models.store import get_text_model
        from core.ai_models.engine import chat as universal_chat
        _record = get_text_model()
        if _record is None:
            return minimal_clean(raw_text)
        content = universal_chat(
            _record,
            [{"role": "user", "content": raw_text}],
            {"system": system_prompt},
        )
        if isinstance(content, str) and content.strip():
            # Ensure not inventing too much: if output is much longer than input, fallback
            if len(content) > len(raw_text) * 2 + 100:
                return minimal_clean(raw_text)
            return content.strip()
    except Exception:
        pass
    return minimal_clean(raw_text)


def process_audio_record(audio_path: str | Path, language: str | None = "ar", api_key: str | None = None) -> dict[str, Any]:
    """
    Full STT filter pipeline: transcribe -> correct.
    Returns {raw_text, clean_text, success}
    """
    raw = transcribe_audio(audio_path, language=language)
    clean = correct_text(raw, api_key=api_key)
    return {"raw_text": raw, "clean_text": clean, "success": True}


def process_audio_bytes(audio_bytes: bytes, suffix: str = ".wav", language: str | None = "ar", api_key: str | None = None) -> dict[str, Any]:
    """Helper for gateway: receives bytes, writes temp file, processes."""
    if not isinstance(audio_bytes, (bytes, bytearray)):
        raise TypeError("audio_bytes must be bytes")
    if len(audio_bytes) > 20 * 1024 * 1024:
        raise ValueError("audio_bytes too large")
    import tempfile, os
    fd, tmp = tempfile.mkstemp(suffix=suffix)
    try:
        os.write(fd, audio_bytes)
        os.close(fd)
        return process_audio_record(tmp, language=language, api_key=api_key)
    finally:
        try:
            Path(tmp).unlink(missing_ok=True)
        except Exception:
            pass
