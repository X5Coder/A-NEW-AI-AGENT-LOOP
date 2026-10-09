#!/usr/bin/env python3
"""تنزيل النماذج المختارة واحداً واحداً مع بروجريس بار حقيقي تفاعلي.

المسؤولية: قراءة selection.json وتنزيل كل نموذج مختار في فولدر models الوحيد.
- لو النموذج مكتمل (علامة + ملفات) => تخطٍّ.
- لو ناقص/جزئي => snapshot_download (يكمل من حيث توقف تلقائياً).
- بار واحد لكل نموذج يُمحى ويُحدَّث في مكانه (يتابع الحجم الفعلي على القرص).
- عند الإلغاء (Ctrl+C) الملفات تبقى، وإعادة الأمر تكمل من نفس المكان.

كل نموذج: قبل بدء تنزيله يُنقَص حجمه من ملف الحجز الإجمالي (X5_RESERVE_FILE)
لتحرير مساحته، ويُحذف الملف تماماً عند استنفاد الإجمالي.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODELS_ROOT = (HERE.parents[1] / "models").resolve()
SELECTION = HERE / "selection.json"
MARKER = ".download_complete"
BAR_WIDTH = 30

# نستخدم بار الواجهة الخاص بنا فقط (واحد لكل نموذج) ونعطّل أشرطة المكتبة.
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")


def _human(n: float) -> str:
    val = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < 1024:
            return f"{val:.2f} {unit}"
        val /= 1024
    return f"{val:.2f} PB"


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


def _render(label: str, done: int, total: int) -> None:
    pct = min(100.0, (done / total * 100.0)) if total > 0 else 0.0
    filled = int(BAR_WIDTH * pct / 100.0)
    bar = "█" * filled + "░" * (BAR_WIDTH - filled)
    sys.stdout.write(f"\r  {label:<10} [{bar}] {pct:5.1f}%  {_human(done)} / {_human(total)}  ")
    sys.stdout.flush()


def _shrink_reserve(nbytes: int) -> None:
    """يحرّر تدريجياً من ملف الحجز الإجمالي بقدر حجم النموذج الذي بدأ تنزيله الآن."""
    path = os.environ.get("X5_RESERVE_FILE")
    if not path or not os.path.exists(path):
        return
    try:
        remaining = os.path.getsize(path) - int(nbytes)
        if remaining > 0:
            subprocess.run(["fallocate", "-l", str(remaining), path], check=False)
        else:
            os.remove(path)
    except OSError:
        pass


def _download_one(entry: dict, token: str | None) -> bool:
    mid, repo, subdir = entry["id"], entry["repo_id"], entry["subdir"]
    total = int(entry.get("bytes", 0))
    target = MODELS_ROOT / subdir
    marker = target / MARKER

    if marker.exists() and target.exists() and any(target.iterdir()):
        print(f"  ✅ {mid:<10} محمّل بالفعل — تخطي")
        return True

    # حماية: تنزيل نماذج models.yaml فقط (التحقق قبل إنشاء أي مجلد).
    KNOWN_SUBDIRS = {
        "embedding/Qwen3-Embedding-0.6B",
        "vision/Qwen3.5-4B",
        "stt/QwenCleo-ASR",
        "tts/VoiceTut-TTS",
    }
    if subdir not in KNOWN_SUBDIRS:
        print(f"  ❌ {mid}: مجلد غير معروف في selection.json — {subdir}")
        return False

    target.mkdir(parents=True, exist_ok=True)
    _shrink_reserve(int(entry.get("bytes", 0)))

    errors: list[Exception] = []

    def worker() -> None:
        try:
            from huggingface_hub import snapshot_download

            # resume_download=True صريح: الملفات الجزئية تُستكمَل لا من الصفر.
            snapshot_download(
                repo_id=repo,
                local_dir=str(target),
                token=token or None,
                max_workers=4,
                resume_download=True,
            )
        except Exception as exc:  # noqa: BLE001 - يُعرض السبب للمستخدم ويوقف النموذج
            errors.append(exc)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    while thread.is_alive():
        _render(mid, _dir_size(target), total)
        time.sleep(0.5)
    thread.join()
    _render(mid, _dir_size(target), total)
    sys.stdout.write("\n")
    sys.stdout.flush()

    if errors:
        print(f"  ❌ {mid}: فشل التنزيل — {errors[0]}")
        return False

    size_now = _dir_size(target)
    if size_now <= 0:
        print(f"  ❌ {mid}: لم يُنزَّل أي ملف")
        return False

    try:
        marker.write_text(
            json.dumps({
                "repo_id": repo,
                "completed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "via": "deploy-install",
            }, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as e:
        print(f"  ❌ {mid}: تعذّر كتابة علامة الاكتمال — {e}")
        return False

    print(f"  ✅ {mid:<10} اكتمل ({_human(size_now)})")
    return True


def main() -> int:
    try:
        selection = json.loads(SELECTION.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"تعذّر قراءة selection.json (شغّل select_models.py أولاً): {e}")
        return 1
    if not isinstance(selection, list) or not selection:
        print("لا يوجد نموذج مختار.")
        return 1

    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_HUB_TOKEN")
    print(f"تنزيل {len(selection)} نموذج في {MODELS_ROOT} ...")
    failures: list[str] = []
    try:
        for entry in selection:
            if not _download_one(entry, token):
                failures.append(entry["id"])
    except KeyboardInterrupt:
        sys.stdout.write("\n")
        print("⏸️  تم الإلغاء — أعد نفس الأمر وسيكمل من حيث توقف.")
        return 130

    print("-" * 60)
    if failures:
        print("❌ فشل تنزيل:", "، ".join(failures))
        return 1
    print("🎉 كل النماذج المختارة جاهزة.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())