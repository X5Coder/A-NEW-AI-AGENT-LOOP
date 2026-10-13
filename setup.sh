#!/usr/bin/env bash
# X5Agent one-liner setup (Linux / Mac).
# Installs + starts the console PERSISTENTLY in a single command
# (detached: safe to close SSH; re-running kills the old server and
# starts a fresh one on the same port 7777; stop with `x5agent --off`):
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
# stdin fix (the classic curl|bash problem): when launched as
# `curl ... | bash`, this shell's stdin is the finished pipe (EOF), so the
# installer's interactive prompts (email/password) would die instantly.
# Recover the real terminal for prompts when there is one behind the pipe.
if [ -t 0 ]; then
  TTY_IN="/dev/stdin"    # already a terminal, keep it
elif [ -e /dev/tty ]; then
  TTY_IN="/dev/tty"      # pipe/file stdin but a terminal exists: use it
else
  TTY_IN="/dev/stdin"    # truly headless: flags/env must supply everything
fi
if [ "$WANT_RUN" = "1" ]; then
  # shellcheck disable=SC2094
  exec "$PY" install.py --run "${ARGS[@]}" < "$TTY_IN"
else
  FILTERED=()
  for a in "${ARGS[@]}"; do
    if [ "$a" != "--no-run" ]; then
      FILTERED+=("$a")
    fi
  done
  # shellcheck disable=SC2094
  exec "$PY" install.py "${FILTERED[@]:-}" < "$TTY_IN"
fi
