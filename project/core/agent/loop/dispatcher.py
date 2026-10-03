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
    "trigger_search": "core.tools.trigger_search.tool",
    "trigger": "core.tools.trigger.tool",
    "question": "core.tools.question.tool",
    "vector_chat_search": "core.tools.vector_chat_search.tool",
    "document_search": "core.tools.document_search.tool",
    "todo_list": "core.tools.todo_list.tool",
    "scheduler": "core.tools.scheduler.tool",
    "skills_search": "core.tools.skills_search.tool",
    "skills_view": "core.tools.skills_view.tool",
    "terminal": "core.tools.terminal.tool",
    "wait": "core.tools.wait.tool",
    "workspace": "core.tools.workspace.tool",
}

_DISCOVERABLE_MAP = {
    "read_file": "plugins.core_plugins.read_file.tool",
    "write_file": "plugins.core_plugins.write_file.tool",
    "search_files": "plugins.core_plugins.search_files.tool",
    "vision_analyze": "plugins.core_plugins.vision_analyze.tool",
    "video_analyze": "plugins.core_plugins.video_analyze.tool",
    "video_transcribe": "plugins.core_plugins.video_transcribe.tool",
    "skill_create": "plugins.core_plugins.skill_create.tool",
    "image_memory": "plugins.core_plugins.image_memory.tool",
    "important_memory": "plugins.core_plugins.important_memory.tool",
}

_ALL = {**_CORE_MAP, **_DISCOVERABLE_MAP}


def _browser_tool_names() -> set[str]:
    """Browser tools owned by project/X5Browser/catalog.py. Empty when unavailable."""
    try:
        from X5Browser.catalog import ALL_TOOLS
        return set(ALL_TOOLS)
    except Exception:
        return set()


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
    _browser_names = _browser_tool_names()
    if n not in _ALL and n not in _browser_names:
        # Try discover additional plugins dynamically by scanning storage/vector? But just error
        return {"error": f"Unknown tool: {n}. Available: {sorted(list(_ALL.keys()) + sorted(_browser_names))}", "tool": n}
    dotted = _ALL.get(n, "")
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
    if n in _DISCOVERABLE_MAP:
        # Core plugins are always on (no switches, hidden from UI) — never gated.
        from core.tools.registry import get_plugin_state, is_core_plugin, is_plugin_effective

        if not is_core_plugin(n):
            # Disabled plugins (or disabled actions) are blocked even if called directly
            if not is_plugin_effective(n):
                return {"tool": n, "arguments": args, "error": f"Plugin disabled: {n}", "success": False}
            act = args.get("action")
            if isinstance(act, str):
                perms = get_plugin_state().get("plugins", {}).get(n, {}).get("permissions", {})
                if perms.get(act) is False:
                    return {"tool": n, "arguments": args, "error": f"Permission disabled: {n}.{act}", "success": False}
    if n in _browser_names:
        # All browser tools are core plugins: always on, hidden from UI, never gated.
        # Dangerous ones still ask in restricted mode (handled in engine).
        try:
            from X5Browser.tools import execute_browser_tool
            output = execute_browser_tool(n, args)
        except (TypeError, ValueError) as e:
            return {"tool": n, "arguments": args, "error": f"Invalid arguments for {n}: {e}", "success": False}
        except Exception as e:
            return {"tool": n, "arguments": args, "error": f"Tool {n} execution failed: {e}", "success": False}
        # Propagate the inner ok so callers (engine pause, server control)
        # see real failures instead of a masked success.
        inner_ok = True
        try:
            if isinstance(output, dict) and "ok" in output:
                inner_ok = bool(output.get("ok"))
        except Exception:
            inner_ok = True
        try:
            s = json.dumps(output, ensure_ascii=False)
            if len(s) > 15000:
                if isinstance(output, dict) and "observation" in output:
                    output["observation"] = str(output["observation"])[:10000] + "... truncated"
                elif isinstance(output, dict) and "text" in output:
                    output["text"] = str(output["text"])[:10000] + "... truncated"
        except Exception:
            pass
        if not inner_ok:
            err = ""
            try:
                if isinstance(output, dict):
                    err = str(output.get("error") or output.get("message") or "")[:500]
            except Exception:
                err = ""
            return {"tool": n, "arguments": args, "output": output,
                    "error": err or f"Tool {n} failed", "success": False}
        return {"tool": n, "arguments": args, "output": output, "success": True}
    if not dotted:
        return {"error": f"Unknown tool: {n}", "tool": n}
    try:
        mod = _load_module(dotted)
        if not hasattr(mod, "execute"):
            return {"error": f"Tool {n} has no execute function", "tool": n}
        # Optional reason is loop-level (shown before the call); only forward it
        # when the tool accepts it, otherwise drop it so old tools keep working.
        call_args = args
        if isinstance(args, dict) and "reason" in args:
            try:
                import inspect as _inspect
                _params = _inspect.signature(mod.execute).parameters
                _ok = ("reason" in _params) or any(
                    p.kind == _inspect.Parameter.VAR_KEYWORD for p in _params.values())
            except Exception:
                _ok = False
            if not _ok:
                call_args = {k: v for k, v in args.items() if k != "reason"}
        # execute with unpacked args - validates inside tool
        result = mod.execute(**call_args)  # type: ignore
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
