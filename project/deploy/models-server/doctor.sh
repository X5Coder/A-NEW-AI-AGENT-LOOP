#!/usr/bin/env bash
# ============================================================
# أمر doctor: يعرض حالة كل شيء (خصوصاً النماذج)، ومنه تختار
# النموذج غير المحمّل أو غير المكتمل وتحمّله كامل.
#
# الاستخدام:
#   ./doctor.sh            # حالة كاملة + اختيار + تنزيل
#   ./doctor.sh --check    # حالة فقط بدون تنزيل
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

MODELS_DIR="$(cd "$SCRIPT_DIR/../../models" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
IMAGE="x5-models-server"
CHECK_ONLY=0
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=1

say()  { echo "==> $*"; }
ok()   { echo "✅ $*"; }
warn() { echo "⚠️  $*"; }

cleanup_tmp_reserves() { rm -f "$SCRIPT_DIR/.reserve-total" 2>/dev/null || true; }
trap 'cleanup_tmp_reserves' INT TERM EXIT

echo "=================================================="
echo "  doctor — حالة النظام والنماذج"
echo "=================================================="

# ---------- البيئة ----------
say "البيئة"
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  ok "Docker يعمل"
else
  warn "Docker غير متاح — التنزيل والتشغيل لن يعملوا"
fi
command -v python3 >/dev/null 2>&1 && ok "Python موجود" || warn "Python غير موجود"
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
  VRAM_MB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | awk '{s+=$1} END{print s+0}')
  ok "GPU متاحة (VRAM ${VRAM_MB}MB)"
else
  warn "لا GPU — التشغيل على CPU (أبطأ خصوصاً الصوت)"
fi
RAM_MB=$(free -m 2>/dev/null | awk '/^Mem:/{print $2}' || echo 0)
RAM_MB=${RAM_MB:-0}  # حارس: awk قد يطبع سطراً فارغاً فينجو || وتفشل الحسابات
echo "    RAM: ${RAM_MB}MB"
mkdir -p "$MODELS_DIR"
echo "    المساحة المتاحة في $MODELS_DIR: $(df -Pm "$MODELS_DIR" | awk 'NR==2{gsub("M","",$4); print int($4/1024)"GB"}')"
[[ -f "$ENV_FILE" ]] && ok "إعدادات/توكن موجودة" || warn "إعدادات/توكن غير موجودة (شغّل ./install.sh)"
docker image inspect "$IMAGE" >/dev/null 2>&1 && ok "صورة Docker مبنية" || warn "صورة Docker غير مبنية (شغّل ./install.sh)"

# ---------- النماذج ----------
echo ""
say "النماذج"
python3 select_models.py --table || true

if [[ "$CHECK_ONLY" == "1" ]]; then
  echo ""
  echo "لفحص المشروع كاملاً: python3 ../../scripts/doctor.py"
  exit 0
fi

# ---------- الاختيار + التنزيل ----------
echo ""
say "اختر النموذج غير المحمّل أو غير المكتمل لتحميله كامل"
SELECTION_IDS=$(python3 select_models.py) || { echo "لم تختر شيئاً."; exit 0; }
[[ -n "$SELECTION_IDS" ]] || { echo "لم تختر شيئاً."; exit 0; }

read -r NEED_MB < <(python3 - <<'EOF'
import json
sel = json.loads(open("selection.json", encoding="utf-8").read())
need = sum(int(e["bytes"]) for e in sel if e["status"] != "ready")
print(need // (1024*1024))
EOF
)
NEED_MB=${NEED_MB:-0}
RESERVE_GB="$(python3 -c "import yaml;print(int(yaml.safe_load(open('models.yaml')).get('reserve_gb',5)))" 2>/dev/null || echo 5)"
AV_MODELS_MB=$(df -Pm "$MODELS_DIR" | awk 'NR==2{gsub("M","",$4); print $4}')
if [[ "$AV_MODELS_MB" -lt $(( NEED_MB + RESERVE_GB * 1024 )) ]]; then
  warn "المساحة لا تكفي. المتاح: $(( AV_MODELS_MB / 1024 ))GB — والمطلوب: $(( (NEED_MB + RESERVE_GB*1024) / 1024 ))GB."
  exit 1
fi
if command -v fallocate >/dev/null 2>&1 && [[ "$NEED_MB" -gt 0 ]]; then
  fallocate -l "$(( NEED_MB ))M" "$SCRIPT_DIR/.reserve-total" 2>/dev/null || true
fi

echo ""
export X5_RESERVE_FILE="$SCRIPT_DIR/.reserve-total"
export HF_TOKEN="${HF_TOKEN:-${HUGGINGFACE_HUB_TOKEN:-}}"
python3 download_models.py || { warn "فشل تنزيل بعض النماذج (الأسماء فوق)."; exit 1; }
cleanup_tmp_reserves

echo ""
say "فحص المشروع كاملاً (doctor.py)"
python3 "$SCRIPT_DIR/../../scripts/doctor.py" || warn "المدقق وجد ملاحظات فوق."
ok "تم."