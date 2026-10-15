"""
Embedding store for discoverable tools.
Responsibility: maintain embeddings for tool descriptions, auto-create on startup, detect changes and rebuild.
Uses the bundled embedding model via core/embedding/service.py (offline).
Ships with a prebuilt seed (tool_embeddings.seed.json) so a fresh copy
works instantly when tool specs are unchanged - no embedding needed.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

def _resolve_store_path() -> Path:
    # Unified: embeddings live in X5Agent/Data/vector (inside zip backup).
    try:
        import sys as _sys
        _proj = Path(__file__).resolve().parents[3]
        if str(_proj) not in _sys.path:
            _sys.path.insert(0, str(_proj))
        from core.paths import data_vector_file
        return data_vector_file("tool_embeddings.json")
    except Exception:
        return Path(__file__).resolve().parents[3] / "storage" / "vector" / "tool_embeddings.json"


STORE_PATH = _resolve_store_path()
_STORE_LEGACY = Path(__file__).resolve().parents[3] / "storage" / "vector" / "tool_embeddings.json"
# Prebuilt seed shipped with the project (committed, small JSON). Used when
# the runtime store is missing and specs are unchanged - instant, offline.
_SEED_CANDIDATES = (
    Path(__file__).resolve().parents[3] / "storage" / "vector" / "tool_embeddings.seed.json",
    Path(__file__).resolve().parent / "tool_embeddings.seed.json",
)
# Also keep hash file separately for quick check? Stored inside JSON.


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _collect_discoverable_specs() -> list[dict]:
    """
    Collect specs for all discoverable tools.
    Scans plugins/*/ */spec.json dynamically + ensures the 5 required tools are present.
    """
    root = _project_root()
    plugins_root = root / "plugins"
    tools: list[dict] = []
    # Session-only tools: they live in studio branch sessions alone (the
    # studio loop pre-seeds them directly) and must never surface in the
    # main agent via tool_search.
    _SESSION_ONLY = {"studio_page", "studio_read"}

    # Known discoverable plugin locations (explicit per user request)
    # We scan all plugins subdirectories that contain spec.json
    if plugins_root.exists():
        for category in plugins_root.iterdir():
            if not category.is_dir():
                continue
            # tool-discovery/search itself is not a discoverable tool, skip
            if category.name == "tool-discovery":
                continue
            for tool_dir in category.iterdir():
                if not tool_dir.is_dir():
                    continue
                # skill_create is CORE now (in-context), never discoverable
                if tool_dir.name == "skill_create":
                    continue
                # studio session tools are session-only (studio
                # branch sessions), never discoverable in the main agent
                if tool_dir.name in _SESSION_ONLY:
                    continue
                spec_path = tool_dir / "spec.json"
                if spec_path.exists():
                    try:
                        spec = json.loads(spec_path.read_text(encoding="utf-8"))
                        # Validate spec has name and description
                        if not isinstance(spec.get("name"), str) or not isinstance(spec.get("description"), str):
                            continue
                        tools.append(spec)
                    except Exception:
                        continue
    # workspace + document_search live in core/tools on disk but are classified
    # discoverable (behind tool_search); folders stay for import compat.
    for _disc_name in ("workspace", "document_search"):
        try:
            _spec_path = root / "core" / "tools" / _disc_name / "spec.json"
            if _spec_path.exists():
                _spec = json.loads(_spec_path.read_text(encoding="utf-8"))
                if (isinstance(_spec.get("name"), str) and isinstance(_spec.get("description"), str)
                        and _spec.get("name") not in {t.get("name") for t in tools}):
                    tools.append(_spec)
        except Exception:
            pass
    # Browser discoverable tools (project/X5Browser, single versioned folder).
    # Appended here so tool_search embeds them and the hash auto-rebuilds on change.
    try:
        import sys as _sys2
        _root2 = _project_root()
        if str(_root2) not in _sys2.path:
            _sys2.path.insert(0, str(_root2))
        from X5Browser.catalog import SPECS as _BROWSER_SPECS
        _seen = {t.get("name") for t in tools}
        for _spec in _BROWSER_SPECS:
            try:
                if not isinstance(_spec, dict) or _spec.get("core"):
                    continue
                if not isinstance(_spec.get("name"), str) or not isinstance(_spec.get("description"), str):
                    continue
                if _spec.get("name") in _seen:
                    continue
                tools.append({"name": _spec["name"], "description": _spec["description"],
                              "inputs": _spec.get("inputs", {})})
                _seen.add(_spec["name"])
            except Exception:
                continue
    except Exception:
        pass
    # Sort by name for deterministic hash
    tools.sort(key=lambda x: x.get("name", ""))
    return tools


def _compute_hash(tools: list[dict]) -> str:
    # Hash of concatenated name + description exactly as in spec (verbatim)
    blob = ""
    for t in tools:
        blob += f"{t.get('name','')}:{t.get('description','')}\n"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _spec_fingerprint(name: str, description: str) -> str:
    """Per-spec fingerprint (stored on every entry).

    Incremental-update contract: a stored embedding is reused ONLY when
    its fingerprint still matches the current spec. New specs, edited
    descriptions, and legacy entries (no fingerprint) are re-embedded;
    removed specs simply disappear. Never raises.
    """
    try:
        blob = f"{name}:{description}"
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()
    except Exception:
        return ""


def _load_store() -> dict | None:
    for cand in (STORE_PATH, _STORE_LEGACY):
        if not cand.exists():
            continue
        try:
            data = json.loads(cand.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return None


def _load_seed(current_hash: str) -> dict | None:
    """Return the shipped seed when its hash matches current specs. Never raises."""
    for cand in _SEED_CANDIDATES:
        try:
            if not cand.exists():
                continue
            data = json.loads(cand.read_text(encoding="utf-8"))
            if (isinstance(data, dict) and data.get("hash") == current_hash
                    and isinstance(data.get("tools"), list) and data["tools"]):
                first = data["tools"][0]
                if isinstance(first.get("embedding"), list) and len(first["embedding"]) > 0:
                    return data
        except Exception:
            continue
    return None


def _save_store(hash_val: str, tools_with_embeddings: list[dict]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "hash": hash_val,
        "model_id": "embedding-model",
        "model_path": "embedding_model",
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tools": tools_with_embeddings,
        "count": len(tools_with_embeddings),
    }
    # Write atomically
    tmp = STORE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STORE_PATH)


# Single-flight rebuild guard (file-based, NOT a memory lock).
# tool_search re-execs this file on every call, so each call gets fresh
# module globals: a threading.Lock here would never coordinate across calls.
# The guard file serializes concurrent rebuilds across threads, processes,
# and fresh module loads. Losers wait bounded for the winner's fresh store
# instead of encoding all texts a second time on CPU.
_REBUILD_GUARD_NAME = "tool_embeddings.rebuilding"
_REBUILD_GUARD_STALE_SECONDS = 300
_REBUILD_WAIT_SECONDS = 180


def _rebuild_guard_path() -> Path:
    return STORE_PATH.parent / _REBUILD_GUARD_NAME


def _stored_is_fresh(stored: dict | None, current_hash: str) -> bool:
    if not isinstance(stored, dict) or stored.get("hash") != current_hash:
        return False
    tools = stored.get("tools")
    if not isinstance(tools, list) or not tools:
        return False
    first = tools[0]
    return isinstance(first.get("embedding"), list) and len(first["embedding"]) > 0


def _rebuild_claimed_by_other() -> bool:
    """True when another call/process is actively rebuilding (stale guard ignored)."""
    try:
        guard = _rebuild_guard_path()
        if not guard.exists():
            return False
        try:
            age = time.time() - guard.stat().st_mtime
        except Exception:
            return False
        if age > _REBUILD_GUARD_STALE_SECONDS:
            try:
                guard.unlink(missing_ok=True)
            except Exception:
                pass
            return False
        return True
    except Exception:
        return False


def _claim_rebuild() -> bool:
    """Atomically claim the rebuild right. True when this call won."""
    try:
        STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(_rebuild_guard_path()), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, str(os.getpid()).encode("utf-8"))
        except Exception:
            pass
        try:
            os.close(fd)
        except Exception:
            pass
        return True
    except FileExistsError:
        return False
    except Exception:
        return True


def _release_rebuild() -> None:
    try:
        _rebuild_guard_path().unlink(missing_ok=True)
    except Exception:
        pass


def ensure_embeddings(auto_create: bool = True) -> list[dict]:
    """
    Ensure embeddings exist and are up-to-date.
    - Collects current specs
    - Computes hash
    - If store exists and hash matches, return stored tools
    - Else delete old embeddings, create new via embedding model, save, return
    Called automatically on startup (tool_search first use or main.py)
    """
    tools = _collect_discoverable_specs()
    if not tools:
        # No discoverable tools yet - return empty but still handle
        return []

    current_hash = _compute_hash(tools)
    stored = _load_store()

    if stored is not None and stored.get("hash") == current_hash and stored.get("tools"):
        # Check that embeddings are present and valid length
        # Validate first entry has embedding
        first = stored["tools"][0]
        if isinstance(first.get("embedding"), list) and len(first["embedding"]) > 0:
            # One-time migration: stamp per-spec fingerprints on vectors the
            # global hash already verified — no re-embedding, so the NEXT
            # change is incremental from the start. Never raises outward.
            try:
                cur_fp = {t.get("name", ""): _spec_fingerprint(t.get("name", ""), t.get("description", ""))
                          for t in tools}
                touched = False
                for entry in stored["tools"]:
                    try:
                        if not entry.get("fingerprint"):
                            fp = cur_fp.get(entry.get("name", ""))
                            if fp:
                                entry["fingerprint"] = fp
                                touched = True
                    except Exception:
                        continue
                if touched:
                    _save_store(current_hash, stored["tools"])
            except Exception:
                pass
            return stored["tools"]

    # Fresh copy: use the shipped prebuilt seed when specs are unchanged
    # (instant, fully offline - no embedding model call needed).
    if stored is None:
        seed = _load_seed(current_hash)
        if seed is not None:
            try:
                _save_store(current_hash, seed["tools"])
            except Exception:
                pass
            print(f"[tool-discovery] Prebuilt tool embeddings loaded ({len(seed['tools'])} tools, hash {current_hash[:8]})")
            return seed["tools"]

    # Slow rebuild path is single-flight (file guard): the fast hash check
    # above stays unblocked, losers wait bounded for the winner's fresh
    # store instead of encoding all texts again on CPU.
    if _rebuild_claimed_by_other():
        deadline = time.time() + _REBUILD_WAIT_SECONDS
        while time.time() < deadline:
            time.sleep(2)
            if _stored_is_fresh(_load_store(), current_hash):
                return _load_store()["tools"]
        # Timed out (owner may be dead): fall through and rebuild anyway.
    won = _claim_rebuild()
    if not won:
        # Lost the race after waiting: one last re-check before rebuilding.
        stored = _load_store()
        if _stored_is_fresh(stored, current_hash):
            return stored["tools"]  # type: ignore[return-value]
    try:
        stored = _load_store()
        if _stored_is_fresh(stored, current_hash):
            return stored["tools"]  # type: ignore[return-value]
        # Hash mismatch or no store - rebuild
        if stored is not None and stored.get("hash") != current_hash:
            # Delete old embeddings: remove file (will be overwritten)
            try:
                STORE_PATH.unlink(missing_ok=True)
            except Exception:
                pass
            print(f"[tool-discovery] Descriptions changed (old {stored.get('hash','')[:8]} -> new {current_hash[:8]}), rebuilding embeddings...")

        if not auto_create:
            return []

        # Prepare texts: description is the primary semantic signal, combine name + description for better retrieval
        texts = []
        fingerprints = []
        for t in tools:
            # Use description verbatim as requested, but also include name for better matching
            txt = f"{t.get('name','')} : {t.get('description','')}"
            texts.append(txt[:2000])
            fingerprints.append(_spec_fingerprint(t.get("name", ""), t.get("description", "")))

        # Incremental rebuild: reuse stored embeddings whose fingerprint is
        # unchanged — only new/changed specs hit the model. Removed specs
        # drop out naturally (we iterate current specs only).
        old_by_name: dict[str, dict] = {}
        if isinstance(stored, dict) and isinstance(stored.get("tools"), list):
            for entry in stored["tools"]:
                try:
                    if (isinstance(entry.get("name"), str)
                            and isinstance(entry.get("embedding"), list)
                            and entry["embedding"]):
                        old_by_name[entry["name"]] = entry
                except Exception:
                    continue
        final_emb: list = [None] * len(tools)
        reused = 0
        for i, t in enumerate(tools):
            old = old_by_name.get(t.get("name", ""))
            if (old is not None and old.get("fingerprint") == fingerprints[i]
                    and isinstance(old.get("embedding"), list)):
                final_emb[i] = old["embedding"]
                reused += 1

        def _embed_some(indexes: list[int]) -> None:
            # Embed one batch in place. Needs the model — imported lazily so
            # a fully-reused update never touches it. Never returns half-done.
            try:
                from core.embedding.service import embed_texts, is_ready
            except Exception as e:
                raise RuntimeError(f"Embedding service not available: {e}") from e
            if not is_ready():
                raise FileNotFoundError("Embedding model folder is missing (project/embedding_model/). Copy it with the project and restart.")
            got = embed_texts([texts[i] for i in indexes], normalize=True,
                              progress_label="tool-discovery")
            if len(got) != len(indexes):
                raise RuntimeError(f"Embedding batch incomplete ({len(got)}/{len(indexes)})")
            for i, emb in zip(indexes, got):
                final_emb[i] = emb

        need = [i for i, e in enumerate(final_emb) if e is None]
        if need:
            # Embed with bounded retries inside service (chunked progress lines
            # so a long first-time CPU encode never looks hung).
            _embed_some(need)
        # Dimension guard: reused vectors must match the fresh dimension
        # (model swap) — mismatched ones are re-embedded, never mixed.
        dim = next((len(e) for e in final_emb if isinstance(e, list) and e), 0)
        bad = [i for i, e in enumerate(final_emb)
               if not isinstance(e, list) or len(e) != dim or dim == 0]
        if bad:
            _embed_some(bad)
            dim = next((len(e) for e in final_emb if isinstance(e, list) and e), 0)
            bad = [i for i, e in enumerate(final_emb)
                   if not isinstance(e, list) or len(e) != dim or dim == 0]
        if bad:
            raise RuntimeError(f"Embedding rebuild left {len(bad)} tools without vectors")

        tools_with_emb = []
        for spec, emb, fp in zip(tools, final_emb, fingerprints):
            tools_with_emb.append({
                "name": spec["name"],
                "description": spec["description"],
                "spec": spec,
                "embedding": emb,
                "fingerprint": fp,
            })

        _save_store(current_hash, tools_with_emb)
        print(f"[tool-discovery] Incremental update: reused {reused}, "
              f"embedded {len(tools_with_emb) - reused} of {len(tools_with_emb)} tools, "
              f"hash {current_hash[:8]}")
        return tools_with_emb
    finally:
        if won:
            _release_rebuild()


def get_embeddings() -> list[dict]:
    """Return current embeddings, ensuring they are up-to-date (auto-create)."""
    return ensure_embeddings(auto_create=True)


def search(query: str, top_k: int = 5) -> list[dict]:
    """
    Semantic search over discoverable tools using embedding cosine similarity.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be non-empty string")
    if len(query) > 2000:
        raise ValueError("query too long")
    if not isinstance(top_k, int) or top_k < 1 or top_k > 20:
        top_k = 5

    tools = ensure_embeddings(auto_create=True)
    if not tools:
        return []

    from core.embedding.service import embed_query
    import numpy as np

    q_emb = np.array(embed_query(query), dtype=np.float32)
    scored = []
    for t in tools:
        emb = np.array(t["embedding"], dtype=np.float32)
        score = float(np.dot(q_emb, emb))  # cosine since normalized
        scored.append((score, t))
    scored.sort(reverse=True, key=lambda x: x[0])
    results = []
    for score, t in scored[:top_k]:
        # Only return if reasonably relevant? But return top_k always for now
        results.append({
            "name": t["name"],
            "description": t["description"],
            "spec": t["spec"],
            "score": round(score, 4),
        })
    return results


def force_rebuild() -> list[dict]:
    """Force delete and rebuild_embeddings (used when user changes descriptions manually)."""
    if STORE_PATH.exists():
        try:
            STORE_PATH.unlink()
        except Exception:
            pass
    return ensure_embeddings(auto_create=True)
