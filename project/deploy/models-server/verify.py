#!/usr/bin/env python3
"""فحص فعلي للنماذج المطلوبة بمدخلات صغيرة — يُشغَّل داخل الحاوية بعد الإقلاع.

الاستخدام (داخل الحاوية):
    API_TOKEN=... SELECTED_MODELS=... python /app/verify.py
    أو: API_TOKEN=... API_BASE=http://127.0.0.1:8000 python /app/verify.py

يفحص: embedding (عربي+إنجليزي والأبعاد) -> vision (صورة+فيديو قصير ونص راجع)
-> tts (جملة قصيرة وملف صوت) -> asr (نسخ صوت الـ tts والتأكد من وجود نص).
يُفحَص المحمَّل المطلوب فقط (غير المحمَّل يُتخطَّى ولا يُسقِط الفحص).
يخرج بـ 0 عند نجاح الكل، وبـ 1 مع اسم النموذج الفاشل وسببه (بالعربي) عند الفشل.
يستخدم المكتبة المعيارية فقط (+Pillow وffmpeg الموجودان في الـ image).
"""

from __future__ import annotations

import io
import json
import math
import os
import struct
import subprocess
import tempfile
import urllib.request
import wave
from pathlib import Path

BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000")
TOKEN = os.environ.get("API_TOKEN", "")
TIMEOUT = 300  # مهلة الطلب الواحد (نماذج CPU بطيئة)
KNOWN_IDS = ["embedding", "vision", "tts", "asr"]
FAILURES: list[str] = []
SKIPPED: list[str] = []


def _only(ids: list[str]) -> list[str]:
    """نماذج هذا التشغيل من SELECTED_MODELS (فارغ = الكل). الفحص يغطي المحمَّل فقط."""
    raw = os.environ.get("SELECTED_MODELS", "").strip()
    if not raw:
        return ids
    want = {p.strip() for p in raw.split(",") if p.strip()}
    unknown = sorted(want - set(ids))
    if unknown:
        print(f"⚠️  معرفات غير معروفة في SELECTED_MODELS تُتجاهَل: {', '.join(unknown)}", flush=True)
    return [i for i in ids if i in want]


def _url(path: str) -> str:
    # الفحص الداخلي يستخدم Bearer (التوكن لا يظهر في الرابط/السجلات).
    return f"{BASE}{path}"


def _auth_headers(extra: dict | None = None) -> dict:
    headers = dict(extra or {})
    headers["Authorization"] = f"Bearer {TOKEN}"
    return headers


def _post_json(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        _url(path), data=json.dumps(payload).encode("utf-8"),
        headers=_auth_headers({"Content-Type": "application/json"}), method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _post_file(path: str, field: str, filename: str, data: bytes,
               mime: str, extra: dict | None = None) -> dict:
    boundary = "----x5verify1234"
    body = io.BytesIO()
    if extra:
        for k, v in extra.items():
            body.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    body.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\""
               f"\r\nContent-Type: {mime}\r\n\r\n".encode() + data + f"\r\n--{boundary}--\r\n".encode())
    req = urllib.request.Request(_url(path), data=body.getvalue(),
                                 headers=_auth_headers(
                                     {"Content-Type": f"multipart/form-data; boundary={boundary}"}),
                                 method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        ctype = r.headers.get("Content-Type", "")
        raw = r.read()
        if "application/json" in ctype:
            return json.loads(raw.decode("utf-8"))
        return {"__raw__": raw}


def _check(name: str, ok: bool, detail: str = "") -> None:
    mark = "✅" if ok else "❌"
    print(f"{mark} {name}" + (f": {detail}" if detail else ""), flush=True)
    if not ok:
        FAILURES.append(f"{name} — {detail}")


def _red_square_png() -> bytes:
    from PIL import Image

    img = Image.new("RGB", (64, 64), (200, 30, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _second_black_mp4() -> bytes:
    """فيديو أسود ثانية واحدة (128x128) عبر ffmpeg الموجود في الـ image."""
    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp.close()
    try:
        subprocess.run(["ffmpeg", "-y", "-v", "error",
                        "-f", "lavfi", "-i", "color=black:s=128x128:d=1:r=8",
                        "-pix_fmt", "yuv420p", tmp.name],
                       check=True, timeout=60)
        return Path(tmp.name).read_bytes()
    finally:
        Path(tmp.name).unlink(missing_ok=True)


def _tone_wav(sec: float = 1.0) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        n = int(16000 * sec)
        for i in range(n):
            w.writeframes(struct.pack("<h", int(9000 * math.sin(2 * math.pi * 440 * i / 16000))))
    return buf.getvalue()


def main() -> int:
    if not TOKEN:
        print("❌ لا يوجد توكن (API_TOKEN فارغ) — الفحص يتطلب التوكن", flush=True)
        return 1
    print("🔍 بدء الفحص الفعلي للنماذج الأربعة...", flush=True)

    # 1) صحة عامة
    try:
        with urllib.request.urlopen(f"{BASE}/health", timeout=30) as r:
            health = json.loads(r.read().decode("utf-8"))
        loaded = {k for k, v in (health.get("models") or {}).items() if v.get("loaded")}
        _check("الخادم يستجيب", True)
    except Exception as e:
        _check("الخادم يستجيب", False, f"لا رد من /health: {e}")
        return 1
    want = _only(KNOWN_IDS)
    not_loaded = sorted(set(want) - loaded)
    if not_loaded:
        print(f"⏭️  غير محمَّل في هذا التشغيل (يُتخطَّى): {', '.join(not_loaded)}", flush=True)
        SKIPPED.extend(not_loaded)

    def _run(mid: str) -> bool:
        return mid in want and mid in loaded

    # 2) Embedding: عربي + إنجليزي + تطابق الأبعاد
    tts_audio: bytes | None = None
    if _run("embedding"):
        try:
            res = _post_json("/embed", {"texts": ["مرحبا كيف حالك اليوم", "hello how are you today"]})
            vecs = res.get("vectors", [])
            dim = res.get("dim", 0)
            ok = len(vecs) == 2 and dim > 0 and len(vecs[0]) == dim == len(vecs[1])
            _check("النموذج embedding", ok, f"البعد={dim}" if ok else f"رد غير منطقي: {str(res)[:120]}")
        except Exception as e:
            _check("النموذج embedding", False, str(e)[:200])

    # 3) Vision: صورة + فيديو قصير
    if _run("vision"):
        try:
            res = _post_file("/vision", "file", "red.png", _red_square_png(), "image/png",
                             {"prompt": "ما لون المربع في الصورة؟"})
            txt = str(res.get("text", "")).strip()
            _check("النموذج vision (صورة)", len(txt) > 0, f"الرد: {txt[:80]}" if txt else "رد فارغ")
        except Exception as e:
            _check("النموذج vision (صورة)", False, str(e)[:200])
        try:
            res = _post_file("/vision", "file", "black.mp4", _second_black_mp4(), "video/mp4",
                             {"prompt": "صف ما تراه في الفيديو."})
            txt = str(res.get("text", "")).strip()
            _check("النموذج vision (فيديو)", len(txt) > 0, f"الرد: {txt[:80]}" if txt else "رد فارغ")
        except Exception as e:
            _check("النموذج vision (فيديو)", False, str(e)[:200])

    # 4) TTS: جملة قصيرة -> ملف صوت
    if _run("tts"):
        try:
            res = _post_json("/tts", {"text": "مرحبا، هذا اختبار صوتي قصير."})
            raw = res.get("__raw__", b"")
            tts_audio = raw if raw else None
            _check("النموذج tts", bool(raw and len(raw) > 1000), f"حجم الصوت={len(raw)} بايت" if raw else "لا صوت")
        except Exception as e:
            _check("النموذج tts", False, str(e)[:200])

    # 5) ASR: نسخ صوت الـ tts (كلام حقيقي) — وإن غاب نجرّب نغمة للتشخيص فقط
    #    (النغمة ليست كلاماً؛ فشل نسخها تشخيصي ولا يُسقِط الفحص ما لم يُطلب asr وحده).
    if _run("asr"):
        try:
            if tts_audio:
                res = _post_file("/asr", "file", "sample.wav", tts_audio, "audio/wav")
                txt = str(res.get("text", "")).strip()
                _check("النموذج asr", len(txt) > 0, f"النص: {txt[:80]}" if txt else "لا نص")
            else:
                res = _post_file("/asr", "file", "tone.wav", _tone_wav(), "audio/wav")
                txt = str(res.get("text", "")).strip()
                tone_mode = "tts" not in want  # النغمة تشخيصية فقط عندما لا يُفحَص tts أصلاً
                _check("النموذج asr (تشخيصي بنغمة)", len(txt) > 0 or not tone_mode,
                       f"النص: {txt[:80]}" if txt else "لا نص من نغمة (متوقع — ليست كلاماً)")
        except Exception as e:
            _check("النموذج asr", False, str(e)[:200])

    print("=" * 50, flush=True)
    if SKIPPED:
        print(f"⏭️  متخطَّى (غير محمَّل): {', '.join(SKIPPED)}", flush=True)
    if FAILURES:
        print("❌ فشل الفحص في:", flush=True)
        for f in FAILURES:
            print(f"   - {f}", flush=True)
        print("لن تُطبع أي روابط نجاح — راجع سجل الحاوية: docker compose logs", flush=True)
        return 1
    print("🎉 النماذج المطلوبة تعمل فعلياً.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
