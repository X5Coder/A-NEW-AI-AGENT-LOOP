"""Python 3.10 floor guard.

The console runs on Python 3.10 (install.py MIN_PYTHON), but development
happens on 3.12+. Some f-string syntax is 3.12-only (PEP 701) and parses fine
here while breaking every run on the target machine - that is exactly how a
multi-line f-string in core/agent/context/builder.py turned every chat
request into "unterminated string literal" on Python 3.10.

The 3.12 tokenizer exposes FSTRING_START / FSTRING_END, so we can check the
3.10 restrictions directly instead of relying on ast feature_version (which
does NOT gate f-strings):

  1. a replacement field may not span lines (the bug that shipped),
  2. a field may not contain a backslash,
  3. a non-triple f-string may not nest the same quote char in a field,
  4. a field may not contain a comment.

Stdlib only, no LLM.
"""
from __future__ import annotations

import io
import tokenize
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {"__pycache__", ".venv", "node_modules", "build", "dist",
             "embedding_model", "web/frontend"}

_START = getattr(tokenize, "FSTRING_START", None)
_END = getattr(tokenize, "FSTRING_END", None)
_COMMENT = getattr(tokenize, "COMMENT", tokenize.COMMENT)
_NL = tokenize.NL
_NEWLINE = tokenize.NEWLINE
_OP = tokenize.OP
_STRING = tokenize.STRING


def _python_files():
    for path in sorted(PROJECT.rglob("*.py")):
        parts = set(path.parts)
        if parts & SKIP_DIRS:
            continue
        yield path


def _field_problems(path: Path) -> list[str]:
    """3.10-incompatible f-string constructs in one file (line + reason)."""
    source = io.open(path, encoding="utf-8", errors="replace").read()
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except Exception as exc:  # a file we cannot tokenize is a finding too
        return [f"cannot tokenize: {exc}"]
    found: list[str] = []
    open_stack: list = []
    depth = 0
    for tok in tokens:
        if _START is not None and tok.type == _START:
            open_stack.append((tok, depth))
            depth = 0
            continue
        if _END is not None and tok.type == _END and open_stack:
            first, outer_depth = open_stack.pop()
            depth = outer_depth
            continue
        if not open_stack:
            continue
        if tok.type == _OP and tok.string == "{":
            depth += 1
            continue
        if tok.type == _OP and tok.string == "}":
            depth = max(depth - 1, 0)
            continue
        if depth < 1:
            continue
        first = open_stack[-1][0]
        outer_quote = first.string[-1]
        triple = first.string.count(outer_quote) == 3
        if tok.type in (_NL, _NEWLINE):
            found.append(f"line {first.start[0]}: replacement field spans lines")
            open_stack.pop()  # one report per f-string
        elif tok.type == _COMMENT:
            found.append(f"line {tok.start[0]}: comment inside a replacement field")
        elif tok.type == _STRING:
            body = tok.string.lstrip("rRbBuUfF")
            if (not triple and body[:1] == outer_quote
                    and not body.startswith(outer_quote * 3)):
                found.append(
                    f"line {tok.start[0]}: nested {outer_quote} inside a "
                    f"{outer_quote}-quoted f-string")
            elif "\\" in tok.string:
                found.append(f"line {tok.start[0]}: backslash inside a replacement field")
    return found


def test_fstring_fields_stay_python310_compatible():
    problems = []
    for path in _python_files():
        for problem in _field_problems(path):
            problems.append(f"{path.relative_to(PROJECT)}: {problem}")
    assert not problems, (
        "f-string syntax that needs Python 3.12 (PEP 701) — the 3.10 floor "
        "would fail at import time:\n  " + "\n  ".join(problems))


def test_the_guard_catches_each_python312_only_construct(tmp_path):
    """A future regression must be reported, not silently accepted."""
    cases = {
        "multiline": 'x = f"{\n    1\n}"\n',
        "backslash": 'x = f"{\'\\n\'.join(a)}"\n',
        "nested_quote": 'x = f"{d["k"]}"\n',
        "comment": 'x = f"{a  # note\n}"\n',
    }
    for name, source in cases.items():
        probe = tmp_path / f"probe_{name}.py"
        probe.write_text(source, encoding="utf-8")
        assert _field_problems(probe), f"{name} must be reported"


def test_legal_fstrings_are_not_flagged(tmp_path):
    cases = {
        "field": 'x = f"{a}"\n',
        "escape_in_text": 'x = f"a\\nb{c}"\n',
        "different_quote": 'x = f"{d[\'k\']}"\n',
        "multiline_triple_text": 'x = f"""line one\nline two {a}"""\n',
    }
    for name, source in cases.items():
        probe = tmp_path / f"probe_{name}.py"
        probe.write_text(source, encoding="utf-8")
        assert not _field_problems(probe), f"{name} is valid on 3.10"
