"""
Embedding bootstrap - automatic bundled model loader.

- Single dedicated folder: project/embedding_model/.
- First run downloads the model automatically from HuggingFace: no prompt,
  no opt-in flag, no manual copy. ~1.2GB once, with resume + progress.
- Smart check first on every run: complete folder -> ready instantly with
  zero network. Missing or incomplete -> only the gaps are fetched.
- Install/update runs sync against HuggingFace: current files are skipped,
  only deltas download (re-installs cost ~zero traffic).
- Device is detected ONCE at startup (cached): CUDA when an NVIDIA GPU is
  usable, else CPU. Reported in the status dict and startup log.
- Idempotent: a .download_complete marker + essential files means ready.
- get_status() is pollable by the UI (progress bar while preparing).
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

# HuggingFace source of the bundled embedding model (auto-fetch, first run).
# Auth is OPTIONAL: plain anonymous download works. When HF_TOKEN (env) is
# set — e.g. `HF_TOKEN=hf_xxx python install.py` on a slow/capped network —
# it is used only to raise HuggingFace rate limits. Never required, never logged.
_SOURCE_REPO = "Qwen/Qwen3-Embedding-0.6B"
MARKER = ".download_complete"
# Essential files only: weights + configs + tokenizer. Skips docs, git
# metadata, duplicate formats and optimizer states to keep it minimal.
ALLOW_PATTERNS = [
    "*.json", "merges.txt", "vocab.json", "*.safetensors", "1_Pooling/*",
]
# A folder counts as usable only with configs + tokenizer + real weights.
ESSENTIAL_FILES = ("config.json", "tokenizer.json")
MAX_DOWNLOAD_ATTEMPTS = 3

_lock = threading.Lock()
_progress: dict = {"state": "unknown", "downloaded_mb": 0.0, "device": "cpu", "detail": ""}
_DEVICE_CACHE: str | None = None


def embedding_dir() -> Path:
    """Bundled path, unless the entrypoint pinned a persistent store.

    The container entrypoint copies baked weights into the manager's bind
    (/app/data/x5store) which survives single-room rebuilds while bundled
    paths do not — so the store wins whenever it holds a usable model.
    Env override (explicit, documented): X5_EMBEDDING_DIR.
    """
    try:
        import os as _os

        forced = (_os.environ.get("X5_EMBEDDING_DIR") or "").strip()
        if forced:
            return Path(forced)
        store = Path("/app/data/x5store")
        if store.is_dir() and (store / MARKER).is_file():
            return store
    except Exception:
        pass
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


def _local_state(folder=None) -> str:
    """Classify the bundled folder: 'ready' | 'incomplete' | 'missing'. Never raises."""
    try:
        d = Path(folder) if folder else embedding_dir()
        if not d.exists():
            return "missing"
        has_weights = any(d.glob("*.safetensors"))
        has_essentials = all((d / name).is_file() for name in ESSENTIAL_FILES)
        if has_weights and has_essentials and (d / MARKER).is_file():
            return "ready"
        if has_weights or has_essentials or any(d.iterdir()):
            return "incomplete"
        return "missing"
    except Exception:
        return "missing"


def require_ready() -> None:
    """Refuse when the bundled resources are unusable (every request gates on this)."""
    if _local_state() != "ready":
        raise RuntimeError(
            "Embedding model is not ready (project/embedding_model/ incomplete). "
            "Start the console or run install.py - the model downloads "
            "automatically on first run.")


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


def ensure_embedding_model(sync: bool = False, timeout_seconds: int = 3600) -> Path:
    """Check-first loader for the bundled embedding model.

    - ready + sync=False -> return instantly, zero network (normal startup).
    - missing/incomplete, or sync=True (install/update) -> verify against
      HuggingFace and fetch ONLY missing/changed files (resume, progress,
      max 3 attempts). Re-installs cost ~zero traffic when files are current.
    """
    _ = timeout_seconds  # kept for signature compatibility; checks are fast.
    target = embedding_dir()
    if not sync and _local_state(target) == "ready":
        _set_state("ready")
        print(f"[OK] Embedding model ready. Device: {detect_device()} (auto).")
        return target
    if sync:
        print("[..] Checking embedding model (current files are skipped, only deltas download)…")
    else:
        print("=" * 70)
        print("  First run: downloading the embedding model (~1.2GB, once only)…")
        print(f"  Device: {detect_device()} (GPU used automatically when available)")
        print("  Interrupted? Just restart - the download resumes where it stopped.")
        print("=" * 70)
    _set_state("downloading", "Fetching embedding model (deltas only)…")
    last_error = None
    _use_hf_token()
    for attempt in range(1, MAX_DOWNLOAD_ATTEMPTS + 1):
        try:
            target.mkdir(parents=True, exist_ok=True)
            from huggingface_hub import snapshot_download
            # Built-in console progress bar from huggingface_hub (tqdm).
            # Files already current are skipped, so this is instant on re-runs.
            snapshot_download(repo_id=_SOURCE_REPO, local_dir=str(target),
                              local_dir_use_symlinks=False, resume_download=True,
                              max_workers=4, allow_patterns=ALLOW_PATTERNS)
            last_error = None
            break
        except Exception as e:
            last_error = e
            print(f"[WARN] Model fetch attempt {attempt}/{MAX_DOWNLOAD_ATTEMPTS} failed: "
                  f"{e} - retrying…", flush=True)
            _set_state("downloading", f"Retrying ({attempt}/{MAX_DOWNLOAD_ATTEMPTS})…")
    if last_error is not None:
        _set_state("error", str(last_error)[:200])
        print(f"[ERROR] Embedding model fetch failed: {last_error}", flush=True)
        print("  Check your connection and restart: the download resumes automatically.", flush=True)
        raise RuntimeError(f"Embedding model download failed: {last_error}") from last_error
    try:
        (target / MARKER).write_text("complete\n", encoding="utf-8")
    except Exception:
        pass
    if _local_state(target) != "ready":
        _set_state("error", "download finished but essential files are unusable")
        raise RuntimeError("Embedding model download finished but essential files are missing.")
    _set_state("ready")
    print("[OK] Embedding model ready.")
    return target


def _use_hf_token() -> None:
    """Optional HuggingFace login from HF_TOKEN env (rate limits only).

    Best-effort and silent on failure: anonymous download is the default.
    The token value is never printed or stored.
    """
    try:
        import os as _os

        token = (_os.environ.get("HF_TOKEN") or "").strip()
        if not token:
            return
        try:
            from huggingface_hub import login as _hf_login

            _hf_login(token=token, add_to_git_credential=False)
            print("[..] HuggingFace: authenticated download (HF_TOKEN set).")
        except Exception as e:
            print(f"[WARN] HuggingFace login failed ({str(e)[:100]}) - "
                  "continuing anonymously.")
    except Exception:
        pass


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
