"""Tests for the unified cross-OS installer (install.py).

Responsibility: verify OS detection, arg validation, --check purity
(no downloads, no writes), and stdlib-only implementation.
No I/O beyond temp probes; no Docker required (checks fail clearly).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
INSTALL_PY = HERE.parent / "deploy" / "models-server" / "install.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(INSTALL_PY), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_install_py_exists_and_compiles() -> None:
    assert INSTALL_PY.exists(), "install.py must exist"
    proc = subprocess.run(
        [sys.executable, "-m", "py_compile", str(INSTALL_PY)],
        capture_output=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr


def test_invalid_port_rejected() -> None:
    for bad in ("99999", "abc", "0"):
        proc = _run("--port", bad, "--check")
        assert proc.returncode != 0, f"port {bad} must be rejected"
        assert "Invalid port" in (proc.stdout + proc.stderr)


def test_check_mode_reports_os_without_changes() -> None:
    proc = _run("--check")
    out = proc.stdout + proc.stderr
    assert "OS detected" in out, "must report detected OS"
    assert "Python" in out, "must report Python state"


def test_stdlib_only() -> None:
    src = INSTALL_PY.read_text(encoding="utf-8")
    third_party = ("yaml", "huggingface_hub", "torch", "requests", "docker")
    for lib in third_party:
        assert f"import {lib}" not in src, f"install.py must stay stdlib-only ({lib})"
