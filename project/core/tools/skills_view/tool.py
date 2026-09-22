"""
skills_view - view skill instructions.
Validates skill_name, prevents path traversal, reads from skills directory.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .events import format_event


def _validate_skill_name(v) -> str:
    if not isinstance(v, str):
        raise TypeError("skill_name must be string")
    s = v.strip()
    if not s:
        raise ValueError("skill_name required")
    if len(s) > 100:
        raise ValueError("skill_name too long")
    # allow alnum, dash, underscore, space -> but sanitize for filename
    if ".." in s or "/" in s or "\\" in s:
        raise ValueError("Invalid skill_name (path traversal)")
    if not re.match(r"^[\w\-\s]+$", s):
        raise ValueError("skill_name must be alphanumeric, dash, underscore, space")
    return s


def execute(skill_name: str) -> dict:
    name = _validate_skill_name(skill_name)
    event = format_event(skill_name=name)
    project_root = Path(__file__).resolve().parents[3]
    skills_dir = project_root / "skills"
    # Try exact match case-insensitive
    target = None
    for child in skills_dir.iterdir():
        if child.is_dir() and child.name.lower() == name.lower():
            target = child
            break
    # Fallback with normalized spaces/dashes
    if target is None:
        for child in skills_dir.iterdir():
            if child.is_dir():
                norm = child.name.lower().replace("_", " ").replace("-", " ")
                if norm == name.lower().replace("_", " ").replace("-", " "):
                    target = child
                    break
    if target is None:
        raise FileNotFoundError(f"Skill not found: {name}")

    # Validate target inside skills_dir
    try:
        target.resolve().relative_to(skills_dir.resolve())
    except ValueError as exc:
        raise ValueError("Skill path escapes allowed directory") from exc

    # Read contents: try skill.json, then README.md, then any md
    content = ""
    instructions = ""
    # skill.json
    sj = target / "skill.json"
    if sj.exists():
        try:
            j = json.loads(sj.read_text(encoding="utf-8"))
            content = j.get("description", "") or j.get("content", "")
            instructions = j.get("instructions", "") or content
        except Exception:
            pass
    if not instructions:
        for candidate in [target / "README.md", target / "instructions.md"]:
            if candidate.exists():
                try:
                    instructions = candidate.read_text(encoding="utf-8")[:5000]
                    break
                except Exception:
                    continue
    if not instructions:
        # list files
        files = [p.name for p in target.iterdir() if p.is_file()]
        instructions = f"Skill '{target.name}' has no instructions file. Files: {', '.join(files[:10])}"
    return {"event": event, "skill_name": target.name, "path": str(target), "instructions": instructions[:5000]}
