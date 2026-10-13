"""svg_maker validation tests (no LLM, no network, deterministic)."""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from plugins.core_plugins.svg_maker import tool as svg_tool
from plugins.core_plugins.svg_maker import events as svg_events


def test_spec_loads_and_names_tool():
    spec = json.loads((PROJECT_ROOT / "plugins" / "core_plugins" / "svg_maker" / "spec.json").read_text(encoding="utf-8"))
    assert spec["name"] == "svg_maker"
    assert spec["inputs"]["description"]["required"] is True
    assert "svg" in spec["description"].lower()


def test_system_prompt_exists_and_forbids_scripts():
    text = (PROJECT_ROOT / "prompts" / "svg_icon_system.md").read_text(encoding="utf-8")
    assert "<svg" in text and "<script>" in text


def test_rejects_empty_description_and_bad_size():
    with pytest.raises(ValueError):
        svg_tool.execute(description="   ")
    with pytest.raises(ValueError):
        svg_tool.execute(description="cart icon", size=5)
    with pytest.raises(ValueError):
        svg_tool.execute(description="cart icon", size=1024)


def test_rejects_bad_icon_name():
    with pytest.raises(ValueError):
        svg_tool.execute(description="cart icon", icon_name="../evil")


def test_sanitize_removes_scripts_and_handlers():
    dirty = '<svg onclick="evil()"><circle cx="12"/><script>alert(1)</script></svg>'
    clean = svg_tool._sanitize_svg(dirty)
    assert "<script" not in clean.lower()
    assert "onclick" not in clean.lower()


def test_extract_svg_prefers_document():
    raw = "intro\n```svg\n<svg><circle cx=\"12\"/></svg>\n```"
    assert "<svg>" in svg_tool._extract_svg(raw).lower()


def test_validate_svg_rejects_empty_script_and_non_svg():
    with pytest.raises(ValueError):
        svg_tool._validate_svg("   ")
    with pytest.raises(ValueError):
        svg_tool._validate_svg("<svg><script>x</script></svg>")
    with pytest.raises(ValueError):
        svg_tool._validate_svg("<div>no svg</div>")
    ok = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/></svg>'
    assert svg_tool._validate_svg(ok) == ok


def test_events_and_dispatcher_registered():
    assert svg_events.SHOW_ARROW is True
    assert "Creating icon" in svg_events.format_event(description="cart", icon_name="cart")
    from core.agent.loop.dispatcher import _DISCOVERABLE_MAP

    assert _DISCOVERABLE_MAP.get("svg_maker") == "plugins.core_plugins.svg_maker.tool"
