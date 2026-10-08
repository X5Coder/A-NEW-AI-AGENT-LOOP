"""Tests for the fixed project TTS voice (custom "nova" identity).

Runs against project/models/tts with the real model only for the synthesis
path (skipped when weights or ffmpeg are missing). Validation, blend, and
filter-chain construction are pure logic and always run. Style mirrors
existing tests: plain script with asserts, cleanup after itself, no external
state.
"""
import shutil
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from models.tts import service as tts_service
from models.tts.voices import (build_style, clear_style_cache, custom_voice_names,
                               default_voice_name, get_custom_voice, is_custom_voice)
from models.tts.postprocess import ATTEMPTS, build_filter

TAG_AUDIO = "tts_test_tmp"


def _audio_dir() -> Path:
    try:
        from core.paths import data_subdir as _ds
        d = _ds("files", "audio")
    except Exception:
        d = project_root.parent / "X5Agent" / "Data" / "files" / "audio"
    return d


def _cleanup():
    d = _audio_dir()
    for name in (TAG_AUDIO,):
        for p in d.glob(f"{name}*"):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass


try:
    _cleanup()
    # 1. custom voice catalog is sane
    assert custom_voice_names() == ("nova",), custom_voice_names()
    assert default_voice_name() == "nova"
    assert is_custom_voice("nova") and is_custom_voice("NOVA")
    assert not is_custom_voice("M1") and not is_custom_voice("")
    cfg = get_custom_voice("nova")
    assert abs(sum(float(w) for w in cfg["blend"].values()) - 1.0) < 1e-6, cfg["blend"]
    assert 0.5 <= float(cfg["profile"]["pitch"]) <= 2.0
    print("custom voice catalog OK")

    # 2. default resolves to nova; unknown voices rejected before any model load
    t, v, lg = tts_service._validate("hello", None, "ar")
    assert v == "nova", (t, v, lg)
    try:
        tts_service._validate("hello", "jarvis", "ar")
        raise AssertionError("unknown voice accepted")
    except ValueError:
        pass
    try:
        tts_service._validate("   ", "nova", "ar")
        raise AssertionError("empty text accepted")
    except ValueError:
        pass
    print("validation (default/reject/empty) OK")

    # 3. filter chain builds from the nova profile
    filt = build_filter(cfg["profile"])
    for token in ("rubberband=pitch=0.9200", "aecho=", "acompressor=", "loudnorm="):
        assert token in filt, filt
    try:
        build_filter({"pitch": 9.0})
        raise AssertionError("bad pitch accepted")
    except ValueError:
        pass
    assert ATTEMPTS >= 1
    print("voice profile filter OK")

    # 4. light model checks are skipped without weights/ffmpeg
    if not tts_service.is_ready() or shutil.which("ffmpeg") is None:
        print("SKIP synthesis checks: tts weights or ffmpeg missing")
    else:
        tts = tts_service._load_tts()
        clear_style_cache()
        style = build_style(tts, "nova")
        assert style.ttl.shape == (1, 50, 256) and style.dp.shape == (1, 8, 16), (
            style.ttl.shape, style.dp.shape)
        assert build_style(tts, "nova") is style
        out = _audio_dir() / f"{TAG_AUDIO}.wav"
        res = tts_service.synthesize("Test", voice="nova", lang="en", out_path=out)
        assert out.is_file() and int(res.get("size_bytes", 0)) > 0, res
        assert res.get("voice") == "nova" and float(res.get("duration_sec", 0)) > 0, res
        from models.tts.postprocess import apply_voice_profile  # noqa
        shaped = _audio_dir() / f"{TAG_AUDIO}_shape_test.wav"
        try:
            shaped.unlink(missing_ok=True)
        except Exception:
            pass
        print("synthesis + shaping ran with model OK")
    print("ALL TTS VOICE TESTS PASSED")
finally:
    try:
        _cleanup()
    except Exception:
        pass
