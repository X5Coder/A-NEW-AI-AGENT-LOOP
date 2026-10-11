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
# Official Google Chrome: the X5Browser engine (x5browser>=1.5 accepts the
# original Chrome only, never Chromium) for headless runs on servers without
# a display; the library finds `google-chrome` on PATH by itself.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git wget \
 && wget -q -O /tmp/google-chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
 && apt-get install -y /tmp/google-chrome.deb \
 && rm -rf /var/lib/apt/lists/* /tmp/google-chrome.deb

WORKDIR /app

# .dockerignore keeps live data (X5Agent/), weights (*.safetensors) and
# secrets (.env) OUT of the image. .git stays: the entrypoint pulls updates.
COPY . /app

# X5Agent/Data bootstrap: the manager builds single rooms from the STORED
# SOURCE TREE (not from this image) and replays the Dockerfile build steps
# there — so baking live paths here is what seeds the persistent binds.
# This must NOT bake weights (1.1GB *.safetensors): the stored tree would
# double the ~5MB code into gigabytes. Configs only; the bootstrap fetches
# weights once at runtime into the persistent store.
RUN mkdir -p X5Agent/Data/database X5Agent/Data/vector \
 && (cp -r project/embedding_model/. X5Agent/Data/.seed/ 2>/dev/null || true) \
 && (find X5Agent -name '*.safetensors' -delete 2>/dev/null || true)
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
