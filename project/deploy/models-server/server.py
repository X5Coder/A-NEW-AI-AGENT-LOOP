"""
سيرفر النماذج المختارة — يحمّل المطلوب فقط عند الإقلاع ويبقيه في الذاكرة.

الأمان:
- الواجهة `/` محميّة بإيميل + باسورد الواجهة (UI_EMAIL + UI_PASSWORD_HASH/SALT).
  كل دخول ناجح يمنح توكن جلسة عشوائياً في الذاكرة (ينتهي بعد 30 يوماً،
  والخروج يُبطله فوراً). رابط الصفحة لا يحمل توكن. حد 5 محاولات دخول
  فاشلة/دقيقة لكل IP (يُرفَض الزائد بـ 429).
- مسارات النماذج (/embed, /vision, /asr, /tts) محميّة بتوكن سري (API_TOKEN)
  عبر Authorization: Bearer حصراً في الوثائق (?token= مقبول للتوافق ويُحذَّر
  منه لأنه يظهر في سجلات الوصول).
- /health عام عمداً ليعمل فحص docker بدون أسرار (يعرض حالة التحميل فقط).

النقاط:
- GET  /        صفحة الحالة (أو صفحة الدخول).
- POST /login   إيميل + باسورد الواجهة.
- GET  /logout  خروج (يُبطِل الجلسة).
- GET  /health  فحص صحة عام (النماذج المطلوبة في هذا التشغيل فقط).
- POST /embed   {text | texts (حتى 64 نصاً)} -> vectors.
- POST /vision  ملف صورة/فيديو + prompt -> نص.
- POST /asr     ملف صوت -> نص.
- POST /tts     {text} -> ملف wav (يُحذَف من /tmp بعد الإرسال).

الأجهزة: تلقائي — GPU إن وُجدت وكفت، وإلا CPU. لا نسخ خفيفة ولا اختيار يدوي.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import io
import logging
import os
import tempfile
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

# ---------- حدود دخول (ثابتة وموثقة، لمنع استهلاك غير محدود) ----------
MAX_TEXT_CHARS = 2000
MAX_TEXTS_PER_REQUEST = 64  # حد أعلى لعدد النصوص في طلب embed واحد
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_VIDEO_BYTES = 100 * 1024 * 1024
MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_NEW_TOKENS_VISION = 256
LOAD_RETRIES = 3  # أقصى إعادة محاولة لتحميل نموذج (طبق القاعدة: max 3)
PBKDF2_ROUNDS = 200000
SESSION_COOKIE = "x5_session"
SESSION_TTL_SEC = 30 * 24 * 3600  # الجلسة تنتهي بعد 30 يوماً من الدخول
SESSION_MAX = 100  # حد أعلى للجلسات المخزنة (يُطرد الأقدم عند التجاوز)
LOGIN_MAX_FAIL = 5  # محاولات دخول فاشلة مسموحة لكل IP في النافذة
LOGIN_WINDOW_SEC = 60  # نافذة حد الدخول (ثانية)

# النماذج المطلوب تحميلها في هذا التشغيل (من SELECTED_MODELS).
# install.sh يكتب فيه معرّفات ما اخترته فقط، فيُحمَّل ويُفحَص ويُعرَض المختار فقط.
# فارغ/غير موجود = كل النماذج (الوضع الافتراضي، متوافق مع الإصدارات السابقة).
def _selected_ids() -> set[str]:
    raw = os.environ.get("SELECTED_MODELS", "").strip()
    if not raw:
        return set()
    return {p.strip() for p in raw.split(",") if p.strip()}

# رسالة إنجليزية ثابتة تُرجَع لأي أداة تطلب نموذجاً غير محمّل (غير موجود/غير مثبّت).
MODEL_MISSING_MESSAGE = (
    "No local model is available to run this tool. "
    "Please install it by running the doctor command."
)

MODELS_ROOT = Path(os.environ.get("MODELS_ROOT", "/models"))
APP_DIR = Path(__file__).resolve().parent

log = logging.getLogger("models-server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ---------- حالة النماذج (المالك الوحيد لها هو هذا السيرفر) ----------
STATUS: dict[str, dict[str, Any]] = {}
LOADED: dict[str, Any] = {}  # id -> كائن النموذج المحمّل (processor/model/SDK)
DEVICE_OF: dict[str, str] = {}  # id -> "gpu" | "cpu"
WANTED: list[str] = []  # معرّفات النماذج المطلوبة في هذا التشغيل (فارغ = الكل)
CUDA_AVAILABLE = False


def _load_models_config() -> dict:
    with open(APP_DIR / "models.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------- أمان الواجهة (إيميل + باسورد) ----------
# كل دخول ناجح يمنح توكن جلسة عشوائياً يُخزَّن في الذاكرة مع وقت انتهائه؛
# الخروج يحذفه فوراً (الجلسة تُبطَل)، والمنتهي يُرفَض تلقائياً.
SESSIONS: dict[str, float] = {}  # session_token -> expires_at (epoch)
LOGIN_FAILS: dict[str, list[float]] = {}  # client ip -> [timestamps of failed logins]


def _client_ip(request: Request) -> str:
    # خلف بروكسي يُقدَّم X-Forwarded-For؛ نأخذ الأول فقط، وإلا عنوان الاتصال.
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    try:
        return (request.client.host if request.client else "?")[:64]
    except Exception:
        return "?"


def _login_allowed(request: Request, now: float) -> bool:
    fails = [t for t in LOGIN_FAILS.get(_client_ip(request), []) if now - t < LOGIN_WINDOW_SEC]
    LOGIN_FAILS[_client_ip(request)] = fails
    return len(fails) < LOGIN_MAX_FAIL


def _login_failed(request: Request, now: float) -> None:
    fails = LOGIN_FAILS.get(_client_ip(request), [])
    fails.append(now)
    LOGIN_FAILS[_client_ip(request)] = fails[-LOGIN_MAX_FAIL:]


def _ui_configured() -> bool:
    return bool(os.environ.get("UI_EMAIL") and os.environ.get("UI_PASSWORD_HASH"))


def _prune_sessions(now: float) -> None:
    expired = [tok for tok, exp in SESSIONS.items() if exp <= now]
    for tok in expired:
        del SESSIONS[tok]
    while len(SESSIONS) > SESSION_MAX:  # حد أعلى: يُطرَد الأقدم
        oldest = min(SESSIONS, key=lambda t: SESSIONS[t])
        del SESSIONS[oldest]


def _new_session() -> str:
    import secrets as _secrets

    now = time.time()
    _prune_sessions(now)
    token = _secrets.token_hex(32)
    SESSIONS[token] = now + SESSION_TTL_SEC
    return token


def _session_ok(request: Request) -> bool:
    # التوكن عشوائي 256-بت يُتحقق من وجوده وعدم انتهائه فقط (مقارنة القاموس آمنة
    # هنا لأن المهاجم لا يملك أي توكن صالح ليقيس عليه توقيتاً).
    given = request.cookies.get(SESSION_COOKIE, "")
    if not given:
        return False
    exp = SESSIONS.get(given)
    if exp is None:
        return False
    if exp <= time.time():  # منتهية -> تُحذَف وتُرفَض
        del SESSIONS[given]
        return False
    return True


def _end_session(request: Request) -> None:
    given = request.cookies.get(SESSION_COOKIE, "")
    if given:
        SESSIONS.pop(given, None)


def _password_ok(password: str) -> bool:
    try:
        salt = bytes.fromhex(os.environ.get("UI_PASSWORD_SALT", ""))
    except ValueError:
        return False
    expected = os.environ.get("UI_PASSWORD_HASH", "")
    if not salt or not expected:
        return False
    got = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS).hex()
    return hmac.compare_digest(got, expected)


def _login_page(error: str = "") -> str:
    err = f"<p style='color:#c00'>{html.escape(error)}</p>" if error else ""
    return (
        "<!DOCTYPE html><html lang='ar' dir='rtl'><head><meta charset='utf-8'>"
        "<title>دخول الواجهة</title></head>"
        "<body style='font-family:sans-serif;max-width:360px;margin:80px auto'>"
        "<h2>تسجيل الدخول</h2>" + err +
        "<form method='post' action='/login'>"
        "<p><input name='email' type='email' placeholder='الإيميل' required "
        "style='width:100%;padding:8px'></p>"
        "<p><input name='password' type='password' placeholder='الباسورد' required "
        "style='width:100%;padding:8px'></p>"
        "<p><button type='submit' style='padding:8px 16px'>دخول</button></p>"
        "</form></body></html>"
    )


# ---------- توكن مسارات النماذج ----------
def _check_token(
    request: Request,
    token: Optional[str] = Query(default=None),
    creds: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False)),
) -> None:
    """يرفض أي طلب بدون التوكن الصحيح (لا يسرّب التوكن في السجلات)."""
    expected = os.environ.get("API_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=500, detail="الخادم بدون توكن (API_TOKEN غير مضبوط)")
    given = token or (creds.credentials if creds else "")
    if not hmac.compare_digest(str(given), expected):
        raise HTTPException(status_code=401, detail="توكن غير صحيح")


# ---------- توزيع الأجهزة ----------
def _pick_devices(sizes_mb: dict[str, int]) -> dict[str, str]:
    """يوزّع النماذج: الأثقل أولاً على الـ GPU ما دامت تكفي، والباقي CPU."""
    global CUDA_AVAILABLE
    try:
        import torch

        CUDA_AVAILABLE = torch.cuda.is_available()
    except Exception:
        CUDA_AVAILABLE = False
    if not CUDA_AVAILABLE:
        log.warning("لا توجد GPU — كل النماذج على الـ CPU (الأداء أبطأ خصوصاً الصوت)")
        return {mid: "cpu" for mid in sizes_mb}
    import torch

    total_mb = torch.cuda.get_device_properties(0).total_memory // (1024 * 1024)
    free_mb = total_mb - 1024  # هامش للنظام
    need = {mid: int(sz * 1.25) for mid, sz in sizes_mb.items()}
    plan: dict[str, str] = {}
    for mid in sorted(need, key=need.get, reverse=True):  # الأثقل أولاً
        if need[mid] <= free_mb:
            plan[mid] = "gpu"
            free_mb -= need[mid]
        else:
            plan[mid] = "cpu"
    log.info("خطة الأجهزة (VRAM الكلي %dMB): %s", total_mb, plan)
    return plan


def _dir_size_mb(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total // (1024 * 1024)


def _load_one(model_id: str, repo: str, local_dir: Path, device: str) -> None:
    """يحمّل نموذجاً واحداً (3 محاولات كحد أقصى) ويسجّل جهازه ووقته."""
    started = time.time()
    last_err: Exception | None = None
    for attempt in range(1, LOAD_RETRIES + 1):
        try:
            if model_id == "embedding":
                from sentence_transformers import SentenceTransformer

                # ملاحظة: مكتبة sentence-transformers تقبل "cuda"/"cpu" فقط ("gpu" ليست صالحة).
                LOADED["embedding"] = SentenceTransformer(
                    str(local_dir), device="cuda" if device == "gpu" else "cpu",
                    trust_remote_code=False,
                )
            elif model_id == "vision":
                import torch
                from transformers import AutoModelForMultimodalLM, AutoProcessor

                dtype = torch.float16 if device == "gpu" else torch.float32
                processor = AutoProcessor.from_pretrained(str(local_dir), trust_remote_code=True)
                model = AutoModelForMultimodalLM.from_pretrained(
                    str(local_dir), torch_dtype=dtype,
                    trust_remote_code=True, low_cpu_mem_usage=True,
                )
                model = model.to("cuda" if device == "gpu" else "cpu")
                model.eval()
                LOADED["vision"] = (processor, model)
            elif model_id == "asr":
                from qwencleo_asr import QwenCleoASR

                dtype = "bfloat16" if device == "gpu" else "float32"
                dev = "cuda:0" if device == "gpu" else "cpu"
                asr = QwenCleoASR(model_id=str(local_dir), device=dev, dtype=dtype)
                if hasattr(asr, "_ensure_model"):
                    asr._ensure_model()  # تحميل فعلي الآن وليس مع أول طلب
                LOADED["asr"] = asr
            elif model_id == "tts":
                from voicetut_tts import VoiceTutTTS

                if device == "gpu":
                    LOADED["tts"] = VoiceTutTTS.from_pretrained(str(local_dir))
                else:
                    LOADED["tts"] = VoiceTutTTS.from_pretrained(
                        str(local_dir), device="cpu", dtype="float32")
            else:
                raise ValueError(f"نموذج غير معروف: {model_id}")
            DEVICE_OF[model_id] = device
            STATUS[model_id] = {
                "loaded": True, "device": device,
                "loaded_at": datetime.now(timezone.utc).isoformat(),
                "repo": repo, "error": None,
                "load_sec": round(time.time() - started, 1),
            }
            log.info("تم تحميل النموذج %s على %s في %.1fs", model_id,
                     "GPU" if device == "gpu" else "CPU", time.time() - started)
            return
        except Exception as e:  # noqa: BLE001 - نعيد المحاولة ثم نسجّل السبب
            last_err = e
            log.warning("فشل تحميل %s (محاولة %d/%d): %s", model_id, attempt, LOAD_RETRIES, e)
            time.sleep(min(2 ** attempt, 8))
    STATUS[model_id] = {"loaded": False, "device": device, "loaded_at": None,
                        "repo": repo, "error": str(last_err), "load_sec": None}
    raise RuntimeError(f"تعذّر تحميل النموذج {model_id}: {last_err}") from last_err


@asynccontextmanager
async def lifespan(app: FastAPI):
    global WANTED
    cfg = _load_models_config()
    wanted = _selected_ids()
    known = {m["id"] for m in cfg["models"]}
    unknown = sorted(wanted - known)
    if unknown:
        # خطأ إعداد صريح بدل تجاهل صامت (يفشل الإقلاع برسالة واضحة).
        raise RuntimeError(f"SELECTED_MODELS يحوي معرفات غير معروفة: {', '.join(unknown)}")
    models = [m for m in cfg["models"] if not wanted or m["id"] in wanted]
    WANTED = [m["id"] for m in models]
    sizes = {m["id"]: _dir_size_mb(MODELS_ROOT / m["subdir"]) for m in models}
    plan = _pick_devices(sizes)
    failed: list[str] = []
    for m in models:
        mid = m["id"]
        local_dir = MODELS_ROOT / m["subdir"]
        if not local_dir.exists():
            STATUS[mid] = {"loaded": False, "device": plan[mid], "loaded_at": None,
                           "repo": m["repo_id"], "error": "مجلد الأوزان غير موجود", "load_sec": None}
            failed.append(mid)
            continue
        try:
            _load_one(mid, m["repo_id"], local_dir, plan[mid])
        except RuntimeError:
            failed.append(mid)
    if failed:
        log.error("نماذج فشلت عند الإقلاع: %s (السيرفر يعمل لكن الفحص سيرفض)", failed)
    yield
    LOADED.clear()


app = FastAPI(title="X5 Models Server", lifespan=lifespan)
_bearer = HTTPBearer(auto_error=False)


# ---------- الواجهة ----------
def _status_page(request: Request) -> str:
    rows = ""
    for mid, st in STATUS.items():
        dot = "🟢" if st.get("loaded") else "🔴"
        dev = "GPU" if st.get("device") == "gpu" else "CPU"
        when = st.get("loaded_at") or "—"
        err = f"<br><small style='color:red'>{html.escape(str(st.get('error') or ''))}</small>" if st.get("error") else ""
        rows += (f"<tr><td>{dot} {html.escape(mid)}</td><td>{dev}</td>"
                 f"<td>{html.escape(str(when))}</td><td>{err}</td></tr>")
    scope = ("الكل" if not WANTED else "، ".join(WANTED))
    login_note = ""
    try:
        if _session_ok(request):
            login_note = (f"<p>مسجَّل الدخول باسم {html.escape(os.environ.get('UI_EMAIL', ''))} "
                          f"— الجلسة صالحة حتى انتهاء المهلة (30 يوماً).</p>")
    except Exception:
        login_note = ""
    return ("<!DOCTYPE html><html lang='ar' dir='rtl'><head><meta charset='utf-8'>"
            "<title>حالة النماذج</title></head><body style='font-family:sans-serif'>"
            "<h2>حالة النماذج</h2>" + login_note +
            f"<p>المحمَّل في هذا التشغيل: {html.escape(scope)}</p>"
            "<table border='1' cellpadding='8'><tr><th>النموذج</th><th>الجهاز</th>"
            "<th>وقت التحميل</th><th>ملاحظة</th></tr>" + rows +
            "</table><p><a href='/logout'>خروج</a></p></body></html>")


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    if _ui_configured() and not _session_ok(request):
        return HTMLResponse(_login_page())
    return HTMLResponse(_status_page(request))


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)) -> Response:
    if not _ui_configured():
        raise HTTPException(status_code=500, detail="الواجهة غير محميّة بإيميل/باسورد بعد")
    now = time.time()
    if not _login_allowed(request, now):
        return HTMLResponse(_login_page("محاولات كثيرة — انتظر دقيقة ثم حاول."),
                            status_code=429)
    ok_email = hmac.compare_digest(email.strip().lower(), os.environ.get("UI_EMAIL", "").lower())
    if not ok_email or not _password_ok(password):
        _login_failed(request, now)
        return HTMLResponse(_login_page("الإيميل أو الباسورد غير صحيح"), status_code=401)
    resp = RedirectResponse("/", status_code=303)
    # Secure فقط عبر https فعلاً (خلف HTTP عادي المتصفح سيرفض الكوكي الآمنة).
    secure = (request.url.scheme == "https") or \
        (request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https")
    resp.set_cookie(SESSION_COOKIE, _new_session(), httponly=True, samesite="lax",
                    secure=secure, max_age=SESSION_TTL_SEC)
    return resp


@app.get("/logout")
def logout(request: Request) -> Response:
    _end_session(request)  # إبطال الجلسة في الخادم فوراً
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@app.get("/health")
def health() -> dict:
    ok = bool(STATUS) and all(v.get("loaded") for v in STATUS.values())
    return {"ok": ok, "models": {k: {"loaded": v.get("loaded"), "device": v.get("device")}
                                for k, v in STATUS.items()}}


# ---------- /embed ----------
class EmbedIn(BaseModel):
    text: Optional[str] = None
    texts: Optional[list[str]] = None


@app.post("/embed")
async def embed(body: EmbedIn, _: None = Depends(_check_token)) -> dict:
    texts = body.texts if body.texts is not None else ([body.text] if body.text else None)
    if not texts or not all(isinstance(t, str) and t.strip() for t in texts):
        raise HTTPException(status_code=400, detail="أرسل text أو texts بنص غير فارغ")
    if len(texts) > MAX_TEXTS_PER_REQUEST:
        raise HTTPException(status_code=400, detail=f"عدد النصوص أكبر من {MAX_TEXTS_PER_REQUEST}")
    if any(len(t) > MAX_TEXT_CHARS for t in texts):
        raise HTTPException(status_code=400, detail=f"النص أطول من {MAX_TEXT_CHARS} حرف")
    if "embedding" not in LOADED:
        raise HTTPException(status_code=503, detail=MODEL_MISSING_MESSAGE)
    vecs = await asyncio.to_thread(
        LOADED["embedding"].encode, texts, normalize_embeddings=True, show_progress_bar=False)
    vecs = vecs.tolist() if hasattr(vecs, "tolist") else [list(v) for v in vecs]
    return {"dim": len(vecs[0]), "vectors": vecs}


# ---------- /vision (صورة أو فيديو قصير) ----------
def _extract_middle_frame(video_bytes: bytes) -> bytes:
    """يستخرج إطار المنتصف من فيديو قصير كـ PNG (يستخدم ملفاً مؤقتاً ويحذفه)."""
    try:
        import cv2
        import numpy as np
    except ImportError as e:
        raise HTTPException(status_code=500, detail="مكتبة الفيديو غير مثبتة") from e
    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    try:
        tmp.write(video_bytes)
        tmp.close()
        cap = cv2.VideoCapture(tmp.name)
        try:
            count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 1)
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(count // 2, 0))
            ok, frame = cap.read()
            if not ok or frame is None:
                raise ValueError("تعذّر قراءة إطار من الفيديو")
            ok, buf = cv2.imencode(".png", frame)
            if not ok:
                raise ValueError("تعذّر ترميز الإطار")
            return bytes(np.asarray(buf).tobytes())
        finally:
            cap.release()
    finally:
        Path(tmp.name).unlink(missing_ok=True)


@app.post("/vision")
async def vision(
    file: UploadFile = File(...),
    prompt: str = Form(default="صف محتوى الصورة باختصار."),
    _: None = Depends(_check_token),
) -> dict:
    if len(prompt) > MAX_TEXT_CHARS:
        raise HTTPException(status_code=400, detail="السؤال أطول من المسموح")
    raw = await file.read()
    ctype = (file.content_type or "").lower()
    name = (file.filename or "").lower()
    is_video = ctype.startswith("video/") or name.endswith((".mp4", ".mov", ".webm", ".mkv"))
    limit = MAX_VIDEO_BYTES if is_video else MAX_IMAGE_BYTES
    if not raw or len(raw) > limit:
        raise HTTPException(status_code=400, detail="ملف فارغ أو أكبر من المسموح")
    if is_video:
        raw = await asyncio.to_thread(_extract_middle_frame, raw)
    if "vision" not in LOADED:
        raise HTTPException(status_code=503, detail=MODEL_MISSING_MESSAGE)

    def _infer() -> str:
        import torch
        from PIL import Image

        processor, model = LOADED["vision"]
        image = Image.open(io.BytesIO(raw)).convert("RGB")
        messages = [{"role": "user", "content": [
            {"type": "image", "image": image}, {"type": "text", "text": prompt}]}]
        text = processor.apply_chat_template(messages, add_generation_prompt=True)
        inputs = processor(text=[text], images=[image], return_tensors="pt")
        dev = next(model.parameters()).device
        inputs = {k: v.to(dev) if hasattr(v, "to") else v for k, v in inputs.items()}
        with torch.no_grad():
            out = model.generate(**inputs, max_new_tokens=MAX_NEW_TOKENS_VISION, do_sample=False)
        decoded: str = processor.batch_decode(out, skip_special_tokens=True)[0]
        answer = decoded.split(prompt)[-1].strip() if prompt in decoded else decoded.strip()
        return answer or decoded.strip()

    answer = await asyncio.to_thread(_infer)
    if not answer:
        raise HTTPException(status_code=502, detail="النموذج لم يُرجع نصاً")
    return {"text": answer}


# ---------- /asr ----------
@app.post("/asr")
async def asr(file: UploadFile = File(...), _: None = Depends(_check_token)) -> dict:
    raw = await file.read()
    if not raw or len(raw) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=400, detail="ملف صوت فارغ أو أكبر من المسموح")
    if "asr" not in LOADED:
        raise HTTPException(status_code=503, detail=MODEL_MISSING_MESSAGE)
    suffix = Path(file.filename or "audio.wav").suffix or ".wav"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tmp.write(raw)
        tmp.close()

        def _tr() -> str:
            return str(LOADED["asr"].transcribe(tmp.name, language="Arabic").text or "").strip()

        text = await asyncio.to_thread(_tr)
    finally:
        Path(tmp.name).unlink(missing_ok=True)
    if not text:
        raise HTTPException(status_code=502, detail="لم ينتج أي نص من الصوت")
    return {"text": text}


# ---------- /tts ----------
class TTSIn(BaseModel):
    text: str


@app.post("/tts")
async def tts(body: TTSIn, _: None = Depends(_check_token)) -> FileResponse:
    text = " ".join(body.text.split())
    if not text:
        raise HTTPException(status_code=400, detail="النص فارغ")
    if len(text) > MAX_TEXT_CHARS:
        raise HTTPException(status_code=400, detail=f"النص أطول من {MAX_TEXT_CHARS} حرف")
    if "tts" not in LOADED:
        raise HTTPException(status_code=503, detail=MODEL_MISSING_MESSAGE)

    def _synth(dst: str) -> dict:
        import numpy as np
        import soundfile as sf

        tts = LOADED["tts"]
        pieces, sr = [], 24000
        for chunk in tts.stream(text, max_chars=220):
            sr = int(chunk[0])
            pieces.append(np.asarray(chunk[1], dtype=np.float32).reshape(-1))
        if not pieces:
            raise ValueError("النموذج لم يُنتج صوتاً")
        wav = np.concatenate(pieces)
        sf.write(dst, wav, sr)
        return {"sr": sr, "n": len(wav)}

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    try:
        info = await asyncio.to_thread(_synth, tmp.name)
    except Exception:
        Path(tmp.name).unlink(missing_ok=True)
        raise
    if info["n"] <= 0 or Path(tmp.name).stat().st_size <= 0:
        Path(tmp.name).unlink(missing_ok=True)
        raise HTTPException(status_code=502, detail="النموذج لم يُنتج صوتاً")
    # FileResponse لا يحذف الملف — نُجدوِله للحذف بعد انتهاء الإرسال.
    try:
        from starlette.background import BackgroundTask as _BT

        cleanup = _BT(Path(tmp.name).unlink, missing_ok=True)
    except Exception:
        cleanup = None
    return FileResponse(tmp.name, media_type="audio/wav", filename="tts.wav",
                        background=cleanup)