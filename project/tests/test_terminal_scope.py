"""Terminal scope: limited_access jails to Workspace, full_access runs anywhere.
No per-command question in either scope — scope file decides reach only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.tools.terminal import tool as term


def _set_scope(tmp_path, scope):
    import core.paths as _paths
    db = tmp_path / "db"
    db.mkdir(parents=True, exist_ok=True)
    (db / "terminal_access.json").write_text(json.dumps({"scope": scope}), encoding="utf-8")
    _orig = _paths.data_db_file
    _paths.data_db_file = lambda name: db / name  # type: ignore
    try:
        yield_scope = term._access_scope()
    finally:
        _paths.data_db_file = _orig
    return yield_scope


def test_default_is_limited(tmp_path, monkeypatch):
    import core.paths as _paths
    monkeypatch.setattr(_paths, "data_db_file", lambda name: tmp_path / "missing.json")
    assert term._access_scope() == "limited_access"


def test_limited_blocks_escape(tmp_path, monkeypatch):
    import core.paths as _paths
    db = tmp_path / "db"
    db.mkdir(parents=True, exist_ok=True)
    (db / "terminal_access.json").write_text(json.dumps({"scope": "limited_access"}), encoding="utf-8")
    monkeypatch.setattr(_paths, "data_db_file", lambda name: db / name)
    assert term._access_scope() == "limited_access"
    try:
        term._validate_command("cat C:\\Windows\\System32\\drivers\\etc\\hosts")
        assert False, "must block absolute path outside Workspace"
    except ValueError:
        pass


def test_full_allows_any_command(tmp_path, monkeypatch):
    import core.paths as _paths
    db = tmp_path / "db"
    db.mkdir(parents=True, exist_ok=True)
    (db / "terminal_access.json").write_text(json.dumps({"scope": "full_access"}), encoding="utf-8")
    monkeypatch.setattr(_paths, "data_db_file", lambda name: db / name)
    assert term._access_scope() == "full_access"
    assert term._validate_command("cat C:\\Windows\\System32\\drivers\\etc\\hosts")
