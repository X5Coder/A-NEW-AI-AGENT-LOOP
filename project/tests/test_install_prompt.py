"""Installer prompt safety: ordered output + a clean, visible owner prompt.

Locks the flow the user sees: every heavy step (dependencies, embedding
download, browser check, firewall) finishes first, then the owner email +
password prompt appears on a clean screen. Block-buffered stdout used to
flush AFTER the prompt, so the download output looked like it swallowed the
prompt and the email question "disappeared".
"""
from __future__ import annotations

import ast
import io
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INSTALL_PY = PROJECT_ROOT / "install.py"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import install  # noqa: E402  (path set above)


class _FakeTerminal(io.StringIO):
    """StringIO that can pretend to be (or not be) a terminal."""

    def __init__(self, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:  # type: ignore[override]
        return self._tty


def _run_with_stdout(fake: io.StringIO, fn) -> None:
    original = sys.stdout
    sys.stdout = fake  # type: ignore[assignment]
    try:
        fn()
    finally:
        sys.stdout = original


# --- ordered output -------------------------------------------------------

def test_enable_ordered_output_turns_on_line_buffering():
    stream = io.TextIOWrapper(io.BytesIO(), line_buffering=False)
    original = sys.stdout
    sys.stdout = stream  # type: ignore[assignment]
    try:
        install.enable_ordered_output()
        assert stream.line_buffering is True
    finally:
        sys.stdout = original


def test_helpers_print_with_flush():
    """Step/log helpers must not sit in a buffer while a prompt renders."""
    source = INSTALL_PY.read_text(encoding="utf-8")
    for helper in ("def _step(", "def _ok(", "def _warn(", "def _fail("):
        body = source[source.index(helper):]
        body = body[:body.index("\n\n\n", 0) if "\n\n\n" in body else len(body)]
        assert "flush=True" in body, f"{helper} must flush"


# --- clean prompt screen --------------------------------------------------

def test_clear_screen_wipes_terminal_and_scrollback():
    fake = _FakeTerminal(tty=True)
    _run_with_stdout(fake, install._clear_screen)
    out = fake.getvalue()
    assert "\033[2J" in out, "visible screen must be cleared"
    assert "\033[3J" in out, "scrollback must be cleared (long \\r progress bars)"


def test_clear_screen_safe_without_a_terminal():
    fake = _FakeTerminal(tty=False)
    _run_with_stdout(fake, install._clear_screen)
    out = fake.getvalue()
    assert "\033" not in out, "no escape codes when stdout is not a terminal"
    assert out.strip() == "", "fallback is blank lines only"


# --- flow order -----------------------------------------------------------

def test_owner_prompt_is_the_last_thing_before_launch():
    """Download/resources/firewall finish first; the console starts after."""
    source = INSTALL_PY.read_text(encoding="utf-8")
    main_body = source[source.index("def main("):]
    resources = main_body.index("ensure_resources(")
    firewall = main_body.index("ensure_firewall(")
    prompt = main_body.index("ask_credentials(")
    launch = main_body.index("launch_detached")
    assert resources < prompt, "embedding download must finish before the prompt"
    assert firewall < prompt, "firewall step must finish before the prompt"
    assert prompt < launch, "the console starts only after the owner is set"


def test_prompt_line_survives_a_pipe_and_shows_the_step():
    """ask_credentials clears the screen, prints 6/6, then waits on stdin."""
    source = INSTALL_PY.read_text(encoding="utf-8")
    body = source[source.index("def ask_credentials("):]
    body = body[:body.index("\ndef ", 10)]
    assert "_clear_screen()" in body
    assert '"6/6 console owner"' in body
    assert body.index("_clear_screen()") < body.index('input("  email: ")')
    assert body.index('input("  email: ")') < body.index("_read_password()")


# --- installer must run before dependencies exist -------------------------

def test_installer_imports_stdlib_only():
    """Top-level imports must be stdlib: the installer runs before deps exist.

    Project modules (core/scripts/web) and optional heavy deps (torch,
    x5browser) are imported lazily inside functions on purpose, so only the
    module scope is checked.
    """
    stdlib = getattr(sys, "stdlib_module_names", None)
    if not stdlib:
        pytest.skip("stdlib_module_names needs python 3.10+")
    tree = ast.parse(INSTALL_PY.read_text(encoding="utf-8"))
    roots = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    external = sorted(roots - stdlib - {"__future__"})
    assert external == [], f"top-level imports must be stdlib: {external}"
