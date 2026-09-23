"""
video_transcribe - async extraction of audio from video via ffmpeg + transcription via whisper-medium-arabic-codeswitched.
Each operation has job_id, model polls via wait tool. Video not saved, only metadata sent.
"""
from __future__ import annotations

import json
import time
import uuid
import tempfile
import subprocess
from pathlib import Path
import threading

from .events import format_event

STORAGE = Path(__file__).resolve().parents[3] / "storage" / "database" / "video_transcribe_jobs.json"
MAX_VIDEO_SIZE = 50 * 1024 * 1024
MAX_JOBS = 50
_LOCK = threading.Lock()


def _load_jobs() -> list[dict]:
    if not STORAGE.exists():
        return []
    try:
        return json.loads(STORAGE.read_text(encoding="utf-8"))
    except Exception:
        return []

def _save_jobs(jobs: list[dict]) -> None:
    STORAGE.parent.mkdir(parents=True, exist_ok=True)
    STORAGE.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")

def _validate_video_path(p: str | None, vid: str | None) -> Path:
    if p and vid:
        raise ValueError("Provide only one of video_path or video_id")
    if not p and not vid:
        raise ValueError("At least one of video_path or video_id required")
    project_root = Path(__file__).resolve().parents[3]
    if p:
        cand = Path(p)
        if not cand.is_absolute():
            cand = (project_root / p).resolve()
        else:
            cand = cand.resolve()
        try:
            cand.relative_to(project_root.resolve())
        except ValueError:
            raise ValueError(f"video_path must be inside project")
        if not cand.exists():
            raise FileNotFoundError(f"Video not found: {p}")
        if cand.stat().st_size > MAX_VIDEO_SIZE:
            raise ValueError("Video too large")
        if cand.suffix.lower() not in (".mp4",".avi",".mov",".mkv",".webm",".flv",".mpg",".mpeg",".m4v"):
            raise ValueError(f"Unsupported video type: {cand.suffix}")
        return cand
    else:
        assert vid is not None
        if "/" in vid or "\\" in vid or ".." in vid:
            raise ValueError("Invalid video_id")
        for base in [project_root / "storage" / "files" / "videos", project_root / "storage" / "files" / "images"]:
            cand = (base / vid).resolve()
            try:
                cand.relative_to((project_root / "storage").resolve())
            except ValueError:
                continue
            if cand.exists():
                return cand
        raise FileNotFoundError(f"Video id not found: {vid}")

def _extract_metadata(video_path: Path) -> dict:
    meta = {"path": str(video_path), "size": video_path.stat().st_size, "type": video_path.suffix.lower().lstrip(".")}
    try:
        result = subprocess.run(["ffmpeg","-i",str(video_path)], capture_output=True, text=True, timeout=10)
        out = result.stderr or ""
        import re
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", out)
        if m:
            h, mm, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
            meta["duration_seconds"] = h*3600 + mm*60 + s
    except Exception:
        pass
    meta.setdefault("duration_seconds", 0)
    return meta

def _extract_audio(video_path: Path, out_wav: Path) -> bool:
    """Extract audio via ffmpeg to 16k mono wav for whisper."""
    try:
        cmd = ["ffmpeg","-y","-i",str(video_path),"-vn","-acodec","pcm_s16le","-ar","16000","-ac","1",str(out_wav)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return result.returncode == 0 and out_wav.exists() and out_wav.stat().st_size > 0
    except Exception:
        return False

def _transcribe_audio(wav_path: Path, language: str | None = None) -> list[dict]:
    """Transcribe wav via faster_whisper, returns segments with timestamp."""
    from models.loader import get_model_path
    model_path = get_model_path("stt")
    if model_path is None or not model_path.exists():
        raise FileNotFoundError("STT model not found")
    from faster_whisper import WhisperModel
    model = WhisperModel(str(model_path), device="cpu", compute_type="int8", cpu_threads=2)
    segments, info = model.transcribe(str(wav_path), language=language, beam_size=5, vad_filter=True)
    results = []
    for seg in segments:
        results.append({"start": round(seg.start,2), "end": round(seg.end,2), "text": seg.text.strip(), "frame_note": f"From {seg.start:.1f}s to {seg.end:.1f}s said: {seg.text.strip()}"})
    return results

def _process_job_async(job_id: str, video_path: Path, language: str | None, metadata: dict):
    try:
        with _LOCK:
            jobs = _load_jobs()
            for j in jobs:
                if j["job_id"] == job_id:
                    j["status"]="processing"
                    j["progress"]="extracting audio"
                    break
            _save_jobs(jobs)

        # Extract audio to temp
        tmpdir = Path(tempfile.mkdtemp(prefix="video_audio_"))
        wav = tmpdir / "audio.wav"
        ok = _extract_audio(video_path, wav)
        if not ok:
            with _LOCK:
                jobs = _load_jobs()
                for j in jobs:
                    if j["job_id"] == job_id:
                        j["status"]="failed"
                        j["error"]="Audio extraction failed (ffmpeg)"
                        break
                _save_jobs(jobs)
            return

        with _LOCK:
            jobs = _load_jobs()
            for j in jobs:
                if j["job_id"] == job_id:
                    j["progress"]="transcribing audio via STT"
                    break
            _save_jobs(jobs)

        segments = _transcribe_audio(wav, language=language)

        output = {
            "metadata": metadata,
            "segment_count": len(segments),
            "segments": segments,
            "full_text": " ".join([s["text"] for s in segments]),
            "summary": f"Transcribed {len(segments)} segments. " + "; ".join([s["frame_note"][:80] for s in segments[:3]])
        }

        with _LOCK:
            jobs = _load_jobs()
            for j in jobs:
                if j["job_id"] == job_id:
                    j["status"]="completed"
                    j["progress"]="completed"
                    j["result"]=output
                    j["completed_at"]=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    break
            _save_jobs(jobs)

        # cleanup
        import shutil
        try:
            shutil.rmtree(str(tmpdir), ignore_errors=True)
        except Exception:
            pass
        # Video was saved locally so path could be sent as metadata - now delete it from storage after transcription
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
    except Exception as e:
        with _LOCK:
            jobs = _load_jobs()
            for j in jobs:
                if j["job_id"] == job_id:
                    j["status"]="failed"
                    j["error"]=str(e)[:500]
                    break
            _save_jobs(jobs)
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

def execute(video_path: str | None = None, video_id: str | None = None, job_id: str | None = None, language: str | None = None) -> dict:
    event = format_event(video_path=video_path, video_id=video_id, job_id=job_id)
    if job_id:
        if not isinstance(job_id, str) or not job_id.strip():
            raise ValueError("job_id must be non-empty")
        jid = job_id.strip()
        jobs = _load_jobs()
        job = next((j for j in jobs if j["job_id"]==jid), None)
        if not job:
            raise FileNotFoundError(f"transcribe job not found: {jid}")
        status = job.get("status")
        if status=="completed":
            return {"event": event, "job_id": jid, "status": status, "result": job.get("result"), "metadata": job.get("metadata")}
        elif status=="failed":
            return {"event": event, "job_id": jid, "status": status, "error": job.get("error")}
        else:
            return {"event": event, "job_id": jid, "status": status or "processing", "progress": job.get("progress") or "processing", "metadata": job.get("metadata"), "message": "لسه - still processing, use wait then poll again"}

    vp = _validate_video_path(video_path, video_id)
    if language is not None and not isinstance(language, str):
        raise TypeError("language must be string")
    lang = language.strip().lower() if language and language.strip() else None
    if lang and len(lang) > 10:
        raise ValueError("language too long")

    jobs = _load_jobs()
    if len(jobs) >= MAX_JOBS:
        jobs = [j for j in jobs if j.get("status") not in ("completed","failed")][-MAX_JOBS+1:]
        _save_jobs(jobs)
        jobs = _load_jobs()

    metadata = _extract_metadata(vp)
    new_id = str(uuid.uuid4())[:8]
    entry = {
        "job_id": new_id,
        "video_path": str(vp),
        "language": lang,
        "metadata": metadata,
        "status": "pending",
        "progress": "queued",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    jobs.append(entry)
    _save_jobs(jobs)

    import threading
    t = threading.Thread(target=_process_job_async, args=(new_id, vp, lang, metadata), daemon=True)
    t.start()

    return {
        "event": event,
        "job_id": new_id,
        "status": "pending",
        "progress": "queued - poll with job_id after wait",
        "metadata": metadata,
        "message": "Transcription started. Poll with job_id after wait."
    }
