from pathlib import Path
def format_event(video_path: str | None = None, video_id: str | None = None, job_id: str | None = None, **_) -> str:
    if job_id:
        n = job_id[:8] if len(job_id) > 8 else job_id
        return f"Checking transcribe job · {n}"
    name = ""
    if video_path:
        name = Path(video_path).name
    elif video_id:
        name = video_id
    else:
        name = "video"
    if len(name) > 25:
        name = name[:22] + "..."
    return f"Transcribing video · {name}"
