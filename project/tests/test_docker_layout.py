"""Docker one-command layout: data in volumes, code from GitHub, never mixed.

Stdlib only, fully offline: asserts the static contract between Dockerfile,
compose.yml, .dockerignore and docker-entrypoint.py so that:
  - every local-data path resolves inside a NAMED volume (never baked, never lost)
  - every code refresh is `git pull --ff-only` (source only, bounded)
  - no secrets or live data can leak into the image
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = REPO_ROOT / "project"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _read(name: str) -> str:
    return (REPO_ROOT / name).read_text(encoding="utf-8")


import os as _os  # noqa: E402  (test helper, after path setup)


def test_compose_data_volume_matches_code_paths():
    from core.paths import data_root

    compose = _read("compose.yml")
    # The volume target must be exactly <repo>/X5Agent/Data as resolved by code.
    assert data_root().as_posix().endswith("X5Agent/Data")
    assert "x5-data:/app/X5Agent/Data" in compose
    assert "x5-models:/app/project/embedding_model" in compose
    assert "volumes:" in compose and "x5-data:" in compose and "x5-models:" in compose


def test_compose_has_no_data_bind_mounts():
    compose = _read("compose.yml")
    # Named volumes only: no host bind of live data (./X5Agent, /X5Agent, ~).
    for line in compose.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            assert "./X5Agent" not in stripped, line
            assert ":~/ " not in stripped and ":~/" not in stripped, line
    assert "7777:7777" in compose  # console port mapping
    assert "unless-stopped" in compose


def test_dockerfile_code_only_no_secrets():
    dockerfile = _read("Dockerfile")
    assert "FROM python:3.12" in dockerfile
    assert "git" in dockerfile  # entrypoint refreshes source via git
    assert "EXPOSE 7777" in dockerfile
    assert 'ENTRYPOINT ["python", "docker-entrypoint.py"]' in dockerfile
    for banned in ("tskey-", "sk-", "password=", "API_KEY=", "ADD http"):
        assert banned not in dockerfile, banned


def test_dockerignore_keeps_data_and_secrets_out():
    ignore = _read(".dockerignore")
    assert "X5Agent/" in ignore
    assert ".safetensors" in ignore
    assert ".env" in ignore
    # .git must stay: the entrypoint pulls newest source from GitHub at start.
    assert ".git" not in [ln.strip() for ln in ignore.splitlines()
                          if ln.strip() and not ln.strip().startswith("#")]


def test_entrypoint_source_only_bounded():
    entry = _read("docker-entrypoint.py")
    assert "git" in entry and "--ff-only" in entry
    assert "300" in entry  # bounded pull timeout
    assert "0.0.0.0" in entry and "7777" in entry
    assert "os.execv" in entry  # signals reach the server
    # Never deletes user data: no recursive remove of the data root.
    assert "rm -rf" not in entry
    assert "shutil.rmtree" not in entry
    assert "config.json" in entry  # seed marker for the models volume


def test_entrypoint_pins_data_to_binds():
    """Single-room survival: the store lives ON the manager binds.

    Symlinks alone do not survive a rebuild (fresh container layer), so the
    entrypoint claims /app/data/x5store (a real dir on the bind) and the
    code reads the store first via its marker — file-by-file persistence.
    """
    entry = _read("docker-entrypoint.py")
    assert "def persist_data" in entry
    assert "/app/data" in entry
    assert "x5store" in entry
    assert ".x5store" in entry  # claim marker routes reads to the store
    assert "persist_data()" in entry  # wired before seeding


def test_code_reads_store_first():
    """data_root() + embedding_dir() prefer the persistent store when claimed."""
    import core.paths as _paths

    src = (PROJECT_ROOT / "core" / "paths.py").read_text(encoding="utf-8")
    assert "x5store" in src and ".x5store" in src
    # Local dev (no /app/data): falls back to the normal X5Agent/Data.
      assert str(_paths.data_root()).endswith(_os.path.join("X5Agent", "Data"))
    emb = (PROJECT_ROOT / "core" / "embedding" / "bootstrap.py").read_text(encoding="utf-8")
    assert "x5store" in emb
