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
    # workspace is DISCOVERABLE (behind tool_search), never in-context core
    assert not any(s.get("name") == "workspace" for s in list_core_specs())
    from core.tools.registry import list_discoverable_specs as _list_disc
    assert any(s.get("name") == "workspace" for s in _list_disc())
    _, user_ctx, _ = build_context("hi")
    assert "workspace" in user_ctx and "[Workspace" in user_ctx
    print("wiring OK")
    print("ALL WORKSPACE TESTS PASSED")
finally:
    cleanup()


def test_downloads_virtual_merge():
    """Virtual browser-downloads folder: merged view, read-only, no real clash."""
    import tempfile
    import shutil as _sh
    import core.tools.workspace.tool as _wstool
    lib = Path(tempfile.mkdtemp(prefix="lib-dl-"))
    (lib / "movie.mp4").write_bytes(b"0" * 64)
    (lib / "sub").mkdir()
    (lib / "sub" / "disk.iso").write_bytes(b"1" * 32)
    real_fn = _wstool.library_downloads_dir
    _wstool.library_downloads_dir = lambda: lib
    try:
        from core.paths import workspace_root as _ws
        # 1. root merges the virtual folder flagged external
        top = execute("list")
        dl = [e for e in top["entries"] if e["name"] == "downloads"]
        assert len(dl) == 1 and dl[0]["type"] == "folder" and dl[0].get("external") is True, top
        # 2. inside: files + folders flagged external
        inside = execute("list", path="downloads")
        by_name = {e["name"]: e for e in inside["entries"]}
        assert by_name["movie.mp4"].get("external") is True, inside
        assert by_name["sub"]["type"] == "folder" and by_name["sub"].get("external") is True, inside
        # 3. nested level works too
        deep = execute("list", path="downloads/sub")
        assert deep["entries"][0]["name"] == "disk.iso", deep
        # 4. mutations blocked with a clear error
        for bad in [lambda: execute("delete_file", path="downloads", name="movie.mp4"),
                    lambda: execute("create_folder", path="downloads", name="x")]:
            try:
                bad()
                raise AssertionError("virtual write not blocked")
            except ValueError:
                pass
        # 5. crafted paths blocked
        try:
            execute("list", path="downloads/../..")
            raise AssertionError("traversal not blocked")
        except (ValueError, FileNotFoundError):
            pass
        # 6. a real Workspace/downloads folder wins; virtual hides
        real = _ws() / "downloads"
        real.mkdir(parents=True, exist_ok=True)
        try:
            (real / "mine.txt").write_text("real", encoding="utf-8")
            top2 = execute("list")
            dl2 = [e for e in top2["entries"] if e["name"] == "downloads"]
            assert len(dl2) == 1 and dl2[0].get("external") is not True, top2
            real_inside = execute("list", path="downloads")
            assert [e["name"] for e in real_inside["entries"]] == ["mine.txt"], real_inside
        finally:
            _sh.rmtree(str(real), ignore_errors=True)
        print("downloads virtual merge OK")
    finally:
        _wstool.library_downloads_dir = real_fn
        _sh.rmtree(str(lib), ignore_errors=True)