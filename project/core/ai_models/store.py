"""
Storage for universal model records (one per kind: text/vision/image/tts/stt).

Record: { model_name, server_url, api_key, config_json, kind }.
Writes ALWAYS to X5Agent/Data (database/ai_models.json). Reads Data-first,
legacy fallback. Each API key is mirrored into X5Agent/Data/.env
(AI_<KIND>_API_KEY, same mechanism the old provider vault used) and is never
returned to the UI (masked flag only).

Saving requires a successful test in the same session unless the caller
passes force=True explicitly (override). The "tested" mark lives only in
memory (per process) - restarting the server requires a fresh Test.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FILENAME = "ai_models.json"

KINDS = ("text", "vision", "image", "tts", "stt")

KIND_TITLES = {
    "text": "AI Text Model",
    "vision": "AI Vision Model",
    "image": "AI Image Model",
    "tts": "AI Text to Speech",
    "stt": "AI Speech to Text",
}

ENV_NAMES = {
    "text": "AI_TEXT_API_KEY",
    "vision": "AI_VISION_API_KEY",
    "image": "AI_IMAGE_API_KEY",
    "tts": "AI_TTS_API_KEY",
    "stt": "AI_STT_API_KEY",
}

MODEL_LINKS = {
    "text": "AI Models → AI Text Model (/ai-models)",
    "vision": "AI Models → AI Vision Model (/ai-models)",
    "image": "AI Models → AI Image Model (/ai-models)",
    "tts": "AI Models → AI Text to Speech (/ai-models)",
    "stt": "AI Models → AI Speech to Text (/ai-models)",
}

_tested_ok: set[str] = set()  # session marks, one per kind


def _data_path() -> Path:
    try:
        from core.paths import data_db_file
        return data_db_file(FILENAME)
    except Exception:
        return Path(__file__).resolve().parents[2] / "storage" / "database" / FILENAME


def _legacy_path() -> Path:
    return Path(__file__).resolve().parents[2] / "storage" / "database" / FILENAME


def _read_all() -> dict:
    for cand in (_data_path(), _legacy_path()):
        try:
            if cand.exists():
                data = json.loads(cand.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            continue
    return {}


def _write_all(data: dict) -> None:
    p = _data_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def _write_env_key(env_name: str, value: str) -> None:
    import os as _os
    value = (value or "").strip().splitlines()[0][:512] if (value or "").strip() else ""
    if value:
        try:
            _os.environ[env_name] = value
        except Exception:
            pass
    else:
        try:
            _os.environ.pop(env_name, None)
        except Exception:
            pass
    try:
        from core.paths import data_root, system_core_root
        try:
            env_path = data_root() / ".env"
        except Exception:
            env_path = system_core_root().parents[0] / "X5Agent" / "Data" / ".env"
        env_path.parent.mkdir(parents=True, exist_ok=True)
        lines = env_path.read_text(encoding="utf-8-sig").splitlines() if env_path.exists() else []
        out: list[str] = []
        found = False
        for line in lines:
            s = line.strip()
            if s and not s.startswith("#") and "=" in s and s.split("=", 1)[0].strip() == env_name:
                if value:
                    out.append(f"{env_name}={value}")
                    found = True
                continue
            out.append(line)
        if value and not found:
            out.append(f"{env_name}={value}")
        env_path.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
    except Exception:
        pass


def _check_kind(kind: str) -> str:
    if kind not in KINDS:
        raise ValueError(f"Unknown model kind '{kind}'.")
    return kind


def mark_tested(kind: str = "text") -> None:
    _tested_ok.add(_check_kind(kind))


def was_tested(kind: str = "text") -> bool:
    return _check_kind(kind) in _tested_ok


def get_model(kind: str = "text") -> dict | None:
    """Full record INCLUDING the key (server-side only). None when not configured."""
    _check_kind(kind)
    data = _read_all()
    rec = data.get(kind)
    if not isinstance(rec, dict):
        return None
    if not str(rec.get("server_url") or "").strip():
        return None
    parser = rec.get("parser_definition") if isinstance(rec.get("parser_definition"), dict) else None
    legacy = rec.get("config_json") if isinstance(rec.get("config_json"), dict) else None
    cfg = parser or legacy
    if not isinstance(cfg, dict):
        return None
    if cfg.get("kind") != kind:
        # Heal old records saved before kind was stamped: the engine reads
        # kind from inside the config, so a missing one fails every call.
        # Copy-on-read: stored data is never mutated here.
        cfg = dict(cfg)
        cfg["kind"] = kind
    return {
        "model_name": str(rec.get("model_name") or ""),
        "server_url": str(rec.get("server_url") or ""),
        "api_url": str(rec.get("api_url") or ""),
        "api_key": str(rec.get("api_key") or ""),
        "config_json": cfg,
        "parser_definition": cfg,
        "kind": kind,
    }


def has_model(kind: str = "text") -> bool:
    try:
        return get_model(kind) is not None
    except Exception:
        return False


def get_text_model() -> dict | None:
    return get_model("text")


def has_text_model() -> bool:
    return has_model("text")


def missing_message(kind: str = "text") -> str:
    """Fixed refusal with the link to the needed model (tools return this)."""
    _check_kind(kind)
    return (f"No {KIND_TITLES[kind]} connected. Open {MODEL_LINKS[kind]}, "
            f"press Test, then Save.")


def kind_from_missing_error(err: str | None) -> str | None:
    """Return the model kind when err is a missing-model refusal, else None.

    Single owner for the detection so the loop, gateway, and server share
    one mapping instead of scattering string checks.
    """
    text = str(err or "")
    if "No AI " not in text or "connected" not in text:
        return None
    low = text.lower()
    if "ai text model" in low:
        return "text"
    if "ai vision model" in low:
        return "vision"
    if "ai image model" in low:
        return "image"
    if "ai text to speech" in low:
        return "tts"
    if "ai speech to text" in low:
        return "stt"
    return None


def require_model(kind: str = "text") -> dict:
    """Return the record or raise the fixed user-facing message (tool gating)."""
    rec = get_model(kind)
    if rec is None:
        raise RuntimeError(missing_message(kind))
    return rec


def require_text_model() -> dict:
    return require_model("text")


def public_model(kind: str = "text") -> dict:
    """UI-safe view: never includes the key (masked flag only)."""
    _check_kind(kind)
    rec = get_model(kind)
    if rec is None:
        return {"configured": False, "model_name": "", "server_url": "", "api_url": "",
                "has_key": False, "key_hint": "", "kind": kind}
    key = rec.get("api_key") or ""
    hint = ("••••" + key[-4:]) if len(key) > 4 else ("••••" if key else "")
    cfg = rec.get("parser_definition") if isinstance(rec.get("parser_definition"), dict) else (rec.get("config_json") or {})
    return {"configured": True, "model_name": rec.get("model_name", ""),
            "server_url": rec.get("server_url", ""), "api_url": str(rec.get("api_url") or ""),
            "has_key": bool(key.strip()),
            "key_hint": hint, "kind": kind,
            "config_json": cfg, "parser_definition": cfg}


def public_text_model() -> dict:
    return public_model("text")


def save_model(kind: str, model_name: str, server_url: str, api_key: str,
               config_json: Any, force: bool = False, api_url: str = "") -> dict:
    """Persist the record. Requires a Test pass in this session unless force=True."""
    _check_kind(kind)
    if not isinstance(server_url, str) or not server_url.strip():
        raise ValueError("Server URL is required.")
    if len(server_url) > 2000:
        raise ValueError("Server URL too long.")
    api_url = str(api_url or "").strip()[:2000]
    if api_url.lower().startswith(("javascript:", "file:", "data:", "ftp:")):
        raise ValueError("API URL must be an http(s) path.")
    if not isinstance(config_json, dict):
        raise ValueError("Connection JSON must be an object.")
    # AIP/1 parser (language/version) or legacy connection JSON (kind/version).
    if config_json.get("language") == "AIP/1":
        if config_json.get("version") != 1:
            raise ValueError("Unsupported AIP version (supported version is 1).")
    else:
        if config_json.get("kind") != kind:
            raise ValueError(f"This section configures {KIND_TITLES[kind]} only (kind must be '{kind}').")
        if config_json.get("version") != 1:
            raise ValueError("Unsupported version (supported version is 1).")
    if not force and not was_tested(kind):
        raise PermissionError("Press Test first — saving needs one successful test in this session (or tick Override).")
    data = _read_all()
    data[kind] = {
        "model_name": str(model_name or "")[:256],
        "server_url": server_url.strip()[:2000],
        "api_url": api_url,
        "api_key": str(api_key or "")[:512],
        "config_json": config_json,
        "parser_definition": config_json,
        "kind": kind,
    }
    _write_all(data)
    _write_env_key(ENV_NAMES[kind], str(api_key or ""))
    return public_model(kind)


def save_text_model(model_name: str, server_url: str, api_key: str,
                    config_json: Any, force: bool = False) -> dict:
    return save_model("text", model_name, server_url, api_key, config_json, force=force)


def clear_model(kind: str = "text") -> None:
    _check_kind(kind)
    data = _read_all()
    data.pop(kind, None)
    try:
        _write_all(data)
    except Exception:
        pass
    _write_env_key(ENV_NAMES[kind], "")
    _tested_ok.discard(kind)


def clear_text_model() -> None:
    clear_model("text")
