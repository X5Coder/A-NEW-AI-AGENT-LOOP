"""
wait tool - core tool to pause loop execution for specified duration.
Validates input, bounded, then sleeps.
"""
from __future__ import annotations

import time
from typing import Any

from .events import format_event

MAX_DURATION = 300
MIN_DURATION = 1


def _validate_duration(v: Any) -> int:
    if isinstance(v, bool):
        raise TypeError("duration must be integer")
    if not isinstance(v, int):
        # allow float that is integer-like? but spec says integer
        try:
            v = int(v)
        except Exception:
            raise TypeError("duration must be integer")
    if v < MIN_DURATION or v > MAX_DURATION:
        raise ValueError(f"duration must be between {MIN_DURATION} and {MAX_DURATION} seconds")
    return v


def _validate_reason(v: Any) -> str | None:
    if v is None:
        return None
    if not isinstance(v, str):
        raise TypeError("reason must be string")
    s = v.strip()
    if not s:
        return None
    if len(s) > 500:
        raise ValueError("reason too long (max 500)")
    return s


def execute(duration: int, reason: str | None = None) -> dict:
    d = _validate_duration(duration)
    r = _validate_reason(reason)
    event = format_event(duration=d)
    # Actually sleep - bounded, interruptible by loop's overall timeout
    start = time.time()
    time.sleep(d)
    elapsed = time.time() - start
    return {
        "event": event,
        "duration": d,
        "reason": r,
        "elapsed": round(elapsed, 2),
        "success": True,
        "message": f"Waited {d}s" + (f" for: {r}" if r else "")
    }
