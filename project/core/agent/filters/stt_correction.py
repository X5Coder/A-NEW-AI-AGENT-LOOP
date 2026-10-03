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
    Transcribe audio file via faster_whisper (whisper-medium-arabic-codeswitched).
    Validates path, bounded size, returns raw text.
    """
    from models.loader import get_model_path
    p = Path(audio_path)
    if not p.exists():
        # try relative to project
        proj = Path(__file__).resolve().parents[3]
        alt = proj / str(audio_path)
        if alt.exists():
            p = alt
        else:
            raise FileNotFoundError(f"Audio not found: {audio_path}")
    if p.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("Audio too large (max 20MB)")
    if p.suffix.lower() not in (".wav", ".mp3", ".m4a", ".ogg", ".flac", ".webm"):
        # allow wav mp3 etc
        pass
    model_path = get_model_path("stt")
    if model_path is None or not model_path.exists():
        raise FileNotFoundError("STT model not found")
    from faster_whisper import WhisperModel
    model = WhisperModel(str(model_path), device="cpu", compute_type="int8", cpu_threads=2)
    # language auto if None
    segments, info = model.transcribe(str(p), language=language, beam_size=5)
    text = " ".join([s.text for s in segments]).strip()
    return text


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
    api_key_val = api_key or os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_KEY")
    if not api_key_val:
        return minimal_clean(raw_text)

    try:
        from core.lm.providers.openrouter.provider import chat as provider_chat
        resp = provider_chat(
            messages=[{"role":"system","content": system_prompt},{"role":"user","content": raw_text}],
            model=None,
            api_key=api_key_val,
            temperature=0.1,
            max_tokens=800,
        )
        content = resp.get("content")
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
