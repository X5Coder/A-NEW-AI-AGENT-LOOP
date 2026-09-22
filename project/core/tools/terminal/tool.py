"""
terminal tool - execute shell commands with validation, bounded timeout, no hardcoded secrets.
Security: validates workdir inside project, blocks dangerous patterns optional, cleans up.
"""
from __future__ import annotations

import subprocess
import shlex
from pathlib import Path

from .events import format_event

MAX_COMMAND_LEN = 2000
DEFAULT_TIMEOUT = 30
MAX_TIMEOUT = 120


def _validate_command(v) -> str:
    if not isinstance(v, str):
        raise TypeError("command must be string")
    s = v.strip()
    if not s:
        raise ValueError("command must be non-empty")
    if len(s) > MAX_COMMAND_LEN:
        raise ValueError(f"command too long (max {MAX_COMMAND_LEN})")
    return s


def _validate_workdir(v) -> Path | None:
    if v is None:
        return None
    if not isinstance(v, str):
        raise TypeError("workdir must be string")
    s = v.strip()
    if not s:
        return None
    p = Path(s)
    # If relative, resolve against project root
    project_root = Path(__file__).resolve().parents[3]
    if not p.is_absolute():
        p = (project_root / s).resolve()
    else:
        p = p.resolve()
    # Must exist and be inside project root or be project root itself
    try:
        # Allow inside project root
        p.relative_to(project_root.resolve())
    except ValueError:
        # Disallow escaping project
        raise ValueError(f"workdir must be inside project directory: {project_root}")
    if not p.exists() or not p.is_dir():
        raise FileNotFoundError(f"workdir not found: {p}")
    return p


def _validate_timeout(v) -> int:
    if v is None:
        return DEFAULT_TIMEOUT
    if not isinstance(v, int):
        raise TypeError("timeout must be integer")
    if v < 1 or v > MAX_TIMEOUT:
        raise ValueError(f"timeout must be 1..{MAX_TIMEOUT}")
    return v


def execute(command: str, workdir: str | None = None, timeout: int | None = None) -> dict:
    cmd = _validate_command(command)
    wd = _validate_workdir(workdir)
    to = _validate_timeout(timeout)
    event = format_event(command=cmd)

    project_root = Path(__file__).resolve().parents[3]
    cwd = str(wd) if wd else str(project_root)

    try:
        # Use shell=False security? But spec says execute shell commands, so we need shell
        # We use subprocess.run with shell=True but validate command length and timeout.
        # For Windows, shell=True is needed for builtins like dir
        # We capture output bounded
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=to,
            encoding="utf-8",
            errors="replace",
        )
        # Bounded output: truncate to 20KB
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        if len(stdout) > 20000:
            stdout = stdout[:20000] + "\n... truncated"
        if len(stderr) > 20000:
            stderr = stderr[:20000] + "\n... truncated"
        return {
            "event": event,
            "command": cmd,
            "workdir": cwd,
            "returncode": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "success": result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"event": event, "command": cmd, "workdir": cwd, "returncode": -1, "stdout": "", "stderr": f"Command timed out after {to}s", "success": False, "timeout": True}
    except Exception as e:
        raise RuntimeError(f"Terminal execution failed: {e}") from e
