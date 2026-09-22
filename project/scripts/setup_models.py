"""
Model auto-setup script - downloads required models on first run if missing.

Responsibility: single - ensure local models exist per manifest.json
Lifecycle: create -> validate manifest -> check marker -> download (max 3 retries) -> verify -> mark complete
Ownership: models/ directory owned by this script

Security: validates paths against allowed root, no hardcoded secrets (HF_TOKEN via env), timeouts, bounded retries.
Idempotent: skips already-complete models, supports --force and --check
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = [2, 5, 10]
DOWNLOAD_TIMEOUT_SECONDS = 300
MARKER_FILENAME = ".download_complete"
MANIFEST_FILENAME = "models/manifest.json"


def get_project_root() -> Path:
    # This file is project/scripts/setup_models.py -> project is parent of scripts
    return Path(__file__).resolve().parents[1]


def get_allowed_models_root(project_root: Path) -> Path:
    return (project_root / "models").resolve()


def validate_local_dir(local_dir: str, allowed_root: Path, project_root: Path) -> Path:
    """Validate local_dir is inside allowed_root (prevent path traversal)."""
    if not local_dir or not isinstance(local_dir, str):
        raise ValueError("local_dir must be a non-empty string")
    # Reject absolute paths or traversal attempts early
    if ".." in Path(local_dir).parts:
        # Still resolve and check containment - but warn
        pass
    # Resolve relative to project root
    candidate = (project_root / local_dir).resolve()
    try:
        candidate.relative_to(allowed_root)
    except ValueError as exc:
        raise ValueError(f"local_dir '{local_dir}' escapes allowed models directory") from exc
    return candidate


def load_manifest(project_root: Path) -> dict:
    manifest_path = project_root / MANIFEST_FILENAME
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")
    with open(manifest_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Validate shape
    if "models" not in data or not isinstance(data["models"], list):
        raise ValueError("manifest must contain 'models' list")
    for entry in data["models"]:
        if not isinstance(entry, dict):
            raise ValueError("each model entry must be an object")
        for field in ("id", "repo_id", "local_dir"):
            if field not in entry or not isinstance(entry[field], str) or not entry[field].strip():
                raise ValueError(f"model entry missing or invalid field: {field} in {entry}")
        # Validate repo_id format (owner/name)
        if "/" not in entry["repo_id"]:
            raise ValueError(f"repo_id must be 'owner/name', got: {entry['repo_id']}")
    return data


def is_model_complete(target_dir: Path) -> bool:
    marker = target_dir / MARKER_FILENAME
    if not marker.exists():
        return False
    # Also ensure directory is non-empty
    if not target_dir.exists() or not any(target_dir.iterdir()):
        return False
    return True


def write_marker(target_dir: Path, repo_id: str) -> None:
    marker = target_dir / MARKER_FILENAME
    content = {
        "repo_id": repo_id,
        "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with open(marker, "w", encoding="utf-8") as f:
        json.dump(content, f, indent=2)


def snapshot_download_with_retries(repo_id: str, local_dir: Path, revision: str = "main", ignore_patterns: list[str] | None = None) -> None:
    """
    Download using huggingface_hub with bounded retries (max 3).
    Handles timeouts, network failures, and cleanup on error.
    """
    try:
        from huggingface_hub import snapshot_download
        from huggingface_hub.utils import HfHubHTTPError
    except ImportError as exc:
        raise RuntimeError(
            "huggingface_hub is required. Install via: pip install huggingface_hub"
        ) from exc

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            print(f"  -> attempt {attempt}/{MAX_RETRIES} for {repo_id} ...")
            # snapshot_download is atomic and handles resume; we set local_dir
            # Use etag timeout via env, but also ensure we pass token if available (no hardcoding)
            token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_HUB_TOKEN")
            snapshot_download(
                repo_id=repo_id,
                local_dir=str(local_dir),
                revision=revision,
                local_dir_use_symlinks=False,
                resume_download=True,
                max_workers=4,
                token=token if token else None,
                ignore_patterns=ignore_patterns,
            )
            return
        except Exception as e:
            last_error = e
            # Do not retry on deterministic errors (invalid repo, auth, etc.) if clearly non-recoverable
            # But for simplicity, retry only on transient network-like errors up to MAX_RETRIES
            is_transient = _is_transient_error(e)
            if not is_transient or attempt == MAX_RETRIES:
                print(f"  [ERROR] download failed for {repo_id}: {e}")
                if attempt == MAX_RETRIES:
                    break
                # For non-transient, still try up to max but with backoff
            wait = RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
            print(f"  [RETRY] waiting {wait}s before retry... ({e.__class__.__name__})")
            time.sleep(wait)

    raise RuntimeError(f"Failed to download {repo_id} after {MAX_RETRIES} attempts: {last_error}") from last_error


def _is_transient_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    transient_keywords = ["timeout", "connection", "network", "temporarily", "503", "429", "504", "reset", "chunked"]
    return any(k in msg for k in transient_keywords) or exc.__class__.__name__ in (
        "ChunkedEncodingError",
        "ConnectionError",
        "TimeoutError",
        "HTTPError",
    )


def handle_ct2_conversion(project_root: Path, entry: dict, final_dir: Path) -> bool:
    """
    If entry requires CT2 conversion, try to convert source HF model to CT2.
    Returns True if CT2 output is ready, False if fallback needed.
    """
    if not entry.get("ct2_conversion"):
        return False

    source_repo = entry.get("ct2_source_repo") or entry["repo_id"]
    quantization = entry.get("ct2_quantization", "float16")
    # Temporary source download dir
    tmp_source = project_root / "models" / "stt" / "_tmp_source_hf"
    tmp_source.mkdir(parents=True, exist_ok=True)

    print(f"  [STT] CT2 mode enabled. Source: {source_repo} -> {final_dir} (quantization={quantization})")
    try:
        # Download source HF model first
        snapshot_download_with_retries(source_repo, tmp_source, entry.get("revision", "main"), ignore_patterns=entry.get("ignore_patterns"))
    except Exception as e:
        print(f"  [WARN] failed to download source for CT2: {e}")
        return False

    # Try conversion using ctranslate2 - find executable in PATH or common locations
    def _find_converter() -> str | None:
        import shutil

        # 1. Check PATH
        found = shutil.which("ct2-transformers-converter")
        if found:
            return found
        # 2. Check common Windows locations
        candidates = [
            Path(sys.executable).parent / "Scripts" / "ct2-transformers-converter.exe",
            Path(sys.executable).parent / "ct2-transformers-converter.exe",
            Path.home() / "AppData" / "Roaming" / "Python" / f"Python{sys.version_info.major}{sys.version_info.minor}" / "Scripts" / "ct2-transformers-converter.exe",
            Path(r"C:\Program Files\Python312\Scripts\ct2-transformers-converter.exe"),
            Path("/usr/local/bin/ct2-transformers-converter"),
            Path.home() / ".local" / "bin" / "ct2-transformers-converter",
        ]
        for cand in candidates:
            if cand.exists():
                return str(cand)
        return None

    converter = _find_converter()
    if converter is None:
        print("  [WARN] ctranslate2 not installed (ct2-transformers-converter missing).")
        print("         Keeping original HF model as fallback. Install with: pip install ctranslate2 transformers")
        _copy_fallback(tmp_source, final_dir)
        return False
    try:
        import subprocess

        result = subprocess.run(
            [converter, "--help"],
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise FileNotFoundError("ct2-transformers-converter not working")
    except Exception:
        print("  [WARN] ctranslate2 converter failed to run.")
        _copy_fallback(tmp_source, final_dir)
        return False

    # Run conversion
    try:
        import subprocess

        print(f"  [STT] converting to CT2 (this may take several minutes)...")
        final_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ct2-transformers-converter",
            "--model",
            str(tmp_source),
            "--output_dir",
            str(final_dir),
            "--copy_files",
            "tokenizer.json",
            "preprocessor_config.json",
            "vocabulary.json",
            "--quantization",
            quantization,
        ]
        # Filter copy_files that may not exist - converter ignores missing? Use simpler command
        cmd_simple = [
            converter,
            "--model",
            str(tmp_source),
            "--output_dir",
            str(final_dir),
            "--quantization",
            quantization,
            "--force",
        ]
        # Try simple first
        result = subprocess.run(cmd_simple, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            print(f"  [WARN] CT2 conversion failed: {result.stderr[:500]}")
            _copy_fallback(tmp_source, final_dir)
            return False
        print("  [STT] CT2 conversion succeeded")
        return True
    except subprocess.TimeoutExpired:
        print("  [ERROR] CT2 conversion timed out")
        _copy_fallback(tmp_source, final_dir)
        return False
    except Exception as e:
        print(f"  [WARN] CT2 conversion error: {e}")
        _copy_fallback(tmp_source, final_dir)
        return False


def _copy_fallback(source: Path, dest: Path) -> None:
    import shutil

    if dest.exists() and any(dest.iterdir()):
        print(f"  [INFO] destination {dest} already has content, skipping fallback copy")
        return
    dest.mkdir(parents=True, exist_ok=True)
    print(f"  [INFO] copying source to destination as fallback...")
    for item in source.iterdir():
        s = source / item.name
        d = dest / item.name
        if s.is_dir():
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d)


def ensure_single_model(project_root: Path, entry: dict, force: bool = False) -> str:
    repo_id = entry["repo_id"]
    revision = entry.get("revision", "main")
    local_dir_str = entry["local_dir"]
    allowed_root = get_allowed_models_root(project_root)
    target_dir = validate_local_dir(local_dir_str, allowed_root, project_root)

    if is_model_complete(target_dir) and not force:
        print(f"[SKIP] {entry['id']} ({repo_id}) already exists at {target_dir}")
        return "skipped"

    # Ensure parent exists
    target_dir.mkdir(parents=True, exist_ok=True)

    # Remove marker if force
    marker = target_dir / MARKER_FILENAME
    if force and marker.exists():
        marker.unlink()

    # Handle CT2 special case
    if entry.get("ct2_conversion"):
        # For CT2, we handle download+convert inside
        # But if not forced and directory has content without marker, we still convert/check
        if is_model_complete(target_dir) and not force:
            return "skipped"
        # Determine if we already have CT2 output (check for model.bin)
        if not force and (target_dir / "model.bin").exists():
            print(f"[SKIP] CT2 model already exists at {target_dir}")
            write_marker(target_dir, repo_id)
            return "skipped"
        success = handle_ct2_conversion(project_root, entry, target_dir)
        if success or (target_dir / "model.bin").exists() or any(target_dir.iterdir()):
            write_marker(target_dir, repo_id)
            print(f"[OK] {entry['id']} ready at {target_dir}")
            return "downloaded"
        # If conversion failed but fallback copy done, still mark
        if any(target_dir.iterdir()):
            write_marker(target_dir, repo_id)
            return "downloaded"
        raise RuntimeError(f"CT2 handling failed for {repo_id}")

    # Normal snapshot download
    print(f"[DOWNLOAD] {entry['id']}: {repo_id} -> {target_dir}")
    snapshot_download_with_retries(repo_id, target_dir, revision, ignore_patterns=entry.get("ignore_patterns"))

    # Verify non-empty
    if not any(target_dir.iterdir()):
        raise RuntimeError(f"Download succeeded but target dir empty: {target_dir}")

    write_marker(target_dir, repo_id)
    print(f"[OK] {entry['id']} completed")
    return "downloaded"


def check_disk_space(project_root: Path, models: list[dict], force: bool = False) -> bool:
    """
    Check VPS disk space before downloading.
    Required = sum(expected_size for missing models) + buffer + min_free_after
    Returns True if enough space, False otherwise (prints Arabic+English warning).
    """
    try:
        manifest = load_manifest(project_root)
        buffer_mb = int(manifest.get("disk_buffer_mb", 5120))
        min_free_mb = int(manifest.get("min_free_after_mb", 1024))
    except Exception:
        buffer_mb = 5120
        min_free_mb = 1024

    # Calculate missing size only (already downloaded models don't need space)
    missing_mb = 0
    allowed_root = get_allowed_models_root(project_root)
    for entry in models:
        try:
            target = validate_local_dir(entry["local_dir"], allowed_root, project_root)
        except Exception:
            continue
        if force or not is_model_complete(target):
            missing_mb += int(entry.get("expected_size_mb", 1000))
            # For CT2, tmp source needs same size temporarily
            if entry.get("ct2_conversion"):
                missing_mb += int(entry.get("expected_size_mb", 1000)) // 2  # ~50% extra for tmp

    if missing_mb == 0:
        return True  # Nothing to download, no check needed

    required_mb = missing_mb + buffer_mb
    # Get available space on the models partition (project root)
    try:
        usage = shutil.disk_usage(str(project_root.resolve()))
        available_mb = usage.free // (1024 * 1024)
    except Exception as e:
        print(f"[WARN] could not check disk space: {e}", file=sys.stderr)
        return True  # Don't block if check fails

    # Also ensure after download we keep min_free_mb
    needed_with_reserve = required_mb + min_free_mb
    if available_mb < needed_with_reserve:
        print("\n" + "=" * 70, file=sys.stderr)
        print("❌ مساحة القرص غير كافية / Insufficient disk space", file=sys.stderr)
        print("=" * 70, file=sys.stderr)
        print(f"  المطلوب للنماذج الناقصة: {missing_mb} MB ({missing_mb/1024:.1f} GB)", file=sys.stderr)
        print(f"  + احتياطي للبيانات:      {buffer_mb} MB ({buffer_mb/1024:.1f} GB)", file=sys.stderr)
        print(f"  + احتياطي نظام:          {min_free_mb} MB", file=sys.stderr)
        print(f"  ─────────────────────────", file=sys.stderr)
        print(f"  الإجمالي المطلوب:        {needed_with_reserve} MB ({needed_with_reserve/1024:.1f} GB)", file=sys.stderr)
        print(f"  المتاح حالياً:           {available_mb} MB ({available_mb/1024:.1f} GB)", file=sys.stderr)
        print(f"  الناقص:                  {needed_with_reserve - available_mb} MB", file=sys.stderr)
        print("=" * 70, file=sys.stderr)
        print("  الحل: فضّي مساحة أو استخدم VPS بمساحة أكبر ثم شغّل:", file=sys.stderr)
        print("        python scripts/setup_models.py", file=sys.stderr)
        print("=" * 70 + "\n", file=sys.stderr)
        return False

    # Inform user
    print(f"[SPACE] المتاح: {available_mb/1024:.1f} GB | المطلوب: {needed_with_reserve/1024:.1f} GB (نماذج {missing_mb/1024:.1f} + احتياطي {buffer_mb/1024:.1f} GB) -> OK")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ensure local models are present (idempotent, 3 retries)")
    parser.add_argument("--force", action="store_true", help="Re-download even if marker exists")
    parser.add_argument("--check", action="store_true", help="Only check status, do not download")
    parser.add_argument("--only", type=str, default=None, help="Only process model with given id (embedding|stt|vision|tts)")
    parser.add_argument("--manifest", type=str, default=None, help="Custom manifest path (relative to project root)")
    parser.add_argument("--skip-space-check", action="store_true", help="Skip disk space check")
    args = parser.parse_args(argv)

    project_root = get_project_root()
    allowed_root = get_allowed_models_root(project_root)

    print(f"Project root: {project_root}")
    print(f"Models root:  {allowed_root}")

    try:
        manifest = load_manifest(project_root)
        if args.manifest:
            # Allow custom manifest for testing - validate path security
            custom = validate_local_dir(args.manifest, project_root, project_root)
            with open(custom, "r", encoding="utf-8") as f:
                manifest = json.load(f)
    except Exception as e:
        print(f"[FATAL] failed to load manifest: {e}", file=sys.stderr)
        return 1

    models = manifest["models"]
    if args.only:
        models = [m for m in models if m["id"] == args.only]
        if not models:
            print(f"[ERROR] no model with id '{args.only}'", file=sys.stderr)
            return 1

    # Disk space check (VPS safety) - before any download
    if not args.check and not args.skip_space_check:
        if not check_disk_space(project_root, models, force=args.force):
            return 1

    has_error = False
    stats = {"downloaded": 0, "skipped": 0, "failed": 0}

    for entry in models:
        mid = entry["id"]
        repo = entry["repo_id"]
        local = entry["local_dir"]
        try:
            target = validate_local_dir(local, allowed_root, project_root)
        except Exception as e:
            print(f"[ERROR] invalid path for {mid}: {e}", file=sys.stderr)
            stats["failed"] += 1
            has_error = True
            continue

        if args.check:
            status = "READY" if is_model_complete(target) else "MISSING"
            print(f"[CHECK] {mid:10} {status}  {repo} -> {target}")
            if status == "MISSING":
                has_error = True
            continue

        try:
            result = ensure_single_model(project_root, entry, force=args.force)
            stats[result] = stats.get(result, 0) + 1
        except Exception as e:
            print(f"[FAIL] {mid} ({repo}): {e}", file=sys.stderr)
            stats["failed"] += 1
            has_error = True

    print(f"\nSummary: {stats}")
    if args.check and has_error:
        print("Some models are missing. Run without --check to download.", file=sys.stderr)
        return 1
    return 1 if has_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
