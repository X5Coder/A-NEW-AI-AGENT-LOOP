# A New Agent Loop — Professional Local Agent

أيجنت شخصي احترافي: **System Prompt + Core Tools ثابتة + Tool Search بالإمبدنج** عبر `Qwen3-Embedding-0.6B`، مع 4 نماذج محلية (Embedding / STT / Vision / TTS) تعمل Offline.

> **للمستخدم المبتدئ على VPS: أمر واحد فقط يحمل كل شيء ويشغل الأيجنت**

---

## 🚀 أمر واحد للتشغيل على أي VPS (Linux / Windows / Mac)

### Linux / Mac (Ubuntu, Debian, VPS)
```bash
git clone https://github.com/X5Coder/A-NEW-AI-AGENT-LOOP.git && cd A-NEW-AI-AGENT-LOOP/project && pip install -e . && python main.py
```

### Windows (PowerShell)
```powershell
git clone https://github.com/X5Coder/A-NEW-AI-AGENT-LOOP.git; Set-Location A-NEW-AI-AGENT-LOOP/project; pip install -e .; python main.py
```

**ماذا يفعل `python main.py` تلقائياً؟**
1. يفحص المساحة: يحتاج **~6.3GB للنماذج + 5GB احتياطي لبياناتك + 1GB للنظام = 14.2GB فاضي**. لو المساحة ناقصة يظهر تنبيه عربي واضح ويتوقف.
2. يحمل الـ 4 نماذج تلقائياً (Resume، 3 محاولات، بأفضل طريقة موفرة للمساحة) ويضعها في مكانها الصحيح.
3. يتأكد أن كل نموذج شغال فعلاً (`model.bin` للـ STT، `processor` للـ Vision...).
4. يشغل الأيجنت لوب — تاني مرة يشتغل فوراً بدون تحميل.

**لا أسرار هاردكود، لا رفع للنماذج على GitHub، كل شيء محلي ويُحمل مرة واحدة فقط.**

---

## 🩺 أمر الدكتور - فحص الحالة

```bash
cd project
python scripts/doctor.py          # فحص شامل: Python, Disk, Dependencies, Models, Load Test
# أو
python scripts/setup_models.py --check   # فحص النماذج فقط
```

مخرجات `doctor`:
```
✅ Python 3.12 OK
✅ Disk: Free 155GB / Required 14.2GB
✅ Dependencies: huggingface_hub, transformers, faster_whisper...
✅ READY embedding 1152 MB
✅ READY stt     2945 MB (CT2)
✅ READY vision  1940 MB
✅ READY tts      396 MB
✅ System healthy - ready to run: python main.py
```

---

## 📦 النماذج المحلية (لا تُرفع لـ GitHub)

| النموذج | المصدر | المسار | الحجم | الاستخدام |
|---|---|---|---|---|
| **Embedding** | `Qwen/Qwen3-Embedding-0.6B` | `project/models/embedding/Qwen3-Embedding-0.6B` | 1.15 GB | البحث عن الأدوات `tool-discovery/search` |
| **STT** | `MohamedRashad/Arabic-Whisper-CodeSwitching-Edition` → CT2 float16 | `project/models/stt/whisper-medium-arabic-codeswitched-ct2` | 2.94 GB | عربي + إنجليزي + مختلط + لايف (faster-whisper, VAD, chunk) |
| **Vision** | `HuggingFaceTB/SmolVLM2-500M-Video-Instruct` | `project/models/vision/SmolVLM2-500M` | 1.94 GB | تحليل الصور/الفيديو |
| **TTS** | `Supertone/supertonic-3` | `project/models/tts/supertonic-3` | 0.39 GB | تحويل نص لصوت 31 لغة ONNX |

الإجمالي بعد التحميل: **6.28 GB** (كان 14GB قبل التنظيف من 24 نسخة ONNX الزائدة).
كلها **git-ignored** — `git clone` حجمه كيلوبايتات، التحميل يتم أول مرة فقط عبر `main.py`.

---

## 🔧 أوامر إضافية

```bash
# تحميل يدوي (نفس ما يعمله main.py)
python scripts/setup_models.py
python scripts/setup_models.py --only vision   # نموذج واحد
python scripts/setup_models.py --force         # إعادة تحميل
python scripts/setup_models.py --check         # فحص فقط

# فحص شامل
python scripts/doctor.py

# تشغيل الأيجنت (بعد اكتمال النماذج)
python main.py
```

---

## 📁 الهيكل

انظر `PROJECT_MAP.md` - الخريطة القياسية. كل ملف في مكانه حسب الخريطة، وأي تغيير يحدّث الخريطة تلقائياً.

```
project/
├── main.py              # نقطة الدخول الواحدة للـ VPS
├── models/
│   ├── manifest.json    # مصدر الحقيقة للنماذج
│   ├── loader.py        # ensure_models() - تحميل تلقائي
│   ├── embedding/       # Qwen3 (git-ignored)
│   ├── stt/             # whisper CT2 (git-ignored)
│   ├── vision/          # SmolVLM2 (git-ignored)
│   └── tts/             # supertonic-3 (git-ignored)
├── scripts/
│   ├── setup_models.py  # 3 retries, فحص مساحة +5GB, تحويل CT2
│   └── doctor.py        # فحص شامل
└── pyproject.toml
```

---

## 🔒 الأمان والجودة

مبني حسب `Master Code Rules.md` (83 قاعدة): correctness أولاً، لا هاردكود أسرار (`HF_TOKEN` من env فقط)، تحقق مسارات، 3 محاولات فقط، تنظيف موارد، رسائل خطأ واضحة بدون تسريب بيانات.

---

## 📄 الترخيص

MIT
