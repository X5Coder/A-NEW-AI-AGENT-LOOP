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

# git: entrypoint refreshes source via `git pull --ff-only` on every start.
# chromium: the X5Browser engine (headless fallback on servers without a
# display; the library finds it as `chromium` on PATH by itself).
RUN apt-get update \
 && apt-get install -y --no-install-recommends git chromium \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# .dockerignore keeps live data (X5Agent/), weights (*.safetensors) and
# secrets (.env) OUT of the image. .git stays: the entrypoint pulls updates.
COPY . /app

# Baked data for single-room binds: the manager copies the FRESH image dirs
# /app/data + /data into the new binds on first deploy (then they persist).
# The entrypoint claims /app/data/x5store and funnels live data there.
# This must NOT bake weights (1.1GB *.safetensors): the manager stores the
# whole source tree, and baking them doubles the ~5MB code into gigabytes.
RUN mkdir -p /app/data/x5store /data \
 && (cp -r /app/project/embedding_model/. /app/data/x5store/ 2>/dev/null || true) \
 && (cp -r /app/project/embedding_model/. /data/x5store-seed/ 2>/dev/null || true) \
 && (find /app/data/x5store /data/x5store-seed -name '*.safetensors' -delete 2>/dev/null || true)
# Seed copy of the tracked embedding configs: at runtime a named volume
# shadows /app/project/embedding_model, so the entrypoint re-seeds the
# volume from here when it is empty (weights download once, then persist).
# Same rule: configs only, never the 1.1GB weights (see .dockerignore).
RUN mkdir -p /app/.seed-embedding \
 && (cp -r /app/project/embedding_model/. /app/.seed-embedding/ 2>/dev/null || true) \
 && (find /app/.seed-embedding -name '*.safetensors' -delete 2>/dev/null || true)

RUN pip install --quiet ./project

EXPOSE 7777

ENTRYPOINT ["python", "docker-entrypoint.py"]
