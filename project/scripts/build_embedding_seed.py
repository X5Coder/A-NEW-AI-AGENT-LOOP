"""Build prebuilt embedding seeds shipped with the project.

Reads the runtime stores (X5Agent/Data/vector) and copies them as
*.seed.json into project/storage/vector/ (committed, small JSON), so a
fresh copy gets instant tool/trigger search when specs are unchanged.

- Offline by default: the bundled project/embedding_model/ folder is used,
  nothing is downloaded.
- --download : opt-in first-install download of the bundled folder, then build.
- --snapshot-only : skip the model entirely; snapshot the current runtime
  stores as seeds after verifying their hash matches current specs
  (instant, used when embeddings are already built).

Usage (from project/):
    python scripts/build_embedding_seed.py [--download] [--snapshot-only]
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _load_store_module(relative: str, name: str):
    path = PROJECT_ROOT / relative
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise FileNotFoundError(f"store not found: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
    return mod


def _snapshot_one(label: str, store_mod, live_name: str, seed_name: str) -> bool:
    """Copy the runtime store to a seed after hash verification. No model load."""
    from core.paths import data_vector_dir

    src = data_vector_dir() / live_name
    dst = PROJECT_ROOT / "storage" / "vector" / seed_name
    if not src.exists():
        print(f"[seed] SKIP (no runtime store): {live_name}")
        return False
    try:
        specs = store_mod._collect_discoverable_specs() if hasattr(
            store_mod, "_collect_discoverable_specs") else store_mod._collect_trigger_specs()
        current_hash = store_mod._compute_hash(specs)
        data = json.loads(src.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("hash") != current_hash:
            print(f"[seed] SKIP {label}: runtime hash is stale - run without --snapshot-only")
            return False
        data["model_id"] = "embedding-model"
        data["model_path"] = "embedding_model"
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(dst)
        print(f"[seed] wrote storage/vector/{seed_name} "
              f"({dst.stat().st_size / 1024:.0f} KB, {label}: {len(specs)})")
        return True
    except Exception as e:
        print(f"[seed] SKIP {seed_name}: {e}", file=sys.stderr)
        return False


def main(argv: list[str]) -> int:
    if "--download" in argv:
        os.environ["X5_ALLOW_EMBEDDING_DOWNLOAD"] = "1"

    tool_store = _load_store_module("plugins/tool-discovery/search/store.py", "seed_tool_store")
    trigger_store = _load_store_module(
        "plugins/trigger-discovery/search/store.py", "seed_trigger_store")

    if "--snapshot-only" in argv:
        ok_tools = _snapshot_one(
            "tools", tool_store, "tool_embeddings.json", "tool_embeddings.seed.json")
        ok_triggers = _snapshot_one(
            "triggers", trigger_store, "trigger_embeddings.json", "trigger_embeddings.seed.json")
        if not (ok_tools and ok_triggers):
            print("[seed] ERROR: seeds not written", file=sys.stderr)
            return 1
        print("[seed] done - commit the *.seed.json files with the project.")
        return 0

    from core.embedding.bootstrap import detect_device, ensure_embedding_model

    try:
        ensure_embedding_model()
    except Exception as e:
        print(f"[seed] ERROR: {e}", file=sys.stderr)
        return 1
    print(f"[seed] embedding model ready (device: {detect_device()}, auto)")

    # ensure_* reuses the runtime store when specs are unchanged (hash match),
    # so this is instant on a healthy copy and rebuilds only when needed.
    tools = tool_store.ensure_embeddings(auto_create=True)
    triggers = trigger_store.ensure_embeddings(auto_create=True)
    print(f"[seed] runtime stores ready: {len(tools)} tools, {len(triggers)} triggers")

    from core.paths import data_vector_dir

    built = 0
    for live_name, seed_name in (
        ("tool_embeddings.json", "tool_embeddings.seed.json"),
        ("trigger_embeddings.json", "trigger_embeddings.seed.json"),
    ):
        src = data_vector_dir() / live_name
        dst = PROJECT_ROOT / "storage" / "vector" / seed_name
        if not src.exists():
            print(f"[seed] SKIP (no runtime store): {live_name}")
            continue
        try:
            data = json.loads(src.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data["model_id"] = "embedding-model"
                data["model_path"] = "embedding_model"
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(dst)
            print(f"[seed] wrote storage/vector/{seed_name} ({dst.stat().st_size / 1024:.0f} KB)")
            built += 1
        except Exception as e:
            print(f"[seed] SKIP {seed_name}: {e}", file=sys.stderr)
    if not built:
        print("[seed] ERROR: no seeds written", file=sys.stderr)
        return 1
    print("[seed] done - commit the *.seed.json files with the project.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
