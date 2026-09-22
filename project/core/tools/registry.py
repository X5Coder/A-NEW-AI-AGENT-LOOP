"""
Tool registry - single source of truth for all tools.
Provides listing and validation, used by gateway and tests.
"""
from __future__ import annotations

from pathlib import Path
import json

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

CORE_TOOLS = ["tool_search", "question", "vector_chat_search", "todo_list", "scheduler", "skills_search", "skills_view", "terminal"]
DISCOVERABLE_PLUGINS = ["read_file", "write_file", "search_files", "vision_analyze", "skill_create"]


def list_core_specs() -> list[dict]:
    specs = []
    for name in CORE_TOOLS:
        p = _PROJECT_ROOT / "core" / "tools" / name / "spec.json"
        if p.exists():
            try:
                specs.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                continue
    return specs


def list_discoverable_specs() -> list[dict]:
    specs = []
    plugins_root = _PROJECT_ROOT / "plugins"
    if plugins_root.exists():
        for cat in plugins_root.iterdir():
            if not cat.is_dir() or cat.name == "tool-discovery":
                continue
            for tool_dir in cat.iterdir():
                spec = tool_dir / "spec.json"
                if spec.exists():
                    try:
                        specs.append(json.loads(spec.read_text(encoding="utf-8")))
                    except Exception:
                        continue
    specs.sort(key=lambda x: x.get("name", ""))
    return specs
