"""
VPS entrypoint - zero-knowledge user: just clone and run.
Responsibility: check space -> auto-download models -> start agent loop.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    print("=" * 70)
    print("  X5Agent - Starting")
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
            print("\n❌ Model download failed - check disk space message above", file=sys.stderr)
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

    # 2b. Unify sandbox: ensure X5Agent dirs + one-time legacy storage -> Data migration
    try:
        from core.paths import ensure_x5agent, migrate_legacy_to_data_once

        ensure_x5agent()
        migrated = migrate_legacy_to_data_once()
        if any(migrated.values()):
            print(f"[OK] Migrated legacy storage to X5Agent/Data: {migrated}")
        else:
            print("[OK] X5Agent sandbox ready (Data is authoritative)")
    except Exception as e:
        print(f"[WARN] sandbox ensure failed: {e}", file=sys.stderr)

    # 3. Auto-embed tool descriptions for tool_search (detect change -> rebuild)
    try:
        import importlib.util

        store_path = Path(__file__).resolve().parent / "plugins" / "tool-discovery" / "search" / "store.py"
        spec = importlib.util.spec_from_file_location("tool_discovery_store_startup", str(store_path))
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore
            tools = mod.ensure_embeddings(auto_create=True)
            print(f"[OK] Tool embeddings ready: {len(tools)} discoverable tools indexed")
            for t in tools:
                print(f"  - {t['name']:15} -> {t['description'][:60]}...")
        else:
            print("[WARN] could not load tool discovery store", file=sys.stderr)
    except FileNotFoundError as e:
        print(f"[WARN] tool embeddings skipped (model missing): {e}", file=sys.stderr)
    except Exception as e:
        print(f"[WARN] tool embeddings failed: {e}", file=sys.stderr)

    # 4. Start agent loop (placeholder - will be replaced by core/agent/loop)
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
