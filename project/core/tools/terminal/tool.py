"""
terminal tool - execute shell commands.

Access scope (NO approval question in either scope — commands run immediately):
- limited_access (DEFAULT): jailed inside X5Agent/Workspace only; any escape
  attempt outside Workspace is blocked.
- full_access: any command on the whole machine runs normally.

Scope file: X5Agent/Data/database/terminal_access.json = {"scope": ...}.
Set from the Agent permission menu (Restricted/Limited vs Full access).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

from .events import format_event

MAX_COMMAND_LEN = 2000
DEFAULT_TIMEOUT = 30
MAX_TIMEOUT = 120

LIMITED = "limited_access"
FULL = "full_access"


def _access_scope() -> str:
    """Current terminal scope: full_access or limited_access (default).

    Reads X5Agent/Data/database/terminal_access.json; anything missing or
    invalid falls back to limited_access. Never raises.
    """
    try:
        import sys as _sys
        proj = Path(__file__).resolve().parents[3]
        if str(proj) not in _sys.path:
            _sys.path.insert(0, str(proj))
        from core.paths import data_db_file as _ddf
        raw = json.loads(_ddf("terminal_access.json").read_text(encoding="utf-8"))
        if isinstance(raw, dict) and raw.get("scope") == FULL:
            return FULL
    except Exception:
        pass
    return LIMITED


def _kill_tree(pid: int) -> None:
    """Kill a process AND its children (a detached server must never linger).

    Never raises: killing is best-effort cleanup.
    """
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=15)
            return
    except Exception:
        pass
    try:
        os.kill(pid, 9)
    except Exception:
        pass


def _read_bounded(path: Path) -> str:
    """Read one captured stream file, bounded to 20KB. Never raises."""
    try:
        data = path.read_bytes()
    except Exception:
        return ""
    text = data.decode("utf-8", errors="replace")
    if len(text) > 20000:
        text = text[:20000] + "\n... truncated"
    return text


def _workspace_root() -> Path:
    # Try shared resolver, fallback to local parents computation (never breaks imports).
    try:
        import sys as _sys
        proj = Path(__file__).resolve().parents[3]
        if str(proj) not in _sys.path:
            _sys.path.insert(0, str(proj))
        from core.paths import workspace_root as _ws
        ws = _ws()
        try:
            ws.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return ws.resolve()
    except Exception:
        repo = Path(__file__).resolve().parents[4]
        ws = repo / "X5Agent" / "Workspace"
        try:
            ws.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return ws.resolve()


def _validate_command(v) -> str:
    if not isinstance(v, str):
        raise TypeError("command must be string")
    s = v.strip()
    if not s:
        raise ValueError("command must be non-empty")
    if len(s) > MAX_COMMAND_LEN:
        raise ValueError(f"command too long (max {MAX_COMMAND_LEN})")
    if _access_scope() == FULL:
        return s  # full access: any command on the whole machine, no question
    # limited_access (default) jail: block any escape attempt outside Workspace.
    low = s.lower()
    ws_str = str(_workspace_root()).lower()
    if ".." in s:
        raise ValueError("Blocked: outside Workspace (.. not allowed). Workspace only.")
    # Block absolute paths that are not inside Workspace (Windows drive or unix root).
    # Find drive-letter absolutes like C:\ or C:/ in the command.
    for m in re.finditer(r"[A-Za-z]:[\\/]", s):
        # allow only if the Workspace path itself appears in the command
        if ws_str not in low:
            raise ValueError("Blocked: absolute path outside Workspace. Workspace only.")
            break
    # Block UNC network paths (\\server\share) - never inside Workspace.
    if re.search(r"(^|[\s;&|\"'])\\\\[A-Za-z0-9]", s):
        raise ValueError("Blocked: network path outside Workspace. Workspace only.")
    # Block unix-style absolutes outside Workspace (/etc, /home, ...).
    # The Workspace itself never lives under these roots, so any match is an escape.
    if re.search(r"(^|[\s;&|\"'/])-?/(etc|bin|usr|home|root|tmp|var|opt|windows|users|programdata)\b", low):
        raise ValueError("Blocked: absolute path outside Workspace. Workspace only.")
    # Block home-dir (~) and env/shell expansion ($VAR, ${VAR}, %VAR%, backticks, $(...)).
    # shell=True would expand these outside the jail, so they are forbidden.
    if re.search(r"(^|[\s;&|\"'=])~([\\/]|$|\s)", s):
        raise ValueError("Blocked: home-dir expansion outside Workspace. Workspace only.")
    if ("$" in s or "`" in s or re.search(r"%[A-Za-z_]+%", s)):
        raise ValueError("Blocked: env/shell expansion not allowed. Use plain Workspace-relative paths.")
    # Block obvious escape cd commands without Workspace context.
    escape_cmds = ["cd /", "cd \\", "cd..", "cd ..", "chdir ..", "pushd ..", "popd"]
    for ec in escape_cmds:
        if ec in low and ws_str not in low:
            raise ValueError("Blocked: outside Workspace (cd escape). Workspace only.")
    return s


def _validate_workdir(v) -> Path | None:
    ws = _workspace_root()
    if _access_scope() == FULL:
        # full access: any folder on the machine (default cwd = Workspace).
        if v is None:
            return ws
        if not isinstance(v, str):
            raise TypeError("workdir must be string")
        s = v.strip()
        if not s:
            return ws
        p = Path(s)
        p = p.resolve() if p.is_absolute() else (ws / s).resolve()
        if not p.exists() or not p.is_dir():
            raise FileNotFoundError(f"workdir not found: {p}")
        return p
    if v is None:
        return ws
    if not isinstance(v, str):
        raise TypeError("workdir must be string")
    s = v.strip()
    if not s:
        return ws
    p = Path(s)
    # If relative, resolve against Workspace (model device), NOT project root.
    if not p.is_absolute():
        p = (ws / s).resolve()
    else:
        p = p.resolve()
    # Must be inside Workspace or be Workspace itself - nothing else.
    try:
        p.relative_to(ws)
    except ValueError:
        raise ValueError(f"Blocked: workdir must be inside Workspace: {ws}") from None
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

    ws = _workspace_root()
    cwd = str(wd) if wd else str(ws)

    # Output goes to FILES, never pipes. A long-running/server command detaches
    # a grandchild that inherits the stdout handle; with pipes that handle stays
    # open after the timeout, so subprocess would block forever waiting to close
    # it and the whole agent loop would hang. Files have no such handle.
    tmpdir = Path(tempfile.mkdtemp(prefix="x5term_"))
    out_path = tmpdir / "stdout.txt"
    err_path = tmpdir / "stderr.txt"
    timed_out = False
    returncode = -1
    try:
        with open(out_path, "wb") as out_f, open(err_path, "wb") as err_f:
            popen_kwargs: dict = {}
            if os.name == "nt":
                popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            proc = subprocess.Popen(
                cmd,
                shell=True,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=out_f,
                stderr=err_f,
                **popen_kwargs,
            )
            try:
                proc.wait(timeout=to)
                returncode = proc.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_tree(proc.pid)
                try:
                    proc.wait(timeout=10)
                except Exception:
                    pass

        stdout = _read_bounded(out_path)
        stderr = _read_bounded(err_path)
        if timed_out:
            note = (
                f"Command still running after {to}s and was stopped. Long-running "
                "commands (servers, watchers) must be started DETACHED with their "
                "output redirected to a file, e.g. "
                'start "" /B cmd /c "python -m http.server 8123 > server.log 2>&1" '
                "then read server.log to check it."
            )
            stderr = ((stderr + "\n") if stderr else "") + note
            stderr = stderr[:20000]
        return {
            "event": event,
            "command": cmd,
            "workdir": cwd,
            "returncode": returncode,
            "stdout": stdout,
            "stderr": stderr,
            "success": (not timed_out) and returncode == 0,
            "timeout": timed_out,
        }
    except Exception as e:
        raise RuntimeError(f"Terminal execution failed: {e}") from e
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
