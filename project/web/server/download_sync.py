"""
Downloads -> Workspace instant sync.

Ownership: the browser (x5browser>=1.3) drops finished files into its own
LIBRARY_DOWNLOADS_DIR; this module moves settled files into
X5Agent/Workspace/downloads/ so the agent can use them at once. Showing them
without moving is not an option: terminal/filesystem tools are jailed inside
the Workspace and could never touch them there.

Rules (all bounded, never raise out of sync_once):
- Settle: a file moves only after TWO sightings with identical size+mtime.
  Chrome holds .crdownload while writing; temp suffixes are skipped outright.
- Caps: max files per cycle, max tracked names, max single-file bytes
  (oversize files stay put with one log line — never fill the disk).
- Filenames come from the network (untrusted): only the bare entry name is
  used, collisions get a counter suffix, each file moves in its own
  try/except so one bad file never blocks the rest.
- Idle when the installed x5browser predates the library downloads folder
  (returns 0, logs once) — the feature activates on upgrade, no crash.
"""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

SYNC_EVERY_SECONDS = 3.0
MAX_FILES_PER_CYCLE = 20
MAX_TRACKED_NAMES = 500
MAX_SINGLE_FILE_BYTES = 500 * 1024 * 1024
TEMP_SUFFIXES = (".crdownload", ".part", ".tmp", ".download", ".opdownload")

_seen: dict[str, tuple[int, int]] = {}
_oversize_logged: set[str] = set()
_stop_event = threading.Event()
_thread: threading.Thread | None = None
_thread_lock = threading.Lock()
_idle_logged = False


def library_downloads_dir() -> Path | None:
    """The library's download folder, or None (missing dir / old library)."""
    try:
        from x5browser.config import LIBRARY_DOWNLOADS_DIR as configured

        candidate = Path(str(configured))
        if candidate.is_dir():
            return candidate
    except Exception:
        pass
    return None


def workspace_downloads_dir() -> Path:
    """X5Agent/Workspace/downloads/ (created on demand)."""
    try:
        import sys as _sys

        proj = Path(__file__).resolve().parents[2]
        if str(proj) not in _sys.path:
            _sys.path.insert(0, str(proj))
        from core.paths import workspace_root as _ws

        target = _ws() / "downloads"
    except Exception:
        target = Path(__file__).resolve().parents[3] / "X5Agent" / "Workspace" / "downloads"
    try:
        target.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return target


def unique_target(dst_dir: Path, name: str) -> Path:
    """Collision-free target: name, name (1).ext ... name (99).ext, then millis."""
    safe = Path(name).name or "download"
    candidate = dst_dir / safe
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    for n in range(1, 100):
        candidate = dst_dir / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
    return dst_dir / f"{stem}.{int(time.time() * 1000)}{suffix}"


def _prune_state(live_names: set[str]) -> None:
    """Drop tracking for vanished files; keep tables bounded."""
    for key in [k for k in _seen if k not in live_names]:
        _seen.pop(key, None)
    for key in [k for k in _oversize_logged if k not in live_names]:
        _oversize_logged.discard(key)
    while len(_seen) > MAX_TRACKED_NAMES:
        _seen.pop(next(iter(_seen)), None)


def sync_once(src: Path | None = None, dst: Path | None = None,
              max_bytes: int | None = None) -> int:
    """Move settled files src -> dst. Returns moved count. Never raises."""
    global _idle_logged
    try:
        src_dir = src if src is not None else library_downloads_dir()
        if src_dir is None:
            if not _idle_logged:
                _idle_logged = True
                print("[downloads-sync] idle: library has no downloads folder yet")
            return 0
        if not src_dir.is_dir():
            return 0
        dst_dir = dst if dst is not None else workspace_downloads_dir()
        try:
            dst_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return 0
        limit = MAX_SINGLE_FILE_BYTES if max_bytes is None else max_bytes
        try:
            entries = sorted(
                (e for e in src_dir.iterdir() if e.is_file()),
                key=lambda e: e.name.lower(),
            )
        except Exception:
            return 0
        live = {e.name for e in entries}
        moved = 0
        for entry in entries:
            if moved >= MAX_FILES_PER_CYCLE:
                break
            name = entry.name
            if not name or name.startswith("."):
                continue
            if name.lower().endswith(TEMP_SUFFIXES):
                continue
            try:
                stat = entry.stat()
            except Exception:
                continue
            if stat.st_size > limit:
                if name not in _oversize_logged:
                    _oversize_logged.add(name)
                    print(f"[downloads-sync] left in place (too large): {name}")
                _seen.pop(name, None)
                continue
            fingerprint = (stat.st_size, stat.st_mtime_ns)
            if _seen.get(name) != fingerprint:
                _seen[name] = fingerprint
                continue  # first sighting (or still writing) — move next cycle
            try:
                target = unique_target(dst_dir, name)
                shutil.move(str(entry), str(target))
            except Exception:
                _seen.pop(name, None)
                continue
            _seen.pop(name, None)
            _oversize_logged.discard(name)
            moved += 1
            print(f"[downloads-sync] moved to Workspace/downloads: {target.name}")
        _prune_state(live)
        return moved
    except Exception:
        return 0


def _loop(every: float = SYNC_EVERY_SECONDS) -> None:
    while not _stop_event.wait(every):
        try:
            moved = sync_once()
            if moved:
                try:
                    from web.server.server import _events_emit_workspace
                except Exception:
                    try:
                        from server import _events_emit_workspace  # type: ignore
                    except Exception:
                        _events_emit_workspace = None  # type: ignore
                if _events_emit_workspace is not None:
                    try:
                        _events_emit_workspace(force=True)
                    except Exception:
                        pass
        except Exception:
            pass


def start_download_sync(every: float = SYNC_EVERY_SECONDS) -> bool:
    """Start the daemon watcher once. Returns True while active."""
    global _thread
    with _thread_lock:
        if _thread is not None and _thread.is_alive():
            return True
        _stop_event.clear()
        src = library_downloads_dir()
        if src is None:
            print("[downloads-sync] idle: library has no downloads folder yet")
            _idle_logged = True
            return False
        print(f"[downloads-sync] watching: {src}")
        _thread = threading.Thread(target=_loop, args=(every,),
                                   name="downloads-sync", daemon=True)
        _thread.start()
        return True


def stop_download_sync(timeout: float = 5.0) -> None:
    """Signal the watcher to stop; bounded join (daemon dies with the process)."""
    _stop_event.set()
    thread = _thread
    if thread is not None and thread.is_alive():
        try:
            thread.join(timeout=timeout)
        except Exception:
            pass
