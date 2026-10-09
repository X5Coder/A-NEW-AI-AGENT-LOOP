"""
Reader for models_prompts.md — single file, one `## KIND: <kind>` section
per model kind (text/vision/image/tts/stt).

Single responsibility: split the file into per-kind prompt texts.
The server serves one section per dialog; the copy button copies it verbatim.
"""
from __future__ import annotations

import re
from pathlib import Path

FILE_NAME = "models_prompts.md"
KINDS = ("text", "vision", "image", "tts", "stt")

_KIND_RE = re.compile(r"^##\s*KIND:\s*([A-Za-z0-9_-]+)\s*$")


def _file_path() -> Path:
    return Path(__file__).resolve().parent / FILE_NAME


def read_all() -> dict:
    """Returns {kind: section_text} (header line excluded). Missing kinds absent."""
    try:
        text = _file_path().read_text(encoding="utf-8")
    except Exception:
        return {}
    out: dict = {}
    current: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        m = _KIND_RE.match(line.strip())
        if m:
            if current is not None:
                out[current] = "\n".join(buf).strip()
            current = m.group(1).strip().lower()
            buf = []
            continue
        if current is not None:
            buf.append(line)
    if current is not None:
        out[current] = "\n".join(buf).strip()
    return out


def get_prompt(kind: str) -> str:
    """Section text for one kind, "" when absent. Falls back to text."""
    kind = str(kind or "text").strip().lower()
    sections = read_all()
    if kind in sections and sections[kind]:
        return sections[kind]
    if sections.get("text"):
        return sections["text"]
    return ""
