"""Gateway studio links: tool returns the link, gateway delivers it clickable."""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _tailscale_path():
    from core.paths import data_root as _data_root
    return _data_root() / "database" / "tailscale.json"


def test_tool_returns_link_for_model_and_gateway():
    import shutil
    from plugins.core_plugins.pdf_studio import tool as _studio
    from core.studio import store as _store
    created = _studio.execute(title="UT GW Link")
    try:
        sid = created["studio_id"]
        # The tool hands the model everything it needs to reply with a link.
        assert created["open_url"] == f"/studio-pdf/{sid}"
        assert created["open_card"] == f"[STUDIO:{sid}]"
        assert f"/studio-pdf/{sid}" in created["message"]
    finally:
        shutil.rmtree(str(_store.studios_root() / created["studio_id"]), ignore_errors=True)


def test_expand_prefers_funnel_rewrites_loopback():
    from gateway import dispatch as _gd
    from core.paths import data_root as _data_root
    path = _tailscale_path()
    backup = path.read_text(encoding="utf-8") if path.is_file() else None
    try:
        _data_root().mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"public_url": "https://demo.tail123.ts.net"}),
                        encoding="utf-8")
        out = _gd._expand_studio_links("done [STUDIO:pdf_7]")
        assert "https://demo.tail123.ts.net/studio-pdf/pdf_7" in out
        assert "[STUDIO:" not in out
        out2 = _gd._expand_studio_links("open http://127.0.0.1:7777/studio-pdf/pdf_7 now")
        assert "https://demo.tail123.ts.net/studio-pdf/pdf_7" in out2
        assert "127.0.0.1" not in out2
        out3 = _gd._expand_studio_links("see /studio-pdf/pdf_7")
        assert "https://demo.tail123.ts.net/studio-pdf/pdf_7" in out3
    finally:
        try:
            if backup is None:
                if path.is_file():
                    path.unlink()
            else:
                path.write_text(backup, encoding="utf-8")
        except Exception:
            pass


def test_expand_falls_back_without_funnel():
    from gateway import dispatch as _gd
    path = _tailscale_path()
    backup = path.read_text(encoding="utf-8") if path.is_file() else None
    try:
        if path.is_file():
            path.unlink()
        out = _gd._expand_studio_links("done [STUDIO:pdf_7]")
        assert "/studio-pdf/pdf_7" in out and "[STUDIO:" not in out
        assert _gd._expand_studio_links("plain text") == "plain text"
    finally:
        try:
            if backup is not None:
                path.write_text(backup, encoding="utf-8")
        except Exception:
            pass
