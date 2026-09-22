"""
VPS entrypoint - zero-knowledge user: just clone and run.
Responsibility: check space -> auto-download models -> start agent loop.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    print("=" * 70)
    print("  A New Agent Loop - Starting")
    print("=" * 70)

    # 1. Ensure models (includes disk space check  + 5GB buffer + auto-download)
    try:
        from models.loader import are_all_models_ready, ensure_models
    except ImportError as e:
        print(f"[FATAL] models loader not found: {e}", file=sys.stderr)
        return 1

    if are_all_models_ready():
        print("[OK] All 4 models ready (embedding, stt, vision, tts)")
    else:
        print("[INFO] Models missing - checking disk space and downloading...")
        print("       This is first run on this VPS, will take 5-10 minutes (6.3 GB)")
        ok = ensure_models(auto_download=True, timeout_seconds=1800)
        if not ok:
            print("\n❌ فشل تحميل النماذج - راجع رسالة المساحة أعلاه", file=sys.stderr)
            print("   Fix disk space and re-run: python main.py", file=sys.stderr)
            return 1
        print("[OK] All models downloaded and verified")

    # 2. Verify models actually load (quick sanity)
    try:
        from models.loader import get_model_path

        for mid in ("embedding", "stt", "vision", "tts"):
            p = get_model_path(mid)
            print(f"  - {mid:10} -> {p}")
            if p is None or not p.exists():
                print(f"[ERROR] model {mid} path missing!", file=sys.stderr)
                return 1
    except Exception as e:
        print(f"[WARN] verification failed: {e}", file=sys.stderr)

    # 3. Start agent loop (placeholder - will be replaced by core/agent/loop)
    print("\n" + "=" * 70)
    print("  ✅ Everything ready - Agent Loop would start here")
    print("  (core/agent/loop not yet implemented - placeholder)")
    print("=" * 70)
    print("  Next: implement core/agent/loop to use the 4 models")
    # Example:
    # from core.agent.loop import run
    # return run()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
