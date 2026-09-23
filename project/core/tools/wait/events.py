def format_event(duration: int = 0, **_) -> str:
    try:
        d = int(duration)
    except Exception:
        d = 0
    if d <= 0:
        return "Waiting"
    if d < 60:
        return f"Waiting · {d}s"
    m = d // 60
    s = d % 60
    if s == 0:
        return f"Waiting · {m}m"
    return f"Waiting · {m}m {s}s"
