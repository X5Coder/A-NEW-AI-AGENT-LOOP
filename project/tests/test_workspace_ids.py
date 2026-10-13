"""Workspace file ids + missing-model pause + pdf internal context (deterministic)."""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def test_workspace_list_files_carry_stable_ids():
    from core.tools.workspace.tool import execute

    out = execute("list")
    assert out.get("success")
    files = [e for e in out.get("entries", []) if e.get("type") == "file"]
    assert files, "Workspace root should contain files for this test"
    for entry in files:
        assert entry.get("id") == entry.get("name")
        assert entry.get("size", 0) >= 0


def test_workspace_subdir_ids_are_relative_paths():
    import shutil
    import uuid as _uuid

    from core.paths import workspace_root
    from core.tools.workspace.tool import execute

    rel = f"UT_WsId_{_uuid.uuid4().hex[:6]}"
    target = workspace_root() / rel
    try:
        target.mkdir(parents=True, exist_ok=True)
        (target / "a.txt").write_text("x", encoding="utf-8")
        out = execute("list", path=rel)
        assert out.get("success")
        by_name = {e["name"]: e for e in out["entries"]}
        assert by_name["a.txt"]["id"] == f"{rel}/a.txt"
    finally:
        shutil.rmtree(str(target), ignore_errors=True)


def test_builder_map_shows_file_ids():
    from core.agent.context.builder import _workspace_block

    block = _workspace_block()
    if "empty" in block:
        pytest.skip("empty Workspace")
    assert 'id: "' in block


def test_resolve_missing_model_pauses_then_skips():
    from core.agent.loop.engine import _resolve_missing_model

    calls = []

    def fake_question(kind, payload):
        calls.append((kind, payload))
        return {"decision": "skip"}

    res = _resolve_missing_model("vision_analyze", {}, "vision", fake_question)
    assert len(calls) == 1 and calls[0][0] == "needs_model"
    assert calls[0][1]["tool"] == "vision_analyze"
    assert res["success"] is True and res["output"]["skipped"] is True


def test_resolve_missing_model_without_ui_skips_directly():
    from core.agent.loop.engine import _resolve_missing_model

    res = _resolve_missing_model("vision_analyze", {}, "vision", None)
    assert res["success"] is True
    assert res["output"]["needs_model_kind"] == "vision"


def test_pdf_internal_context_shows_tools_and_workspace():
    from plugins.core_plugins.pdf_create.tool import _internal_context

    ctx = _internal_context()
    assert "pdf_create" in ctx and "tool_search" in ctx
    assert "Workspace" in ctx
    assert len(ctx) <= 12000
