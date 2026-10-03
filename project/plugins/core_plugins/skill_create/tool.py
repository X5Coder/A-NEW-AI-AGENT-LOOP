"""
skill_create - create reusable skill under skills/{name}/
Validates name, prevents traversal, writes skill.json atomically.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .events import format_event

MAX_NAME_LEN = 50
MAX_CONTENT_LEN = 10000
MAX_DESC_LEN = 500


def _validate_name(v) -> str:
    if not isinstance(v, str):
        raise TypeError("name must be string")
    s = v.strip()
    if not s:
        raise ValueError("name required")
    if len(s) > MAX_NAME_LEN:
        raise ValueError(f"name too long (max {MAX_NAME_LEN})")
    if ".." in s or "/" in s or "\\" in s:
        raise ValueError("Invalid name (path traversal)")
    if not re.match(r"^[\w\-\s]+$", s):
        raise ValueError("name must be alphanumeric, dash, underscore, space")
    return s


def _validate_content(v) -> str:
    if not isinstance(v, str):
        raise TypeError("content must be string")
    s = v.strip()
    if not s:
        raise ValueError("content required")
    if len(s) > MAX_CONTENT_LEN:
        raise ValueError(f"content too long (max {MAX_CONTENT_LEN})")
    return s


def _validate_desc(v) -> str | None:
    if v is None:
        return None
    if not isinstance(v, str):
        raise TypeError("description must be string")
    s = v.strip()
    if not s:
        return None
    if len(s) > MAX_DESC_LEN:
        raise ValueError(f"description too long (max {MAX_DESC_LEN})")
    return s


def execute(name: str, content: str, description: str | None = None) -> dict:
    n = _validate_name(name)
    c = _validate_content(content)
    d = _validate_desc(description)
    event = format_event(name=n)

    project_root = Path(__file__).resolve().parents[3]
    skills_dir = project_root / "skills"
    # Normalize name to filesystem safe: replace spaces with _
    safe = re.sub(r"\s+", "_", n.strip())
    safe = re.sub(r"[^a-zA-Z0-9_\-]", "", safe)
    if not safe:
        safe = "skill"

    target_dir = (skills_dir / safe).resolve()
    # Validate inside skills_dir
    try:
        target_dir.relative_to(skills_dir.resolve())
    except ValueError as exc:
        raise ValueError("Skill path escapes allowed directory") from exc

    target_dir.mkdir(parents=True, exist_ok=True)
    skill_file = target_dir / "skill.json"
    data = {
        "name": n,
        "description": d or n,
        "content": c,
        "instructions": c,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    # Atomic write
    tmp = target_dir / ".tmp_skill.json"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(skill_file)

    # Also write README for convenience (always synced — same name overwrites on edit)
    readme = target_dir / "README.md"
    readme.write_text(f"# {n}\n\n{d or ''}\n\n## Instructions\n\n{c}\n", encoding="utf-8")

    return {"event": event, "skill_name": n, "safe_name": safe, "path": str(target_dir), "success": True}
