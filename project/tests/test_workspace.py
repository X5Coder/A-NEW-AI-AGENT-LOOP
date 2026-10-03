import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from core.tools.workspace.tool import execute
from core.agent.loop.dispatcher import execute_tool
from core.tools.registry import list_core_specs
from core.agent.context.builder import build_context, _workspace_block

TAG = "ws_test_tmp"
created = []


def cleanup():
    import shutil
    try:
        sys.path.insert(0, str(project_root))
        from core.paths import workspace_root
        ws = workspace_root()
        for name in (TAG, TAG + "_live"):
            p = ws / name
            if p.exists():
                shutil.rmtree(p, ignore_errors=True)
    except Exception:
        pass


try:
    cleanup()
    # 1. list root
    out = execute("list")
    assert out.get("success") and isinstance(out.get("entries"), list), out
    print("list root OK")

    # 2. create folder + nested file invisible at top level
    r = execute("create_folder", name=TAG)
    assert r.get("success"), r
    created.append(TAG)
    from core.paths import workspace_root as _ws
    inner = _ws() / TAG / "a.txt"
    inner.write_text("hi", encoding="utf-8")
    live = _ws() / (TAG + "_live")
    live.mkdir(parents=True, exist_ok=True)
    created.append(TAG + "_live")
    for i in range(3):
        (live / f"f{i}.txt").write_text("x", encoding="utf-8")
    top = execute("list")
    names = {e["name"]: e for e in top["entries"]}
    assert TAG in names and names[TAG]["type"] == "folder", top
    print("create + list OK")

    # 3. context shows folders only, with counts
    block = _workspace_block()
    assert f"folder: {TAG}/" in block, block
    assert f"folder: {TAG + '_live'}/ (3 items" in block, block
    assert "a.txt" not in block, block
    print("context top-level-only OK")

    # 4. escape blocked
    for bad in [lambda: execute("list", path=".."),
                lambda: execute("create_folder", path="..", name="x"),
                lambda: execute("create_folder", name="a/b"),
                lambda: execute("create_folder", name=".."),
                lambda: execute("nope")]:
        try:
            bad()
            raise AssertionError("escape not blocked")
        except (ValueError, TypeError, FileNotFoundError):
            pass
    print("jail OK")

    # 5. dispatcher + registry + L7 wiring
    d = execute_tool("workspace", {"action": "list"})
    assert d.get("success"), d
    assert any(s.get("name") == "workspace" for s in list_core_specs())
    _, user_ctx, _ = build_context("hi")
    assert "workspace" in user_ctx and "[Workspace" in user_ctx
    print("wiring OK")
    print("ALL WORKSPACE TESTS PASSED")
finally:
    cleanup()
