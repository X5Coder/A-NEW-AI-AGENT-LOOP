"""
skills_search - semantic search over skills via embedding.
Fallback lexical if embedding unavailable.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .events import format_event


def _validate_query(q: Any) -> str:
    if not isinstance(q, str):
        raise TypeError("query must be string")
    s = q.strip()
    if not s:
        raise ValueError("query required")
    if len(s) > 2000:
        raise ValueError("query too long")
    return s


def _validate_k(k: Any) -> int:
    if k is None:
        return 5
    if not isinstance(k, int):
        raise TypeError("k must be integer")
    if k < 1 or k > 20:
        raise ValueError("k 1..20")
    return k


def _load_skills() -> list[dict]:
    project_root = Path(__file__).resolve().parents[3]
    skills_dir = project_root / "skills"
    if not skills_dir.exists():
        return []
    skills = []
    for child in skills_dir.iterdir():
        if child.is_dir():
            # try read skill manifest or description
            desc = ""
            name = child.name
            # look for README or skill.json
            for candidate in [child / "skill.json", child / "README.md", child / "description.txt"]:
                if candidate.exists():
                    try:
                        txt = candidate.read_text(encoding="utf-8")
                        if candidate.suffix == ".json":
                            j = json.loads(txt)
                            desc = j.get("description", "") or j.get("content", "")[:500]
                            name = j.get("name", name)
                        else:
                            desc = txt[:500]
                        break
                    except Exception:
                        continue
            if not desc:
                desc = name
            skills.append({"name": name, "path": str(child), "description": desc})
    return skills


def execute(query: str, k: int | None = None) -> dict:
    q = _validate_query(query)
    limit = _validate_k(k)
    event = format_event()
    skills = _load_skills()
    if not skills:
        return {"event": event, "query": q, "results": [], "note": "no skills found"}

    try:
        from models.embedding.service import embed_texts, is_ready
        if is_ready():
            descs = [s["description"][:1000] for s in skills]
            q_emb = embed_texts([q])[0]
            d_embs = embed_texts(descs)
            import numpy as np
            qv = np.array(q_emb)
            scored = []
            for idx, emb in enumerate(d_embs):
                score = float(np.dot(qv, np.array(emb)))
                scored.append((score, idx))
            scored.sort(reverse=True, key=lambda x: x[0])
            results = []
            for score, idx in scored[:limit]:
                sk = skills[idx]
                results.append({"score": round(score, 4), "skill": sk})
            return {"event": event, "query": q, "k": limit, "results": results}
    except Exception:
        pass

    # lexical fallback
    q_low = q.lower()
    scored = []
    for idx, sk in enumerate(skills):
        txt = (sk["name"] + " " + sk["description"]).lower()
        score = txt.count(q_low)
        if q_low in txt:
            score += 1
        scored.append((score, idx))
    scored.sort(reverse=True, key=lambda x: x[0])
    results = [{"score": s, "skill": skills[i]} for s, i in scored[:limit] if s > 0]
    return {"event": event, "query": q, "k": limit, "results": results}
