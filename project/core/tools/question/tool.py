"""
question tool - asks user and waits for response.
Ownership: core/tools/question, clear lifecycle, validates inputs, bounded.
"""
from __future__ import annotations

from typing import Any

from .events import format_event


def _validate_question(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError("question must be string")
    v = value.strip()
    if not v:
        raise ValueError("question must be non-empty")
    if len(v) > 2000:
        raise ValueError("question too long (max 2000)")
    return v


def _validate_options(value: Any) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise TypeError("options must be array<string>")
    if len(value) > 4:
        raise ValueError("options max 4 choices")
    if len(value) == 0:
        raise ValueError("options must not be empty if provided")
    cleaned: list[str] = []
    for o in value:
        if not isinstance(o, str):
            raise TypeError("each option must be string")
        s = o.strip()
        if not s:
            raise ValueError("option must be non-empty")
        if len(s) > 200:
            raise ValueError("option too long (max 200)")
        cleaned.append(s)
    # uniqueness
    if len(set(cleaned)) != len(cleaned):
        raise ValueError("options must be unique")
    return cleaned


def execute(question: str, options: list[str] | None = None) -> dict:
    """
    Validate inputs and return structured request.
    In real gateway, this would pause loop and wait for user response.
    Here we return the event + payload for caller to handle.
    """
    q = _validate_question(question)
    opts = _validate_options(options)
    event = format_event(question=q)
    result: dict[str, Any] = {
        "event": event,
        "question": q,
        "status": "waiting_for_user",
    }
    if opts is not None:
        result["options"] = opts
    else:
        result["options"] = None
    return result
