# X5Agent runtime image — code only, no user data, no secrets.
#
# Data safety contract (enforced by compose.yml, not here):
#   ALL local data lives in named volumes:
#     x5-data   -> /app/X5Agent/Data            (database, vectors, files, skills, configs, .env)
#     x5-models -> /app/project/embedding_model (1.1GB weights, downloaded once, then reused)
# Rebuilding or re-pulling this image NEVER deletes those volumes.
#
# Single-command flow:  docker compose up --build
#   build  = fresh image with current code + dependencies
#   start  = docker-entrypoint.py pulls newest code from GitHub (--ff-only,
#            source only) then exec()s the console. Volumes are never touched.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# git only: entrypoint refreshes source via `git pull --ff-only` on every start.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# .dockerignore keeps live data (X5Agent/), weights (*.safetensors) and
# secrets (.env) OUT of the image. .git stays: the entrypoint pulls updates.
COPY . /app

# Seed copy of the tracked embedding configs: at runtime a named volume
# shadows /app/project/embedding_model, so the entrypoint re-seeds the
# volume from here when it is empty (weights download once, then persist).
RUN mkdir -p /app/.seed-embedding \
 && cp -r /app/project/embedding_model/. /app/.seed-embedding/ 2>/dev/null || true

RUN pip install --quiet ./project

EXPOSE 7777

ENTRYPOINT ["python", "docker-entrypoint.py"]
