"""Gateway studio links: tool returns the link, gateway delivers it clickable."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


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


def test_expand_uses_console_base_and_rewrites_loopback():
    from gateway import dispatch as _gd
    backup = _gd._dashboard_url
    try:
        _gd._dashboard_url = "http://203.0.113.9:7777/"
        out = _gd._expand_studio_links("done [STUDIO:pdf_7]")
        assert "http://203.0.113.9:7777/studio-pdf/pdf_7" in out
        assert "[STUDIO:" not in out
        out2 = _gd._expand_studio_links("open http://127.0.0.1:7777/studio-pdf/pdf_7 now")
        assert "http://203.0.113.9:7777/studio-pdf/pdf_7" in out2
        assert "127.0.0.1" not in out2
        out3 = _gd._expand_studio_links("see /studio-pdf/pdf_7")
        assert "http://203.0.113.9:7777/studio-pdf/pdf_7" in out3
    finally:
        _gd._dashboard_url = backup


def test_expand_falls_back_to_loopback_base():
    from gateway import dispatch as _gd
    backup = _gd._dashboard_url
    try:
        _gd._dashboard_url = ""
        out = _gd._expand_studio_links("done [STUDIO:pdf_7]")
        assert "/studio-pdf/pdf_7" in out and "[STUDIO:" not in out
        assert _gd._expand_studio_links("plain text") == "plain text"
    finally:
        _gd._dashboard_url = backup
