# ============================================================
# image واحد فيه الكود والمكتبات فقط — بدون أي أوزان.
# الأوزان تعيش خارج الـ image في فولدر models الوحيد:  project/models  ->  /models
# يعمل على CPU فقط أو مع GPU بنفس الـ image (torch يكتشف تلقائياً).
# ============================================================
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models/.cache \
    HUGGINGFACE_HUB_CACHE=/models/.cache

# أدوات النظام: ffmpeg (فيديو/صوت) + git (مكتبات من GitHub) + curl (فحص الصحة)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        git \
        curl \
    && rm -rf /var/lib/apt/lists/*

# مكتبات النماذج — نسخ حديثة حسب طريقة كل نموذج في صفحته:
# - Qwen3-Embedding-0.6B  -> sentence-transformers
# - Qwen3.5-4B            -> transformers حديث (AutoProcessor + AutoModelForMultimodalLM)
# - QwenCleo-ASR          -> qwencleo-asr (حسب صفحة النموذج)
# - VoiceTut-TTS          -> voicetut-tts (حسب صفحة النموذج)
#
# مهم GPU: حزمة torch من PyPI نسخة CPU فقط ولا ترى كرت الشاشة أبداً.
# لذلك يُثبَّت torch/torchaudio أولاً من قناة TORCH_INDEX_URL التي يمررها
# install.sh تلقائياً (cu121/cu124/cu126 حسب تعريف كرت الشاشة، أو cpu)،
# ثم بقية الحزم من PyPI فتجد torch مثبّتاً ولا تستبدله.
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir --index-url ${TORCH_INDEX_URL} \
        "torch" \
        "torchaudio" \
    && pip install --no-cache-dir \
        "transformers>=4.52" \
        "sentence-transformers" \
        "accelerate" \
        "huggingface_hub" \
        "safetensors" \
        "Pillow" \
        "numpy" \
        "soundfile" \
        "opencv-python-headless" \
        "fastapi" \
        "uvicorn" \
        "python-multipart" \
        "pyyaml" \
        "qwencleo-asr" \
        "voicetut-tts"

WORKDIR /app
COPY models.yaml server.py verify.py /app/

EXPOSE 8000

# التشغيل الفعلي يضبطه docker-compose (وضع offline بعد اكتمال التنزيل).
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
