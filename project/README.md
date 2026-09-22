# A New Agent Loop

## 🚀 أمر واحد للـ VPS (انسخه وشغّله)

**Linux / Mac:**
```bash
git clone https://github.com/X5Coder/A-NEW-AI-AGENT-LOOP.git && cd A-NEW-AI-AGENT-LOOP/project && pip install -e . && python main.py
```

**Windows PowerShell:**
```powershell
git clone https://github.com/X5Coder/A-NEW-AI-AGENT-LOOP.git; Set-Location A-NEW-AI-AGENT-LOOP/project; pip install -e .; python main.py
```

هذا الأمر الواحد:
1. يفحص المساحة (يحتاج 14.2GB فاضي = 6.3GB نماذج + 5GB لبياناتك + 1GB نظام) ولو ناقصة يظهر تنبيه عربي ويتوقف
2. يحمل الـ 4 نماذج تلقائياً في أماكنها الصحيحة (Resume + 3 محاولات)
3. يتأكد أن كل نموذج شغال فعلاً
4. يشغل الأيجنت — تاني مرة يشتغل فوراً

## 🩺 فحص الحالة

```bash
python scripts/doctor.py
python scripts/setup_models.py --check
```

## النماذج (6.28GB إجمالي، لا تُرفع لـ GitHub)

- `models/embedding/Qwen3-Embedding-0.6B` (1.15GB) - البحث عن الأدوات
- `models/stt/whisper-medium-arabic-codeswitched-ct2` (2.94GB CT2) - عربي/إنجليزي/مختلط + لايف
- `models/vision/SmolVLM2-500M` (1.94GB) - تحليل صور
- `models/tts/supertonic-3` (0.39GB) - نص لصوت

## أوامر

```bash
python main.py                          # تشغيل (يحمل تلقائي أول مرة)
python scripts/doctor.py                # فحص شامل
python scripts/setup_models.py          # تحميل يدوي
python scripts/setup_models.py --check  # فحص فقط
```
