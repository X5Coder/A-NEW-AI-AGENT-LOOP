"""Event formatting for scheduler."""
from __future__ import annotations

def _short_schedule(repeat: str | None, start_at: str | None, repeat_count: int | None) -> str:
    if repeat:
        r = repeat.strip()
        if len(r) > 20:
            r = r[:17] + "..."
        if repeat_count:
            return f"{r} ×{repeat_count}"
        return r
    if start_at and start_at.strip().lower() != "now":
        s = start_at.strip()
        if len(s) > 20:
            s = s[:17] + "..."
        return s
    if repeat_count:
        return f"{repeat_count} times"
    return "now"

def format_event(repeat: str | None = None, start_at: str | None = None, repeat_count: int | None = None, action: str | None = None, **_) -> str:
    short = _short_schedule(repeat, start_at, repeat_count)
    return f"Scheduling · {short}"
