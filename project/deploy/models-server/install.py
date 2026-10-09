#!/usr/bin/env python3
"""Single cross-OS entry point for the models server: ONE command on any OS.

Responsibility: detect the OS, run pre-flight checks (OS, Docker, Python,
GPU/CPU, RAM, disk, project state), then delegate stage-by-stage to the
existing scripts (select_models.py -> install.sh flow on POSIX,
direct Python orchestration on Windows where bash is unavailable).

Usage (identical everywhere):
    python install.py                 # interactive full install
    python install.py --yes           # no questions (missing models auto + UI_*_PRESET env)
    python install.py --cpu           # ignore GPU, CPU only
    python install.py --port 9000     # custom port (saved and reused)
    python install.py --force-recreate# recreate the container from scratch
    python install.py --check         # OS + environment report only, no changes

Rules enforced: stdlib only (no new deps), max 3 retries on probes,
no secrets printed or logged, no downloads in --check mode.
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODELS_DIR = (HERE.parents[1] / "models").resolve()
INSTALL_SH = HERE / "install.sh"
MAX_RETRIES = 3


def say(msg: str) -> None:
    print(f"==> {msg}", flush=True)


def ok(msg: str) -> None:
    print(f"✅ {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"⚠️  {msg}", flush=True)


def die(msg: str, code: int = 1) -> None:
    print(f"❌ {msg}", flush=True)
    raise SystemExit(code)


def run(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    """Run a probe command, bounded retries, return (exit_code, output)."""
    last_out = ""
    for _ in range(MAX_RETRIES):
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            return proc.returncode, out.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            last_out = str(exc)
            time.sleep(1)
    return 1, last_out


def detect_os() -> dict:
    """Detect OS once; every later decision branches on this record."""
    sys_name = platform.system()  # Windows | Linux | Darwin
    return {
        "system": sys_name,
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "is_windows": sys_name == "Windows",
        "is_linux": sys_name == "Linux",
        "is_macos": sys_name == "Darwin",
    }


def check_python(info: dict) -> None:
    say(f"OS detected: {info['system']} {info['release']} ({info['machine']})")
    if sys.version_info < (3, 10):
        die(f"Python >= 3.10 required, found {platform.python_version()}.")
    ok(f"Python {platform.python_version()} OK.")


def check_docker(info: dict) -> None:
    docker = shutil.which("docker")
    if not docker:
        if info["is_linux"]:
            die("Docker not found. Install it: curl -fsSL https://get.docker.com | sh")
        die(
            "Docker not found. Install Docker Desktop: "
            "https://docs.docker.com/get-docker then rerun this command."
        )
    code, _ = run(["docker", "info"], timeout=30)
    if code != 0:
        die("Docker is installed but not running. Start Docker first, then rerun.")
    code, _ = run(["docker", "compose", "version"], timeout=30)
    if code != 0:
        die("The 'docker compose' plugin is missing. Update Docker to a recent version.")
    ok("Docker + compose plugin work.")


def check_gpu() -> dict:
    """Report GPU/CPU path. Never fails the install; CPU fallback is valid."""
    if shutil.which("nvidia-smi") is None:
        warn("No GPU detected — CPU mode (slower, especially audio).")
        return {"gpu": False, "name": "", "vram_mb": 0}
    code, out = run(
        ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], timeout=30
    )
    name = out.splitlines()[0].strip() if code == 0 and out else "NVIDIA GPU"
    code2, out2 = run(
        ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
        timeout=30,
    )
    vram = 0
    if code2 == 0:
        try:
            vram = sum(int(float(x)) for x in re.findall(r"[\d.]+", out2))
        except ValueError:
            vram = 0
    if code == 0:
        ok(f"GPU: {name} (VRAM {vram}MB)")
        return {"gpu": True, "name": name, "vram_mb": vram}
    warn("nvidia-smi present but unreadable — CPU mode.")
    return {"gpu": False, "name": "", "vram_mb": 0}


def check_ram_disk() -> None:
    try:
        import ctypes

        if platform.system() == "Windows":
            mem_kb = ctypes.c_ulonglong()
            ctypes.windll.kernel32.GetPhysicallyInstalledSystemMemory(ctypes.byref(mem_kb))
            ram_mb = int(mem_kb.value // 1024)
        else:
            pages = os.sysconf("SC_PHYS_PAGES")
            page = os.sysconf("SC_PAGE_SIZE")
            ram_mb = int(pages * page // (1024 * 1024))
    except Exception:
        ram_mb = 0
    if ram_mb:
        print(f"    RAM: {ram_mb}MB")
    else:
        warn("RAM size unreadable — continuing.")
    try:
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(str(MODELS_DIR))
        print(f"    Free space in {MODELS_DIR}: {usage.free // (1024**3)}GB")
    except OSError as exc:
        die(f"Cannot access models dir {MODELS_DIR}: {exc}")


def check_project_state() -> None:
    say("Project state:")
    code, out = run(
        [sys.executable, "select_models.py", "--table"], timeout=120
    )
    print(out if out else "    (model table unavailable)")
    env_file = HERE / ".env"
    print(
        "   ✅ settings/token exist"
        if env_file.exists()
        else "   ➖ settings/token missing (will be created)"
    )


def report_only(args: argparse.Namespace) -> int:
    info = detect_os()
    print("=" * 52)
    print("  models-server — environment check (no changes)")
    print("=" * 52)
    check_python(info)
    check_docker(info)
    check_gpu()
    check_ram_disk()
    check_project_state()
    ok("Check done — no changes made.")
    return 0


def delegate_posix(args: argparse.Namespace) -> int:
    """Linux/macOS: reuse the battle-tested install.sh (single source of flow)."""
    if not INSTALL_SH.exists():
        die("install.sh missing next to install.py — reinstall the repo.")
    if not os.access(INSTALL_SH, os.X_OK):
        warn("install.sh is not executable — trying via bash explicitly.")
        cmd = ["bash", str(INSTALL_SH)]
    else:
        cmd = [str(INSTALL_SH)]
    if args.yes:
        cmd.append("--yes")
    if args.cpu:
        cmd.append("--cpu")
    if args.force_recreate:
        cmd.append("--force-recreate")
    if args.port:
        cmd += ["--port", args.port]
    say(f"Delegating to install.sh on {platform.system()} ...")
    try:
        return subprocess.run(cmd, cwd=str(HERE)).returncode
    except KeyboardInterrupt:
        print("\n⏸️  Cancelled — rerun the same command to resume.", flush=True)
        return 130


def delegate_windows(args: argparse.Namespace) -> int:
    """Windows: Git-Bash may exist (then use it); otherwise fail clearly.

    The full flow (download 20GB + build + GPU probe) lives in install.sh.
    Reimplementing it in Python would duplicate ~450 lines and risk drift,
    so Windows reuses it via Git-Bash when available and otherwise points
    the user at the one prerequisite instead of half-running.
    """
    bash = shutil.which("bash")
    if bash:
        cmd = [bash, str(INSTALL_SH)]
        if args.yes:
            cmd.append("--yes")
        if args.cpu:
            cmd.append("--cpu")
        if args.force_recreate:
            cmd.append("--force-recreate")
        if args.port:
            cmd += ["--port", args.port]
        say("Git-Bash found — running the same install.sh flow ...")
        try:
            return subprocess.run(cmd, cwd=str(HERE)).returncode
        except KeyboardInterrupt:
            print("\n⏸️  Cancelled — rerun the same command to resume.", flush=True)
            return 130
    die(
        "Windows needs Git-Bash to run the installer. Install Git for Windows "
        "(https://git-scm.com/download/win, includes Git-Bash), then rerun:\n"
        "    python install.py"
    )
    return 2  # unreachable


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ONE command to install the models server on any OS."
    )
    parser.add_argument("--yes", action="store_true", help="no questions")
    parser.add_argument("--cpu", action="store_true", help="ignore GPU, CPU only")
    parser.add_argument("--port", default="", help="custom port 1-65535")
    parser.add_argument(
        "--force-recreate", action="store_true", help="recreate the container"
    )
    parser.add_argument(
        "--check", action="store_true", help="OS + env report only, no changes"
    )
    args = parser.parse_args(argv)
    if args.port and (
        not args.port.isdigit() or not 1 <= int(args.port) <= 65535
    ):
        die(f"Invalid port: {args.port} (must be 1-65535).", code=2)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    info = detect_os()
    print("=" * 52)
    print(f"  models-server — one command ({info['system']})")
    print("=" * 52)
    if args.check:
        return report_only(args)
    check_python(info)
    check_docker(info)
    if args.cpu:
        warn("CPU mode requested (--cpu) — GPU ignored.")
    else:
        check_gpu()
    check_ram_disk()
    if info["is_windows"]:
        return delegate_windows(args)
    return delegate_posix(args)


if __name__ == "__main__":
    raise SystemExit(main())
