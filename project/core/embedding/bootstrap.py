"""
Embedding bootstrap - offline-first bundled model loader.

- Single dedicated folder: project/embedding_model/ (weights + tokenizer
  ship WITH the project copy; normal runs NEVER touch the network).
- Device is detected ONCE at startup (cached): CUDA when an NVIDIA GPU is
  usable, else CPU. Reported in the status dict and startup log.
- Idempotent: a .download_complete marker means ready, never re-checks.
- get_status() is pollable by the UI (progress bar while preparing).
- First-install download is OPT-IN only (scripts/build_embedding_seed.py
  --download or X5_ALLOW_EMBEDDING_DOWNLOAD=1); normal startup refuses
  clearly with copy instructions when the folder is missing.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

# Internal source used ONLY by the opt-in first-install download.
# Never shown in user-visible strings (UI/logs use "embedding model" only).
_SOURCE_REPO = "Qwen/Qwen3-Embedding-0.6B"
MARKER = ".download_complete"
# Essential files only: weights + configs + tokenizer. Skips docs, git
# metadata, duplicate formats and optimizer states to keep it minimal.
ALLOW_PATTERNS = [
    "*.json", "merges.txt", "vocab.json", "*.safetensors", "1_Pooling/*",
]

_lock = threading.Lock()
_progress: dict = {"state": "unknown", "downloaded_mb": 0.0, "device": "cpu", "detail": ""}
_DEVICE_CACHE: str | None = None


def embedding_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "embedding_model"


def detect_device() -> str:
    """Cached device: checked once, then reused. cuda when usable, else cpu."""
    global _DEVICE_CACHE
    if _DEVICE_CACHE in ("cuda", "cpu"):
        return _DEVICE_CACHE
    device = "cpu"
    try:
        import torch
        if torch.cuda.is_available():
            device = "cuda"
    except Exception:
        device = "cpu"
    _DEVICE_CACHE = device
    try:
        with _lock:
            _progress["device"] = device
    except Exception:
        pass
    return device


def is_complete() -> bool:
    d = embedding_dir()
    try:
        return d.exists() and (d / MARKER).exists() and any(d.iterdir())
    except Exception:
        return False


def require_ready() -> None:
    """Refuse when the bundled resources are missing (every request gates on this)."""
    if not is_complete():
        raise RuntimeError(
            "Embedding model folder is missing (project/embedding_model/). "
            "Copy it next to the project (it ships with the release zip) and restart - "
            "no download happens automatically.")


def get_status() -> dict:
    """Pollable install status for the UI progress bar. Never raises."""
    try:
        with _lock:
            snap = dict(_progress)
        if is_complete():
            snap["state"] = "ready"
        elif snap.get("state") in (None, "unknown"):
            snap["state"] = "missing"
        return snap
    except Exception:
        return {"state": "unknown", "downloaded_mb": 0.0, "device": "cpu", "detail": ""}


def _set_state(state: str, detail: str = "") -> None:
    try:
        with _lock:
            _progress["state"] = state
            _progress["device"] = detect_device()
            if detail:
                _progress["detail"] = detail[:200]
    except Exception:
        pass


def ensure_embedding_model(timeout_seconds: int = 3600) -> Path:
    """Use the bundled embedding model (offline-first, never downloads by default).

    Device (GPU/CPU) is detected once and cached. When the folder is missing,
    fails clearly with copy instructions; download happens ONLY when the
    opt-in flag is set (env X5_ALLOW_EMBEDDING_DOWNLOAD=1, used by the
    first-install script).
    """
    _ = timeout_seconds  # kept for signature compatibility; offline check is instant.
    target = embedding_dir()
    if is_complete():
        _set_state("ready")
        print(f"[OK] Embedding model ready. Device: {detect_device()} (auto).")
        return target
    if os.environ.get("X5_ALLOW_EMBEDDING_DOWNLOAD") != "1":
        _set_state("error", "bundled embedding model folder is missing")
        raise RuntimeError(
            "Embedding model folder is missing (project/embedding_model/). "
            "Copy it next to the project (it ships with the release zip) and restart - "
            "no download happens automatically.")
    _set_state("downloading", "Downloading resources…")
    print("=" * 70)
    print("  Downloading resources… (bundled embedding model, first install only)")
    print(f"  Device: {detect_device()} (GPU used automatically when available)")
    print("=" * 70)
    try:
        target.mkdir(parents=True, exist_ok=True)
        from huggingface_hub import snapshot_download
        # Built-in console progress bar from huggingface_hub (tqdm).
        snapshot_download(repo_id=_SOURCE_REPO, local_dir=str(target),
                          local_dir_use_symlinks=False, resume_download=True,
                          max_workers=4, allow_patterns=ALLOW_PATTERNS)
        (target / MARKER).write_text("complete\n", encoding="utf-8")
    except Exception as e:
        _set_state("error", str(e)[:200])
        print(f"[ERROR] Resource download failed: {e}", flush=True)
        print("  Check your connection and restart: the download resumes automatically.", flush=True)
        raise RuntimeError(f"Embedding model download failed: {e}") from e
    if not is_complete():
        _set_state("error", "download finished but files are missing")
        raise RuntimeError("Embedding model download finished but files are missing.")
    _set_state("ready")
    print("[OK] Resources ready.")
    return target


def ensure_in_background() -> threading.Thread:
    """Start the mandatory download without blocking (status via get_status())."""
    t = threading.Thread(target=ensure_embedding_model, name="embedding-bootstrap", daemon=True)
    t.start()
    return t


def wait_until_ready(timeout_seconds: int = 3600, poll: float = 1.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout_seconds:
        if is_complete():
            return True
        time.sleep(poll)
    return is_complete()
