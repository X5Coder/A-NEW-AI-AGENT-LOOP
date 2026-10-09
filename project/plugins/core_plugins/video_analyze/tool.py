"""
video_analyze - async video frame-by-frame analysis via the connected AI Vision Model.
Each operation has job_id, model polls every wait period. Frames analyzed concurrently via ThreadPool for speed.
Video not saved, only metadata sent to text model.
"""
from __future__ import annotations

import json
import time
import uuid
import tempfile
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

from .events import format_event

def _storage_path() -> Path:
    # Unified: job state ALWAYS in X5Agent/Data (zip backup). Fallback legacy only if resolver missing.
    try:
        import sys as _sys
        _proj = Path(__file__).resolve().parents[3]
        if str(_proj) not in _sys.path:
            _sys.path.insert(0, str(_proj))
        from core.paths import data_db_file
        return data_db_file("video_analyze_jobs.json")
    except Exception:
        return Path(__file__).resolve().parents[3] / "storage" / "database" / "video_analyze_jobs.json"


STORAGE = _storage_path()
_STORAGE_LEGACY = Path(__file__).resolve().parents[3] / "storage" / "database" / "video_analyze_jobs.json"


def _media_bases(project_root: Path) -> list[Path]:
    # Media may live in Data/files (unified) or legacy storage/files. Data first.
    bases: list[Path] = []
    try:
        import sys as _sys2
        if str(project_root) not in _sys2.path:
            _sys2.path.insert(0, str(project_root))
        from core.paths import data_subdir as _ds
        bases.append((_ds("files", "videos")).resolve())
        bases.append((_ds("files", "images")).resolve())
    except Exception:
        pass
    bases.append((project_root / "storage" / "files" / "videos").resolve())
    bases.append((project_root / "storage" / "files" / "images").resolve())
    return bases


def _is_inside(cand: Path, root: Path) -> bool:
    try:
        cand.relative_to(root)
        return True
    except ValueError:
        return False
MAX_VIDEO_SIZE = 50 * 1024 * 1024
MAX_JOBS = 50
_LOCK = threading.Lock()


def _load_jobs() -> list[dict]:
    for cand in (STORAGE, _STORAGE_LEGACY):
        if not cand.exists():
            continue
        try:
            data = json.loads(cand.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            continue
    return []

def _save_jobs(jobs: list[dict]) -> None:
    STORAGE.parent.mkdir(parents=True, exist_ok=True)
    STORAGE.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")

def _workspace_base() -> Path | None:
    try:
        import sys as _sysw
        _pw = Path(__file__).resolve().parents[3]
        if str(_pw) not in _sysw.path:
            _sysw.path.insert(0, str(_pw))
        from core.paths import workspace_root as _ws
        return _ws().resolve()
    except Exception:
        return None


def _validate_video_path(p: str | None, vid: str | None) -> Path:
    if p and vid:
        raise ValueError("Provide only one of video_path or video_id")
    if not p and not vid:
        raise ValueError("At least one of video_path or video_id required")
    project_root = Path(__file__).resolve().parents[3]
    ws_base = _workspace_base()
    if p:
        cand = Path(p)
        if not cand.is_absolute():
            # Workspace-relative first (video_download saves there), then project-relative.
            if ws_base is not None:
                ws_cand = (ws_base / p).resolve()
                try:
                    ws_cand.relative_to(ws_base)
                    if ws_cand.exists():
                        cand = ws_cand
                    else:
                        cand = (project_root / p).resolve()
                except ValueError:
                    cand = (project_root / p).resolve()
            else:
                cand = (project_root / p).resolve()
        else:
            cand = cand.resolve()
        allowed = [project_root.resolve()] + _media_bases(project_root)
        if ws_base is not None:
            allowed.append(ws_base)
        if not any(_is_inside(cand, r) for r in allowed):
            raise ValueError(f"video_path must be inside Workspace, project or Data/files")
        if not cand.exists():
            raise FileNotFoundError(f"Video not found: {p}")
        if cand.stat().st_size > MAX_VIDEO_SIZE:
            raise ValueError(f"Video too large (max {MAX_VIDEO_SIZE})")
        if cand.suffix.lower() not in (".mp4",".avi",".mov",".mkv",".webm",".flv",".mpg",".mpeg",".m4v"):
            raise ValueError(f"Unsupported video type: {cand.suffix}")
        return cand
    else:
        # video_id -> storage/files/videos/{id} or images?
        assert vid is not None
        if "/" in vid or "\\" in vid or ".." in vid:
            raise ValueError("Invalid video_id")
        # try videos folder then images (Data first, legacy fallback)
        for base in _media_bases(project_root):
            cand = (base / vid).resolve()
            try:
                cand.relative_to(base)
            except ValueError:
                continue
            if cand.exists():
                return cand
        raise FileNotFoundError(f"Video id not found: {vid}")

def _extract_metadata(video_path: Path) -> dict:
    """Use ffmpeg to get metadata, fallback to basic."""
    meta = {"path": str(video_path), "size": video_path.stat().st_size, "type": video_path.suffix.lower().lstrip(".")}
    try:
        # Use ffprobe if available, else ffmpeg -i
        result = subprocess.run(
            ["ffmpeg", "-i", str(video_path)],
            capture_output=True, text=True, timeout=10
        )
        out = result.stderr or ""
        # Parse duration
        import re
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", out)
        if m:
            h, mm, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
            meta["duration_seconds"] = h*3600 + mm*60 + s
        m2 = re.search(r"(\d+)x(\d+)", out)
        if m2:
            meta["width"] = int(m2.group(1))
            meta["height"] = int(m2.group(2))
        m3 = re.search(r"(\d+(?:\.\d+)?)\s*fps", out)
        if m3:
            meta["fps"] = float(m3.group(1))
        # Estimate frame count
        if "duration_seconds" in meta and "fps" in meta:
            meta["frame_count"] = int(meta["duration_seconds"] * meta["fps"])
    except Exception:
        pass
    # Fallback defaults
    meta.setdefault("duration_seconds", 0)
    meta.setdefault("fps", 30)
    meta.setdefault("width", 0)
    meta.setdefault("height", 0)
    meta.setdefault("frame_count", 0)
    return meta

def _extract_frames(video_path: Path, max_frames: int = 12) -> list[Path]:
    """Extract frames via ffmpeg at 1 fps, up to max_frames, to temp dir."""
    tmpdir = Path(tempfile.mkdtemp(prefix="video_frames_"))
    try:
        # Extract at 1 fps
        pattern = str(tmpdir / "frame_%04d.jpg")
        cmd = ["ffmpeg", "-y", "-i", str(video_path), "-vf", "fps=1", "-q:v", "2", "-frames:v", str(max_frames), pattern]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            # fallback: try without fps filter, just extract first frame
            cmd2 = ["ffmpeg", "-y", "-i", str(video_path), "-vframes", "1", str(tmpdir / "frame_0001.jpg")]
            subprocess.run(cmd2, capture_output=True, timeout=30)
        frames = sorted(tmpdir.glob("frame_*.jpg"))
        # If still no frames, return empty
        return frames
    except Exception:
        return []

def _analyze_frame(frame_path: Path, prompt: str) -> dict:
    """Analyze single frame via the connected AI Vision Model."""
    try:
        from core.ai_models.media import image_to_base64
        from core.ai_models.store import require_model
        from core.ai_models.engine import analyze
        record = require_model("vision")  # fixed refusal + model link when missing
        b64, mime = image_to_base64(frame_path)
        ans = analyze(record, {"prompt": prompt, "image_base64": b64, "image_mime": mime})
        return {"frame": frame_path.name, "analysis": ans, "success": True}
    except Exception as e:
        return {"frame": frame_path.name, "analysis": f"Failed: {e}", "success": False}

def _process_job_async(job_id: str, video_path: Path, prompt: str, metadata: dict):
    """Background thread: extract frames, analyze concurrently, update job store."""
    try:
        # Update to processing
        with _LOCK:
            jobs = _load_jobs()
            for j in jobs:
                if j["job_id"] == job_id:
                    j["status"] = "processing"
                    j["progress"] = "extracting frames"
                    break
            _save_jobs(jobs)

        frames = _extract_frames(video_path, max_frames=12)
        if not frames:
            with _LOCK:
                jobs = _load_jobs()
                for j in jobs:
                    if j["job_id"] == job_id:
                        j["status"] = "failed"
                        j["error"] = "No frames extracted (ffmpeg may have failed)"
                        j["progress"] = "failed"
                        break
                _save_jobs(jobs)
            return

        # Update progress
        with _LOCK:
            jobs = _load_jobs()
            for j in jobs:
                if j["job_id"] == job_id:
                    j["status"] = "processing"
                    j["progress"] = f"analyzing {len(frames)} frames concurrently"
                    j["frame_count"] = len(frames)
                    break
            _save_jobs(jobs)

        # Analyze concurrently via ThreadPool for speed (bounded 4 workers)
        results = []
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_to_frame = {executor.submit(_analyze_frame, fp, prompt): fp for fp in frames}
            for future in as_completed(future_to_frame):
                try:
                    res = future.result(timeout=60)
                    results.append(res)
                except Exception as e:
                    fp = future_to_frame[future]
                    results.append({"frame": fp.name, "analysis": f"Error: {e}", "success": False})

        # Sort by frame name
        results.sort(key=lambda x: x["frame"])

        # Build JSON per frame
        output = {
            "metadata": metadata,
            "frame_count": len(frames),
            "frames": results,
            "summary": f"Analyzed {len(results)} frames. " + "; ".join([f"{r['frame']}: {r['analysis'][:60]}" for r in results[:3]])
        }

        with _LOCK:
            jobs = _load_jobs()
            for j in jobs:
                if j["job_id"] == job_id:
                    j["status"] = "completed"
                    j["progress"] = "completed"
                    j["result"] = output
                    j["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    break
            _save_jobs(jobs)

        # Cleanup temp frames
        import shutil
        try:
            if frames:
                shutil.rmtree(str(frames[0].parent), ignore_errors=True)
        except Exception:
            pass
        # Video was saved locally so path could be sent as metadata - now delete it from storage after analysis
        try:
            project_root = Path(__file__).resolve().parents[3]
            # Only delete if inside storage/files/videos or storage/files (temp)
            try:
                video_path.resolve().relative_to((project_root / "storage" / "files").resolve())
                if video_path.exists():
                    video_path.unlink(missing_ok=True)
            except ValueError:
                # not inside storage/files, don't delete (may be persistent elsewhere)
                pass
        except Exception:
            pass
    except Exception as e:
        with _LOCK:
            jobs = _load_jobs()
            for j in jobs:
                if j["job_id"] == job_id:
                    j["status"] = "failed"
                    j["error"] = str(e)[:500]
                    j["progress"] = "failed"
                    break
            _save_jobs(jobs)
        # Also try to delete original video on failure (still temp)
        try:
            project_root = Path(__file__).resolve().parents[3]
            try:
                video_path.resolve().relative_to((project_root / "storage" / "files").resolve())
                if video_path.exists():
                    video_path.unlink(missing_ok=True)
            except ValueError:
                pass
        except Exception:
            pass


def execute(video_path: str | None = None, video_id: str | None = None, prompt: str | None = None, job_id: str | None = None) -> dict:
    event = format_event(video_path=video_path, video_id=video_id, job_id=job_id)
    # If job_id provided, poll status
    if job_id:
        if not isinstance(job_id, str) or not job_id.strip():
            raise ValueError("job_id must be non-empty string")
        jid = job_id.strip()
        jobs = _load_jobs()
        job = next((j for j in jobs if j["job_id"] == jid), None)
        if not job:
            raise FileNotFoundError(f"video job not found: {jid}")
        status = job.get("status")
        if status == "completed":
            return {"event": event, "job_id": jid, "status": status, "result": job.get("result"), "metadata": job.get("metadata"), "progress": job.get("progress")}
        elif status == "failed":
            return {"event": event, "job_id": jid, "status": status, "error": job.get("error"), "progress": job.get("progress")}
        else:
            # still processing or pending
            return {"event": event, "job_id": jid, "status": status or "processing", "progress": job.get("progress") or "processing", "metadata": job.get("metadata"), "message": "لسه - still processing, poll again after wait"}

    # Else start new job
    # Validate video
    vp = _validate_video_path(video_path, video_id)
    prompt_text = prompt.strip() if prompt and isinstance(prompt, str) and prompt.strip() else "Describe this video frame in detail."
    if prompt and len(prompt) > 2000:
        raise ValueError("prompt too long")
    # Check job limit
    jobs = _load_jobs()
    if len(jobs) >= MAX_JOBS:
        # prune completed old
        jobs = [j for j in jobs if j.get("status") not in ("completed","failed")][-MAX_JOBS+1:]
        _save_jobs(jobs)
        jobs = _load_jobs()
    metadata = _extract_metadata(vp)
    # Video not saved - only metadata to be sent to text model (as per spec)
    new_id = str(uuid.uuid4())[:8]
    job_entry = {
        "job_id": new_id,
        "video_path": str(vp),  # stored temporarily for processing, but not persisted as saved video
        "prompt": prompt_text,
        "metadata": metadata,
        "status": "pending",
        "progress": "queued",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "frame_count": metadata.get("frame_count", 0),
    }
    jobs.append(job_entry)
    _save_jobs(jobs)

    # Start async processing in background thread
    import threading
    t = threading.Thread(target=_process_job_async, args=(new_id, vp, prompt_text, metadata), daemon=True)
    t.start()

    return {
        "event": event,
        "job_id": new_id,
        "status": "pending",
        "progress": "queued - poll with job_id after wait",
        "metadata": metadata,
        "message": "Video analysis started. Poll with job_id after wait (use wait tool)."
    }