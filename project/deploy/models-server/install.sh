#!/usr/bin/env bash
# ============================================================
# تثبيت سيرفر النماذج:  ./install.sh  (مباشر) أو  python install.py  (موحّد كل الأنظمة)
# يعمل على جهاز محلي (Linux) أو VPS — بـ GPU أو CPU فقط.
# على Windows: استخدم  python install.py  (يفحص OS + Docker ثم يشغّل نفس التدفق عبر Git-Bash).
#
# التدفق:
#  1) كل الفحوصات (Docker / Python / CPU-GPU / RAM / المساحة / حالة المشروع)
#  2) عرض النماذج (اسم + حجم + حالة) واختيار المستخدم بعلامات ثم Enter
#  3) فحص مساحة دقيق للاختيار (حجم المختار غير المكتمل + 5GB) وحجز المساحة
#  4) تنزيل كل نموذج ببروجريس بار حقيقي (يكمل لو اتلغى)
#  5) تشغيل المدقق (doctor) تلقائياً
#  6) طلب إيميل + باسورد الواجهة (أو من UI_*_PRESET في غير التفاعلي)
#     -> بناء الـ image -> فحص GPU الحقيقي -> تشغيل -> فحص فعلي
#  7) طباعة رابط الصفحة + توكن النماذج
#
# خيارات:  ./install.sh [--yes] [--port 8000] [--cpu] [--force-recreate]
#   --yes            : بدون أسئلة (يختار كل النماذج الناقصة تلقائياً)
#   --cpu            : تجاهل كرت الشاشة والتشغيل على الـ CPU حتى لو وُجد GPU
#   --force-recreate : إعادة إنشاء الحاوية من الصفر (يلزم عند تغيّر المنافذ أو الـ .env)
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT="8000"
PORT_ARG=""
ASSUME_YES=0
FORCE_CPU=0
FORCE_RECREATE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes) ASSUME_YES=1 ;;
    --cpu) FORCE_CPU=1 ;;
    --force-recreate) FORCE_RECREATE=1 ;;
    --port=*) PORT_ARG="${1#--port=}" ;;
    --port) shift; PORT_ARG="${1:-8000}" ;;
    *) echo "خيار غير معروف: $1"; exit 2 ;;
  esac
  shift
done

MODELS_DIR="$(cd "$SCRIPT_DIR/../../models" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
IMAGE="x5-models-server"
RESERVE_GB="$(python3 -c "import yaml;print(int(yaml.safe_load(open('models.yaml')).get('reserve_gb',5)))" 2>/dev/null || echo 5)"
RESERVE_MB=$(( RESERVE_GB * 1024 ))

say()  { echo "==> $*"; }
ok()   { echo "✅ $*"; }
warn() { echo "⚠️  $*"; }
die()  { echo "❌ $*"; exit 1; }

cleanup_tmp_reserves() { rm -f "$SCRIPT_DIR"/.reserve-total "$SCRIPT_DIR"/.reserve-model-* 2>/dev/null || true; }
trap 'cleanup_tmp_reserves' INT TERM EXIT

echo "=================================================="
echo "  سيرفر النماذج — تثبيت بأمر واحد"
echo "=================================================="

# ============================================================
# المرحلة 1: كل الفحوصات
# ============================================================
say "المرحلة 1: الفحوصات (Docker / Python / GPU / RAM / المساحة / حالة المشروع)"

# 1.1 Docker
if ! command -v docker >/dev/null 2>&1; then
  if [[ "$(uname -s)" == "Linux" ]]; then
    say "Docker غير موجود — سيُثبَّت تلقائياً (Linux)..."
    curl -fsSL https://get.docker.com | sh \
      || die "فشل تثبيت Docker تلقائياً. ثبّته يدوياً من: https://docs.docker.com/get-docker"
    sudo usermod -aG docker "$USER" 2>/dev/null || true
    warn "أُضفتَ لمجموعة docker — قد تحتاج تسجيل خروج ودخول، أو استخدم sudo مع الأوامر."
  else
    die "Docker غير مثبّت. ثبّت Docker Desktop من: https://docs.docker.com/get-docker ثم أعد الأمر."
  fi
fi
docker info >/dev/null 2>&1 || die "Docker مثبّت لكنه لا يعمل. شغّل Docker أولاً ثم أعد المحاولة."
docker compose version >/dev/null 2>&1 || die "إضافة docker compose غير موجودة. حدّث Docker لنسخة حديثة."
ok "Docker يعمل."

# 1.2 Python + مكتبات مساعدة (huggingface_hub للأحجام، pyyaml للقراءة)
if ! command -v python3 >/dev/null 2>&1; then
  die "بايثون (python3) غير موجود — ثبّته ثم أعد الأمر."
fi
python3 -c "import yaml, huggingface_hub" 2>/dev/null || {
  say "تثبيت مكتبات مساعدة (huggingface_hub, pyyaml)..."
  pip3 install --quiet huggingface_hub pyyaml 2>/dev/null || pip install --quiet huggingface_hub pyyaml \
    || die "تعذّر تثبيت huggingface_hub و pyyaml."
}
ok "بايثون جاهز."

# 1.3 المعالج: GPU أم CPU
GPU_OK=0
if [[ "$FORCE_CPU" == "1" ]]; then
  warn "وضع CPU بطلبك (--cpu) — يُتجاهَل كرت الشاشة."
  GPU_NAME=""; VRAM_MB=0
elif command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
  GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -n1 || echo "NVIDIA")
  VRAM_MB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | awk '{s+=$1} END{print s+0}')
  # محمول (POSIX sed بدل grep -P غير المتوفر في Ubuntu افتراضياً)
  CUDA_VER=$(nvidia-smi 2>/dev/null | sed -n 's/.*CUDA Version: \([0-9][0-9]*\.[0-9][0-9]*\).*/\1/p' | head -n1 || echo "")
  GPU_OK=1
  ok "GPU: $GPU_NAME (VRAM ${VRAM_MB}MB${CUDA_VER:+, CUDA $CUDA_VER})"
else
  GPU_NAME=""; VRAM_MB=0
  warn "لا GPU متاحة — التشغيل على الـ CPU (الأداء أبطأ خصوصاً الصوت)."
fi

# قناة torch المناسبة للبناء (GPU تحتاج نسخة CUDA وإلا لن يرى torch الكرت أبداً):
# cu126 للتعريف 12.6+، cu124 للتعريف 12.4+، cu121 للتعريف 12.1+، وإلا cpu.
# (إن تعذّرت قراءة نسخة CUDA لأي سبب نعتبرها 0.0 = قناة cpu الآمنة.)
TORCH_INDEX_URL="https://download.pytorch.org/whl/cpu"
if [[ "$GPU_OK" == "1" ]]; then
  _major="${CUDA_VER%%.*}"; _minor="${CUDA_VER#*.}"; _minor="${_minor%%.*}"
  _major="${_major:-0}"; _minor="${_minor:-0}"
  [[ "$_major" =~ ^[0-9]+$ ]] || _major=0
  [[ "$_minor" =~ ^[0-9]+$ ]] || _minor=0
  if [[ "$_major" -gt 12 || ("$_major" == "12" && "$_minor" -ge 6) ]]; then
    TORCH_INDEX_URL="https://download.pytorch.org/whl/cu126"
  elif [[ "$_major" == "12" && "$_minor" -ge 4 ]]; then
    TORCH_INDEX_URL="https://download.pytorch.org/whl/cu124"
  elif [[ "$_major" == "12" ]]; then
    TORCH_INDEX_URL="https://download.pytorch.org/whl/cu121"
  else
    warn "تعريف CUDA (${CUDA_VER:-غير مقروء}) أقدم من 12.1 — لا توجد حزمة torch رسمية له، سيبنى بقناة cpu."
    GPU_OK=0
  fi
  [[ "$GPU_OK" == "1" ]] && say "قناة torch للبناء: $TORCH_INDEX_URL"
fi

# 1.4 الذاكرة RAM
RAM_MB=$(free -m 2>/dev/null | awk '/^Mem:/{print $2}' || grep MemTotal /proc/meminfo | awk '{print int($2/1024)}')
TOTAL_MEM_MB=$(( RAM_MB + VRAM_MB ))
echo "    الذاكرة: RAM ${RAM_MB}MB + VRAM ${VRAM_MB}MB = ${TOTAL_MEM_MB}MB"

# 1.5 المساحة المتاحة
avail_mb() { df -Pm "$1" | awk 'NR==2{gsub("M","",$4); print $4}'; }
mkdir -p "$MODELS_DIR"
DOCKER_ROOT=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo "/var/lib/docker")
AV_MODELS_MB=$(avail_mb "$MODELS_DIR")
echo "    المساحة المتاحة في $MODELS_DIR: $(( AV_MODELS_MB / 1024 ))GB"
echo "    مخزن Docker: $DOCKER_ROOT"

# 1.6 حالة المشروع الحالية (مثبّت أم لا / ناقص إيه)
echo ""
say "حالة المشروع الحالية:"
python3 select_models.py --table || true
[[ -f "$ENV_FILE" ]] && echo "   ✅ إعدادات/توكن موجودة" || echo "   ➖ إعدادات/توكن غير موجودة (ستُنشأ)"
docker image inspect "$IMAGE" >/dev/null 2>&1 && echo "   ✅ صورة Docker مبنية" || echo "   ➖ صورة Docker غير مبنية (ستُبنى)"
# المنفذ: من --port، وإلا من .env السابق، وإلا 8000
PREV_PORT="$(grep '^PORT=' "$ENV_FILE" 2>/dev/null | head -n1 | cut -d= -f2- || true)"
if [[ -n "$PORT_ARG" ]]; then
  PORT="$PORT_ARG"
elif [[ -n "${PREV_PORT:-}" ]]; then
  PORT="$PREV_PORT"; ok "المنفذ من الإعدادات السابقة: $PORT"
fi
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] && [[ "$PORT" -ge 1 && "$PORT" -le 65535 ]] \
  || die "منفذ غير صالح: $PORT (لازم 1-65535)."

# ============================================================
# المرحلة 2: الاختيار
# ============================================================
echo ""
say "المرحلة 2: اختر النماذج التي تريد تنزيلها"
if [[ "$ASSUME_YES" == "1" ]]; then
  SELECTION_IDS=$(python3 select_models.py --all) || die "لم يتم اختيار أي نموذج."
else
  SELECTION_IDS=$(python3 select_models.py) || die "لم يتم اختيار أي نموذج."
fi
[[ -n "$SELECTION_IDS" ]] || die "لم يتم اختيار أي نموذج."
ok "اختيارك: $SELECTION_IDS"

# ============================================================
# المرحلة 3: فحص المساحة الدقيق + الذاكرة
# ============================================================
echo ""
say "المرحلة 3: فحص المساحة والذاكرة للاختيار"
# حجم المختار غير المكتمل فقط (المكتمل لا يحتاج مساحة)
read -r NEED_MB PLANNED_MB < <(python3 - <<'EOF'
import json
sel = json.loads(open("selection.json", encoding="utf-8").read())
need = sum(int(e["bytes"]) for e in sel if e["status"] != "ready")
planned = sum(int(e["bytes"]) for e in sel)
print(need // (1024*1024), planned // (1024*1024))
EOF
)
NEED_MB=${NEED_MB:-0}; PLANNED_MB=${PLANNED_MB:-0}
TOTAL_NEED_MB=$(( NEED_MB + RESERVE_MB ))
echo "    للتنزيل: $(( NEED_MB / 1024 ))GB | حجز ثابت: ${RESERVE_GB}GB | الإجمالي المطلوب: $(( TOTAL_NEED_MB / 1024 ))GB"
[[ "$AV_MODELS_MB" -ge "$TOTAL_NEED_MB" ]] \
  || die "المساحة لا تكفي. المتاح: $(( AV_MODELS_MB / 1024 ))GB — والمطلوب: $(( TOTAL_NEED_MB / 1024 ))GB. فرّغ مساحة ثم أعد الأمر."

NEED_MEM_MB=$(( PLANNED_MB + $(python3 -c "import yaml;print(int(yaml.safe_load(open('models.yaml')).get('runtime_overhead_mb',4096)))" 2>/dev/null || echo 4096) ))
echo "    الذاكرة المقدّرة لتحميل المختار معاً: ~$(( NEED_MEM_MB / 1024 ))GB (المتاح ${TOTAL_MEM_MB}MB)"
if [[ "$TOTAL_MEM_MB" -lt "$NEED_MEM_MB" ]]; then
  warn "الذاكرة المتاحة أقل من المقدّر (${TOTAL_MEM_MB}MB < ${NEED_MEM_MB}MB). قد يكون التشغيل أبطأ أو يفشل."
  if [[ "$ASSUME_YES" == "1" ]]; then
    warn "المتابعة بموافقة مسبقة (--yes)."
  else
    read -r -p "هل تريد المتابعة رغم ذلك؟ (y/n): " ans
    [[ "$ans" == "y" || "$ans" == "Y" ]] || die "توقّف بطلبك قبل أي تنزيل."
  fi
fi
ok "المساحة والذاكرة تم فحصهما."

# حجز المساحة مؤقتاً على إجمالي التنزيل (ملف واحد) — يُحرَّر تدريجياً أثناء التنزيل
if command -v fallocate >/dev/null 2>&1 && [[ "$NEED_MB" -gt 0 ]]; then
  say "حجز المساحة مؤقتاً على إجمالي التنزيل (~$(( NEED_MB / 1024 ))GB)..."
  fallocate -l "$(( NEED_MB ))M" "$SCRIPT_DIR/.reserve-total" 2>/dev/null \
    && ok "تم حجز إجمالي التنزيل مؤقتاً (يُحرَّر مع التنزيل)." \
    || warn "تعذّر حجز المساحة — المتابعة بدون حجز."
else
  warn "لا حجز مساحة (fallocate غير موجود أو لا يوجد ما يُنزَّل)."
fi

# ============================================================
# المرحلة 4: التنزيل
# ============================================================
echo ""
say "المرحلة 4: تنزيل الأوزان في فولدر models الوحيد (project/models)"
export X5_RESERVE_FILE="$SCRIPT_DIR/.reserve-total"
export HF_TOKEN="${HF_TOKEN:-${HUGGINGFACE_HUB_TOKEN:-}}"
python3 download_models.py || die "فشل التنزيل. أعد نفس الأمر — سيعرض الجدول من جديد ويكمل الناقص من حيث توقف."
cleanup_tmp_reserves
ok "اكتمل التنزيل."

# حجز دائم 5GB في النهاية
if command -v fallocate >/dev/null 2>&1; then
  if fallocate -l "${RESERVE_GB}G" "$SCRIPT_DIR/.reserve-buffer" 2>/dev/null; then
    ok "تم حجز ${RESERVE_GB}GB احتياطي دائم (.reserve-buffer — يمكن حذفه يدوياً لتحرير المساحة)."
  else
    warn "تعذّر إنشاء حجز الـ ${RESERVE_GB}GB الاحتياطي."
  fi
fi

# ============================================================
# المرحلة 5: المدقق (doctor) تلقائياً
# ============================================================
echo ""
say "المرحلة 5: تشغيل المدقق (doctor) على المشروع"
python3 "$SCRIPT_DIR/../../scripts/doctor.py" || warn "المدقق وجد ملاحظات فوق — نكمل للتشغيل والفحص الفعلي."

# ============================================================
# المرحلة 6: التوكن + بناء الـ image + التشغيل
# ============================================================
echo ""
say "المرحلة 6: تأمين الواجهة + التوكن + البناء والتشغيل"

# مساعدات قراءة/كتابة .env (الباسورد والتوكن يُحفظان هنا ولا يُطبعان أبداً)
if [[ ! -f "$ENV_FILE" ]]; then : > "$ENV_FILE"; fi
chmod 600 "$ENV_FILE" 2>/dev/null || true
get_env() { grep "^$1=" "$ENV_FILE" 2>/dev/null | head -n1 | cut -d= -f2- || true; }
set_env() {  # set_env KEY VALUE
  local key="$1" val="$2"
  if grep -q "^$key=" "$ENV_FILE" 2>/dev/null; then
    # استبدال بلا تفسير للمحتوى؛ القيمة تُمرَّر عبر stdin لا كوسيط سطر أوامر
    # مرئي في `ps` أثناء التشغيل.
    printf '%s' "$val" | python3 - "$ENV_FILE" "$key" <<'EOF'
import sys
p, k = sys.argv[1], sys.argv[2]
v = sys.stdin.read()
lines = open(p, encoding="utf-8").read().splitlines()
lines = [l for l in lines if not l.startswith(k + "=")] + [f"{k}={v}"]
open(p, "w", encoding="utf-8").write("\n".join(lines) + "\n")
EOF
  else
    printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
  fi
}

# إيميل + باسورد الواجهة: يُطلبان أثناء الإعداد، يُحفظان في .env، وبهما يدخل المستخدم مباشرة.
# (متغيرا البيئة UI_EMAIL_PRESET/UI_PASSWORD_PRESET للتشغيل غير التفاعلي فقط،
#  ولا يُحفَظان في أي ملف — يُستخدَمان مرة واحدة لتوليد المشتق المخزّن ثم يُمسَحان.)
UI_EMAIL="$(get_env UI_EMAIL)"
if [[ -n "$UI_EMAIL" ]]; then
  ok "الواجهة محميّة مسبقاً بإيميل: $UI_EMAIL"
else
  INTERACTIVE=0; [[ -t 0 ]] && INTERACTIVE=1
  # في وضع غير تفاعلي (--yes) أو بدون طرفية: البيانات من البيئة أو تُرفَض مبكراً
  # قبل أي تنزيل (لا تنزيل 20GB ثم فشل في النهاية).
  # ملاحظة: تُمرَّر في نفس سطر الأمر (UI_EMAIL_PRESET=... ./install.sh --yes)
  # لا في ملف — وتُستخدَم مرة واحدة لتوليد المشتق المخزّن ثم تُمسَح.
  if [[ "$INTERACTIVE" == "0" || "$ASSUME_YES" == "1" ]]; then
    UI_EMAIL_IN="${UI_EMAIL_PRESET:-}"
    if [[ ! "$UI_EMAIL_IN" =~ ^[A-Za-z0-9._%+-]+@gmail\.com$ ]]; then
      die "وضع غير تفاعلي بدون إيميل صالح: أعد الأمر هكذا: UI_EMAIL_PRESET=you@gmail.com UI_PASSWORD_PRESET='...' ./install.sh --yes ."
    fi
    UI_EMAIL="$UI_EMAIL_IN"
    if [[ -z "${UI_PASSWORD_PRESET:-}" || ${#UI_PASSWORD_PRESET} -lt 6 ]]; then
      die "وضع غير تفاعلي بدون باسورد صالح: أعد الأمر هكذا: UI_EMAIL_PRESET=you@gmail.com UI_PASSWORD_PRESET='...' ./install.sh --yes ."
    fi
    UI_PASS1="$UI_PASSWORD_PRESET"
  else
    for _ in 1 2 3; do
      read -r -p "اكتب إيميل Gmail صالح: " UI_EMAIL_IN || die "انتهى الإدخال قبل إدخال الإيميل — أعد الأمر في طرفية تفاعلية."
      UI_EMAIL_IN="$(printf '%s' "$UI_EMAIL_IN" | tr -d '[:space:]')"
      if [[ "$UI_EMAIL_IN" =~ ^[A-Za-z0-9._%+-]+@gmail\.com$ ]]; then
        UI_EMAIL="$UI_EMAIL_IN"; break
      fi
      warn "الإيميل غير صالح — لازم يكون @gmail.com فقط"
    done
    [[ -n "$UI_EMAIL" ]] || die "ما وصلنا لإيميل صالح — أعد الأمر."

    while true; do
      read -r -s -p "اكتب باسورد الواجهة (6 أحرف على الأقل): " UI_PASS1 || { echo; die "انتهى الإدخال قبل إدخال الباسورد — أعد الأمر في طرفية تفاعلية."; }
      echo
      [[ ${#UI_PASS1} -ge 6 ]] || { warn "قصير جداً."; continue; }
      read -r -s -p "أعد كتابة الباسورد: " UI_PASS2 || { echo; die "انتهى الإدخال قبل تأكيد الباسورد — أعد الأمر في طرفية تفاعلية."; }
      echo
      [[ "$UI_PASS1" == "$UI_PASS2" ]] || { warn "غير متطابقين."; continue; }
      break
    done
  fi

  UI_PASSWORD_SALT=$(openssl rand -hex 16 2>/dev/null || python3 -c "import secrets;print(secrets.token_hex(16))")
  # الباسورد يُمرَّر عبر stdin (لا يظهر في قائمة العمليات ولا يُسجَّل).
  UI_PASSWORD_HASH=$(printf '%s' "$UI_PASS1" | python3 - "$UI_PASSWORD_SALT" <<'EOF'
import hashlib, sys
pw = sys.stdin.buffer.read().decode("utf-8", "replace")
print(hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(sys.argv[1]), 200000).hex())
EOF
)
  unset UI_PASS1 UI_PASS2
  unset UI_PASSWORD_PRESET 2>/dev/null || true
  SESSION_SECRET=$(openssl rand -hex 32 2>/dev/null || python3 -c "import secrets;print(secrets.token_hex(32))")

  set_env UI_EMAIL "$UI_EMAIL"
  set_env UI_PASSWORD_SALT "$UI_PASSWORD_SALT"
  set_env UI_PASSWORD_HASH "$UI_PASSWORD_HASH"
  set_env SESSION_SECRET "$SESSION_SECRET"
  ok "تم حفظ بيانات الواجهة في .env (الباسورد مخزّن مجزّأً فقط، غير مطبوع)."
fi

# توكن النماذج: يُولَّد أول مرة فقط ويُحفظ في .env ويُعاد استخدامه دائماً.
API_TOKEN="$(get_env API_TOKEN)"
if [[ -z "$API_TOKEN" ]]; then
  API_TOKEN=$(openssl rand -hex 32 2>/dev/null || python3 -c "import secrets;print(secrets.token_hex(32))")
  set_env API_TOKEN "$API_TOKEN"
  ok "تم توليد توكن النماذج (سري) وحفظه."
else
  ok "توكن النماذج موجود مسبقاً (يُعاد استخدامه)."
fi
set_env PORT "$PORT"

# GPU داخل الحاوية؟ الاختبار الحقيقي الوحيد: torch.cuda داخل الـ image المبني.
# الترتيب الصحيح: بناء الصورة أولاً (بقناة torch المناسبة)، ثم سؤال torch نفسه.
say "بناء صورة Docker (الكود والمكتبات فقط)..."
docker compose build --build-arg TORCH_INDEX_URL="$TORCH_INDEX_URL" || die "فشل بناء الـ image."
ok "تم بناء الـ image."

TORCH_CUDA=0
if [[ "$GPU_OK" == "1" ]]; then
  if TORCH_CUDA=$(docker run --rm --gpus all "$IMAGE" python -c "import torch;print(1 if torch.cuda.is_available() else 0)" 2>/dev/null); then
    [[ "$TORCH_CUDA" == "1" ]] || TORCH_CUDA=0
  else
    TORCH_CUDA=0
  fi
fi
if [[ "$TORCH_CUDA" == "1" ]]; then
  cat > docker-compose.override.yml <<'YAML'
# مولّد تلقائياً بواسطة install.sh (يُحذف تلقائياً في وضع CPU).
services:
  models-server:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
YAML
  ok "GPU مفعّلة للحاوية وtorch يراها فعلاً (بأقصى سرعة: fp16/bf16 تلقائياً)."
else
  rm -f docker-compose.override.yml
  [[ "$GPU_OK" == "1" ]] && warn "GPU موجودة لكن torch داخل الحاوية لا يراها (Toolkit ناقص أو تعريف قديم) — التشغيل على CPU." \
                         || warn "وضع CPU."
fi

# النماذج المختارة فقط تُحمَّل وتُعرَض وتُفحَص (env تُقرأ عند إقلاع الحاوية):
# تُفلتَر على معرّفات models.yaml نفسها (المصدر الوحيد) حتى لا يدخل اسم غريب أبداً.
SELECTED_MODELS="$(python3 - <<'EOF' 2>/dev/null || echo ""
import json, yaml
valid = {m["id"] for m in yaml.safe_load(open("models.yaml", encoding="utf-8")).get("models", [])}
try:
    sel = json.load(open("selection.json", encoding="utf-8"))
    print(",".join(e["id"] for e in sel if e.get("id") in valid))
except Exception:
    print("")
EOF
)"
set_env SELECTED_MODELS "$SELECTED_MODELS"
[[ -n "$SELECTED_MODELS" ]] && say "النماذج المطلوبة في هذا التشغيل: $SELECTED_MODELS" || say "كل النماذج مطلوبة."

export PORT
if [[ "$FORCE_RECREATE" == "1" ]]; then
  say "إعادة إنشاء الحاوية من الصفر (--force-recreate)..."
  docker compose up -d --force-recreate || die "فشل تشغيل الحاوية."
else
  docker compose up -d || die "فشل تشغيل الحاوية."
fi
say "في انتظار تحميل النماذج في الذاكرة (قد يطول أول مرة خصوصاً على CPU)..."
HEALTHY=0
for n in $(seq 1 90); do
  if curl -fsS "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q '"ok": *true'; then
    HEALTHY=1; ok "الخادم صحيح وكل النماذج المطلوبة محمّلة."; break
  fi
  # كل دقيقتين نطمئن المستخدم أننا ما زلنا ننتظر (البناء الكامل قد يطول على CPU)
  if [[ $(( n % 6 )) == 0 ]]; then
    say "ما زلنا ننتظر تحميل النماذج... ($(( n * 20 / 60 )) دقيقة مرت، الأقصى 30)"
  fi
  sleep 20
done
[[ "$HEALTHY" == "1" ]] || die "النماذج لم تكتمل خلال المهلة. راجع: docker compose logs"

# ============================================================
# المرحلة 7: فحص فعلي
# ============================================================
echo ""
say "المرحلة 7: فحص فعلي للنماذج المطلوبة"
# ملاحظة: التوكن يُمرَّر متغير بيئة للحاوية مباشرةً (env) لا كوسيط سطر أوامر
# مرئي في `ps` على المضيف — verify.py يرسله داخل ترويسة Bearer فقط.
if ! docker compose exec -T -e API_TOKEN -e SELECTED_MODELS \
    models-server python -u /app/verify.py; then
  die "فشل فحص نموذج أو أكثر (الأسماء والأسباب فوق). راجع: docker compose logs"
fi

# ============================================================
# المرحلة 8: النهاية + الروابط
# ============================================================
PUB_IP=$(curl -s --max-time 8 https://ifconfig.me 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')
if [[ -n "${PUB_IP:-}" && "$PUB_IP" != 127.* && "$PUB_IP" != 10.* && "$PUB_IP" != 192.168.* && "$PUB_IP" != 172.* ]]; then
  HOST="$PUB_IP"
else
  HOST="localhost"
fi

echo ""
echo "=================================================="
echo "🎉 تم بنجاح! كل النماذج تعمل وسيرفر الويب جاهز."
echo ""
echo "🖥️  رابط الواجهة (بدون توكن — سجّل الدخول مباشرةً):"
echo "     http://$HOST:$PORT/"
echo "     الإيميل:   $UI_EMAIL"
echo "     الباسورد:  نفس الباسورد اللي كتبته أثناء الإعداد"
echo ""
echo "🔑 رابط/توكن النماذج (للأيجنت — مطلوب في كل الطلبات):"
echo "     http://$HOST:$PORT   ← ضعه كـ API_TOKEN"
echo "     $API_TOKEN"
echo ""
echo "🩺 فحص الصحة (عام): http://$HOST:$PORT/health"
echo "=================================================="