"""
text_to_speech - speak text aloud with the fixed project voice in X5Agent/Data/files/audio.
The voice is always the same custom "nova" identity (blended from the model's
own preset styles + a deepening voice profile), so Arabic and English replies
sound like one consistent speaker. The wav plays as a WhatsApp-style voice
record inside the chat (served by /api/audio, rendered as a bubble).
Delegates synthesis to models/tts/service.py (lazy load, 3 retries).
"""
from __future__ import annotations

import uuid
from pathlib import Path

from .events import format_event

MAX_AUDIO_BYTES = 8 * 1024 * 1024


def _audio_dir() -> Path:
    try:
        import sys as _sys
        proj = Path(__file__).resolve().parents[3]
        if str(proj) not in _sys.path:
            _sys.path.insert(0, str(proj))
        from core.paths import data_subdir as _ds
        d = _ds("files", "audio")
    except Exception:
        d = Path(__file__).resolve().parents[4] / "X5Agent" / "Data" / "files" / "audio"
    d.mkdir(parents=True, exist_ok=True)
    return d.resolve()


def execute(text: str, voice: str | None = None, lang: str | None = None) -> dict:
    event = format_event(text=text if isinstance(text, str) else "")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be non-empty string")
    # The project voice is fixed: any request resolves to the same custom
    # identity so the speaker never changes. Explicit presets are rejected
    # so callers cannot switch voices mid-conversation.
    from models.tts.voices import default_voice_name, is_custom_voice
    v = default_voice_name()
    if isinstance(voice, str) and voice.strip() and is_custom_voice(voice.strip()):
        v = voice.strip().lower()
    lg = lang.strip().lower() if isinstance(lang, str) and lang.strip() else "ar"

    from models.tts.service import synthesize
    out = _audio_dir() / f"tts_{uuid.uuid4().hex[:8]}.wav"
    try:
        result = synthesize(text=text, voice=v, lang=lg, out_path=out)
    except (ValueError, TypeError, FileNotFoundError):
        raise
    except Exception as e:
        raise RuntimeError(f"Speech synthesis failed: {e}") from None

    size = int(result.get("size_bytes", 0))
    if size > MAX_AUDIO_BYTES:
        try:
            out.unlink(missing_ok=True)
        except Exception:
            pass
        raise ValueError("Spoken audio too large, send shorter text")
    return {
        "event": event,
        "audio_path": out.name,
        "duration_sec": result.get("duration_sec", 0),
        "size_bytes": size,
        "voice": result.get("voice", v),
        "lang": result.get("lang", lg),
        "hint": f"Voice record saved as {out.name}. It plays inside the chat.",
    }