"""
Doctor - checks system health for VPS.
Responsibility: single - report status of python, disk, models, dependencies.
Usage: python scripts/doctor.py  OR  python -m scripts.doctor
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

# Ensure project root is on sys.path for `import models` when run as script
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def get_project_root() -> Path:
    return _PROJECT_ROOT


def check_python() -> bool:
    print("=== Python ===")
    print(f"  Version: {sys.version}")
    ok = sys.version_info >= (3, 10)
    print(f"  {'✅' if ok else '❌'} Requires >=3.10: {'OK' if ok else 'FAIL - upgrade Python'}")
    return ok


def check_disk() -> bool:
    print("\n=== Disk Space ===")
    root = get_project_root()
    try:
        usage = shutil.disk_usage(str(root.resolve()))
        free_gb = usage.free / (1024**3)
        total_gb = usage.total / (1024**3)
        print(f"  Path: {root.resolve()}")
        print(f"  Total: {total_gb:.1f} GB | Free: {free_gb:.1f} GB")
        # Load required from manifest
        manifest_path = root / "models" / "manifest.json"
        if manifest_path.exists():
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            required_mb = sum(m.get("expected_size_mb", 1000) for m in data["models"])
            # Add CT2 tmp overhead
            for m in data["models"]:
                if m.get("ct2_conversion"):
                    required_mb += m.get("expected_size_mb", 1000) // 2
            buffer_mb = data.get("disk_buffer_mb", 5120)
            min_free = data.get("min_free_after_mb", 1024)
            need_gb = (required_mb + buffer_mb + min_free) / 1024
            print(f"  Required for fresh install: {need_gb:.1f} GB (models {required_mb/1024:.1f} + buffer {buffer_mb/1024:.1f})")
            ok = free_gb >= need_gb
            # If models already installed, need less
            from models.loader import are_all_models_ready

            if are_all_models_ready():
                print(f"  ✅ Models already installed - disk OK (need 0, have {free_gb:.1f} GB)")
                return True
            print(f"  {'✅' if ok else '❌'} {'Enough' if ok else 'NOT ENOUGH - free space required'}")
            return ok
        return True
    except Exception as e:
        print(f"  ⚠️ Could not check disk: {e}")
        return True


def check_dependencies() -> bool:
    print("\n=== Dependencies ===")
    deps = ["huggingface_hub", "transformers", "sentence_transformers", "faster_whisper", "ctranslate2"]
    all_ok = True
    for dep in deps:
        try:
            __import__(dep)
            print(f"  ✅ {dep}")
        except ImportError:
            print(f"  ❌ {dep} - missing (pip install -e .)")
            all_ok = False
    return all_ok


def check_models() -> bool:
    print("\n=== Models (4 required) ===")
    try:
        from models.loader import are_all_models_ready, is_model_ready, get_model_path
    except Exception as e:
        print(f"  ❌ loader import failed: {e}")
        return False

    manifest_path = get_project_root() / "models" / "manifest.json"
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  ❌ manifest failed: {e}")
        return False

    all_ok = True
    for m in data["models"]:
        mid = m["id"]
        ready = is_model_ready(mid)
        path = get_model_path(mid)
        size = "?"
        if path and path.exists():
            try:
                total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
                size = f"{total/1024/1024:.0f} MB"
            except Exception:
                pass
        status = "✅ READY" if ready else "❌ MISSING"
        print(f"  {status} {mid:10} {size:>10} -> {path}")
        if not ready:
            all_ok = False

    print(f"\n  Overall: {'✅ All READY' if all_ok else '❌ Some MISSING - run: python main.py  OR  python scripts/setup_models.py'}")
    return all_ok


def check_model_load() -> bool:
    print("\n=== Model Load Test (quick) ===")
    ok = True
    # Embedding
    try:
        from sentence_transformers import SentenceTransformer

        path = get_project_root() / "models" / "embedding" / "Qwen3-Embedding-0.6B"
        if (path / ".download_complete").exists():
            print("  ✅ embedding: path exists (skip heavy load in doctor)")
        else:
            print("  ⏭️ embedding: not downloaded, skip load test")
    except Exception as e:
        print(f"  ❌ embedding: {e}")
        ok = False

    # STT CT2
    try:
        from faster_whisper import WhisperModel

        path = get_project_root() / "models" / "stt" / "whisper-medium-arabic-codeswitched-ct2"
        if (path / "model.bin").exists():
            m = WhisperModel(str(path), device="cpu", compute_type="int8")
            print(f"  ✅ stt CT2: loads OK")
        else:
            print("  ⏭️ stt: model.bin missing, skip")
    except Exception as e:
        print(f"  ❌ stt: {e}")
        ok = False

    # Vision
    try:
        from transformers import AutoProcessor

        path = get_project_root() / "models" / "vision" / "SmolVLM2-500M"
        if (path / ".download_complete").exists():
            AutoProcessor.from_pretrained(str(path), local_files_only=True)
            print("  ✅ vision: processor loads OK")
        else:
            print("  ⏭️ vision: not downloaded")
    except Exception as e:
        print(f"  ❌ vision: {e}")
        ok = False

    # TTS
    try:
        path = get_project_root() / "models" / "tts" / "supertonic-3"
        if (path / ".download_complete").exists():
            print("  ✅ tts: path exists")
        else:
            print("  ⏭️ tts: not downloaded")
    except Exception as e:
        print(f"  ❌ tts: {e}")
        ok = False

    return ok


def main() -> int:
    print("=" * 70)
    print("  Doctor - X5Agent")
    print("=" * 70)
    results = []
    results.append(("Python", check_python()))
    results.append(("Disk", check_disk()))
    results.append(("Dependencies", check_dependencies()))
    results.append(("Models", check_models()))
    results.append(("LoadTest", check_model_load()))

    print("\n" + "=" * 70)
    print("  Summary")
    print("=" * 70)
    for name, ok in results:
        print(f"  {'✅' if ok else '❌'} {name}")
    all_ok = all(r[1] for r in results)
    print("\n" + ("✅ System healthy - ready to run: python main.py" if all_ok else "❌ Issues found - fix above then run: python scripts/setup_models.py"))
    print("=" * 70)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
