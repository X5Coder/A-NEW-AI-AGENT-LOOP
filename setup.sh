#!/usr/bin/env bash
# X5Agent one-liner setup (Linux / Mac).
# Installs + starts the console in a single command:
#   curl -fsSL https://raw.githubusercontent.com/X5Coder/X5Agent/main/setup.sh | bash
# With options (passed through to project/install.py):
#   curl -fsSL .../setup.sh | bash -s -- --email owner@mail.com --password S3cret123 --public
# Re-running updates the source only (git pull --ff-only, data never touched).
set -euo pipefail

REPO_URL="https://github.com/X5Coder/X5Agent"
DIR="${X5_DIR:-$HOME/X5Agent}"

if ! command -v git >/dev/null 2>&1; then
  echo "[setup] ERROR: git is missing. Install it first: sudo apt install -y git" >&2
  exit 1
fi

if [ -d "$DIR/.git" ]; then
  echo "[setup] updating existing copy: $DIR"
  git -C "$DIR" pull --ff-only || echo "[setup] WARN: git pull failed, continuing with current code"
else
  echo "[setup] cloning into: $DIR"
  git clone "$REPO_URL" "$DIR"
fi

PY=""
for cand in python3.12 python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then
    PY="$cand"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "[setup] ERROR: no python found. Install python 3.12: sudo apt install -y python3.12" >&2
  exit 1
fi
echo "[setup] python: $($PY --version 2>&1)"

# Default to --run (install + start) unless the caller opts out with --no-run.
ARGS=("$@")
WANT_RUN=1
for a in "$@"; do
  if [ "$a" = "--no-run" ]; then
    WANT_RUN=0
    break
  fi
done

cd "$DIR/project"
if [ "$WANT_RUN" = "1" ]; then
  exec "$PY" install.py --run "${ARGS[@]}"
else
  FILTERED=()
  for a in "${ARGS[@]}"; do
    if [ "$a" != "--no-run" ]; then
      FILTERED+=("$a")
    fi
  done
  exec "$PY" install.py "${FILTERED[@]:-}"
fi
