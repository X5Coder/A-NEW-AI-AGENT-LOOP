"""
Tool dispatcher - maps tool names to execute functions for agent loop.
Handles both CORE and DISCOVERABLE tools.
Validates model-generated arguments (untrusted) before execution.
"""
from __future__ import annotations

import json
import importlib.util
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Registry: name -> (module_path, spec)
_CORE_MAP = {
    "tool_search": "core.tools.tool_search.tool",
    "question": "core.tools.question.tool",
    "vector_chat_search": "core.tools.vector_chat_search.tool",
    "todo_list": "core.tools.todo_list.tool",
    "scheduler": "core.tools.scheduler.tool",
    "skills_search": "core.tools.skills_search.tool",
    "skills_view": "core.tools.skills_view.tool",
    "terminal": "core.tools.terminal.tool",
    "wait": "core.tools.wait.tool",
}

_DISCOVERABLE_MAP = {
    "read_file": "plugins.filesystem.read_file.tool",
    "write_file": "plugins.filesystem.write_file.tool",
    "search_files": "plugins.filesystem.search_files.tool",
    "vision_analyze": "plugins.vision.vision_analyze.tool",
    "video_analyze": "plugins.vision.video_analyze.tool",
    "video_transcribe": "plugins.vision.video_transcribe.tool",
    "skill_create": "plugins.skills.skill_create.tool",
    "image_memory": "plugins.memory.image_memory.tool",
    "important_memory": "plugins.memory.important_memory.tool",
}

_ALL = {**_CORE_MAP, **_DISCOVERABLE_MAP}


def _load_module(dotted: str):
    # Use importlib.import_module for proper package-relative imports (handles `from .events import`)
    # Ensure project root is in sys.path
    import sys
    import importlib
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    # Handle dash paths via file location (only tool-discovery)
    if "tool-discovery" in dotted:
        p = PROJECT_ROOT / "plugins" / "tool-discovery" / "search" / "store.py"
        spec = importlib.util.spec_from_file_location("store", str(p))
        mod = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(mod)
        return mod
    # Normal case: dotted is importable package now that we added __init__.py
    try:
        return importlib.import_module(dotted)
    except ImportError:
        # Fallback to file location if import fails
        parts = dotted.split(".")
        if parts[0] == "core" and len(parts) >= 4 and parts[1] == "tools":
            f = PROJECT_ROOT / "core" / "tools" / parts[2] / "tool.py"
        elif parts[0] == "plugins" and len(parts) == 4:
            f = PROJECT_ROOT / "plugins" / parts[1] / parts[2] / "tool.py"
        else:
            f = PROJECT_ROOT / "/".join(parts[:-1]) / "tool.py"
        module_name = dotted.replace(".", "_").replace("-", "_")
        spec = importlib.util.spec_from_file_location(module_name, str(f))
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load {dotted} from {f}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


def execute_tool(name: str, arguments: dict | str | None) -> dict:
    """
    Execute tool by name with validated arguments.
    Returns dict with tool output (or error) - never raises unhandled, loop continues.
    """
    if not isinstance(name, str) or not name.strip():
        return {"error": "tool name missing", "tool": name}
    n = name.strip()
    if n not in _ALL:
        # Try discover additional plugins dynamically by scanning storage/vector? But just error
        return {"error": f"Unknown tool: {n}. Available: {sorted(_ALL.keys())}", "tool": n}
    dotted = _ALL[n]
    # Arguments validation: model-generated data is untrusted
    args: dict[str, Any] = {}
    if arguments is None:
        args = {}
    elif isinstance(arguments, dict):
        args = arguments
    elif isinstance(arguments, str):
        try:
            args = json.loads(arguments) if arguments.strip() else {}
            if not isinstance(args, dict):
                return {"error": f"Tool {n} arguments must be JSON object, got {type(args)}", "tool": n}
        except Exception as e:
            return {"error": f"Tool {n} arguments invalid JSON: {e}", "tool": n}
    else:
        return {"error": f"Tool {n} arguments must be dict or JSON string", "tool": n}
    # Size limits: args JSON stringify truncated 10000 chars
    try:
        if len(json.dumps(args, ensure_ascii=False)) > 10000:
            return {"error": f"Tool {n} arguments too large (max 10000 chars)", "tool": n}
    except Exception:
        pass
    try:
        mod = _load_module(dotted)
        if not hasattr(mod, "execute"):
            return {"error": f"Tool {n} has no execute function", "tool": n}
        # execute with unpacked args - validates inside tool
        result = mod.execute(**args)  # type: ignore
        # Truncate large outputs
        try:
            s = json.dumps(result, ensure_ascii=False)
            if len(s) > 15000:
                # truncate result content field if present
                if isinstance(result, dict) and "content" in result:
                    result["content"] = str(result["content"])[:10000] + "... truncated"
                elif isinstance(result, dict) and "answer" in result:
                    result["answer"] = str(result["answer"])[:10000] + "... truncated"
        except Exception:
            pass
        return {"tool": n, "arguments": args, "output": result, "success": True}
    except TypeError as e:
        return {"tool": n, "arguments": args, "error": f"Invalid arguments for {n}: {e}", "success": False}
    except FileNotFoundError as e:
        return {"tool": n, "arguments": args, "error": str(e), "success": False}
    except PermissionError as e:
        return {"tool": n, "arguments": args, "error": str(e), "success": False}
    except ValueError as e:
        return {"tool": n, "arguments": args, "error": str(e), "success": False}
    except Exception as e:
        return {"tool": n, "arguments": args, "error": f"Tool {n} execution failed: {e}", "success": False}
