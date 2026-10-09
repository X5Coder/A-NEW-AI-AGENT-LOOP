"""
Trigger embedding store - per-plugin triggers, searchable via trigger_search.
Similar to tool-discovery but for triggers. Uses the bundled embedding
model via core/embedding/service.py (offline). Ships with a prebuilt seed
(trigger_embeddings.seed.json) for instant offline use when specs match.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

def _resolve_store_path() -> Path:
    # Unified: embeddings live in X5Agent/Data/vector (inside zip backup).
    try:
        import sys as _sys
        _proj = Path(__file__).resolve().parents[3]
        if str(_proj) not in _sys.path:
            _sys.path.insert(0, str(_proj))
        from core.paths import data_vector_file
        return data_vector_file("trigger_embeddings.json")
    except Exception:
        return Path(__file__).resolve().parents[3] / "storage" / "vector" / "trigger_embeddings.json"


STORE_PATH = _resolve_store_path()
_STORE_LEGACY = Path(__file__).resolve().parents[3] / "storage" / "vector" / "trigger_embeddings.json"
# Prebuilt seed shipped with the project (committed, small JSON).
_SEED_CANDIDATES = (
    Path(__file__).resolve().parents[3] / "storage" / "vector" / "trigger_embeddings.seed.json",
    Path(__file__).resolve().parent / "trigger_embeddings.seed.json",
)

def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]

def _collect_trigger_specs() -> list[dict]:
    root = _project_root()
    triggers = []
    plugins_root = root / "plugins"
    if plugins_root.exists():
        for category in plugins_root.iterdir():
            if not category.is_dir():
                continue
            triggers_dir = category / "triggers"
            if not triggers_dir.exists():
                continue
            for trig_dir in triggers_dir.iterdir():
                if not trig_dir.is_dir():
                    continue
                spec_path = trig_dir / "spec.json"
                if spec_path.exists():
                    try:
                        spec = json.loads(spec_path.read_text(encoding="utf-8"))
                        if isinstance(spec.get("name"), str) and isinstance(spec.get("description"), str):
                            triggers.append(spec)
                    except Exception:
                        continue
    triggers.sort(key=lambda x: x.get("name",""))
    return triggers

def _compute_hash(triggers: list[dict]) -> str:
    blob = ""
    for t in triggers:
        blob += f"{t.get('name','')}:{t.get('description','')}\n"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()

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
                    and isinstance(data.get("triggers"), list) and data["triggers"]):
                first = data["triggers"][0]
                if isinstance(first.get("embedding"), list) and len(first["embedding"]) > 0:
                    return data
        except Exception:
            continue
    return None


def _save_store(hash_val: str, triggers_with_emb: list[dict]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "hash": hash_val,
        "model_id": "embedding-model",
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "triggers": triggers_with_emb,
        "count": len(triggers_with_emb)
    }
    tmp = STORE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STORE_PATH)

def ensure_embeddings(auto_create: bool = True) -> list[dict]:
    triggers = _collect_trigger_specs()
    if not triggers:
        return []
    current_hash = _compute_hash(triggers)
    stored = None
    if STORE_PATH.exists():
        try:
            stored = json.loads(STORE_PATH.read_text(encoding="utf-8"))
        except Exception:
            stored = None
    if stored and stored.get("hash") == current_hash and stored.get("triggers"):
        first = stored["triggers"][0]
        if isinstance(first.get("embedding"), list) and len(first["embedding"])>0:
            return stored["triggers"]
    if stored and stored.get("hash") != current_hash:
        try:
            STORE_PATH.unlink(missing_ok=True)
        except Exception:
            pass
        print(f"[trigger-discovery] Triggers changed ({stored.get('hash','')[:8]} -> {current_hash[:8]}), rebuilding...")
    if stored is None:
        seed = _load_seed(current_hash)
        if seed is not None:
            try:
                _save_store(current_hash, seed["triggers"])
            except Exception:
                pass
            print(f"[trigger-discovery] Prebuilt trigger embeddings loaded ({len(seed['triggers'])} triggers, hash {current_hash[:8]})")
            return seed["triggers"]
    if not auto_create:
        return []
    from core.embedding.service import embed_texts, is_ready
    if not is_ready():
        raise FileNotFoundError("Embedding model folder is missing (project/embedding_model/). Copy it with the project and restart.")
    texts = [f"{t.get('name','')} : {t.get('description','')}"[:2000] for t in triggers]
    embeddings = embed_texts(texts, normalize=True)
    with_emb = []
    for spec, emb in zip(triggers, embeddings):
        with_emb.append({"name": spec["name"], "description": spec["description"], "spec": spec, "embedding": emb})
    _save_store(current_hash, with_emb)
    print(f"[trigger-discovery] Embeddings for {len(with_emb)} triggers, hash {current_hash[:8]}")
    return with_emb

def search(query: str, top_k: int = 5) -> list[dict]:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be non-empty")
    if len(query) > 2000:
        raise ValueError("query too long")
    triggers = ensure_embeddings(auto_create=True)
    if not triggers:
        return []
    from core.embedding.service import embed_query
    import numpy as np
    q_emb = np.array(embed_query(query), dtype=np.float32)
    scored = []
    for t in triggers:
        score = float(np.dot(q_emb, np.array(t["embedding"], dtype=np.float32)))
        scored.append((score, t))
    scored.sort(reverse=True, key=lambda x: x[0])
    results = []
    for score, t in scored[:top_k]:
        results.append({"name": t["name"], "description": t["description"], "spec": t["spec"], "score": round(score,4)})
    return results
