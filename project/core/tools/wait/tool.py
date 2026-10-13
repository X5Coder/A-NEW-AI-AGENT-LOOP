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


def execute(duration: int, cancel_flag=None) -> dict:
    d = _validate_duration(duration)
    event = format_event(duration=d)
    # Bounded sleep in small slices so STOP interrupts the wait at once
    # (the loop breaks right after). The dispatcher forwards the loop's
    # cancel event here automatically. Never raises on cancel.
    start = time.time()
    try:
        _evt = cancel_flag
        _end = start + d
        while True:
            if _evt is not None and getattr(_evt, "is_set", lambda: False)():
                break
            _left = _end - time.time()
            if _left <= 0:
                break
            time.sleep(min(0.25, _left))
    except Exception:
        pass
    elapsed = time.time() - start
    _interrupted = bool(
        cancel_flag is not None and getattr(cancel_flag, "is_set", lambda: False)())
    return {
        "event": event,
        "duration": d,
        "elapsed": round(elapsed, 2),
        "success": True,
        "interrupted": _interrupted,
        "message": (f"Wait interrupted by STOP after {elapsed:.1f}s"
                    if _interrupted else f"Waited {d}s"),
    }
