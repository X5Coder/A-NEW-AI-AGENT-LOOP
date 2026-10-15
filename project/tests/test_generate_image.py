"""Generate-image card: unified end-of-reply images (no LLM, no network)."""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent.loop.engine import _collect_final_images, _event_media
from core.agent.loop.dispatcher import _DISCOVERABLE_MAP
from plugins.core_plugins.generate_image import tool as gen_tool
from plugins.core_plugins.generate_image.events import format_event


def _ok(out):
    return {"success": True, "output": out}


def test_spec_registered():
    spec = json.loads((PROJECT_ROOT / "plugins" / "core_plugins" / "generate_image" / "spec.json").read_text(encoding="utf-8"))
    assert spec["name"] == "generate_image"
    assert "prompt" in spec["inputs"]
    assert _DISCOVERABLE_MAP.get("generate_image") == "plugins.core_plugins.generate_image.tool"


def test_events_label_bounded():
    assert format_event(prompt="a red cat").startswith("Generating image")
    assert len(format_event(prompt="x" * 500)) <= 80


def test_empty_prompt_rejected():
    try:
        gen_tool.execute(prompt="   ")
    except ValueError as e:
        assert "prompt" in str(e)
    else:
        raise AssertionError("empty prompt must fail")


def test_long_prompt_rejected():
    try:
        gen_tool.execute(prompt="x" * 1001)
    except ValueError as e:
        assert "max 1000" in str(e)
    else:
        raise AssertionError("long prompt must fail")


def test_event_media_carries_generated_image():
    media = _event_media("generate_image", _ok({"image_path": "gen_abc123.png"}))
    assert media and media.get("image") == "gen_abc123.png"


def test_event_media_rejects_traversal():
    media = _event_media("generate_image", _ok({"image_path": "../secret.png"}))
    assert not (media and media.get("image"))


def test_final_images_unifies_shots_and_generated():
    log = [
        {"tool": "screenshot", "result": _ok({"has_screenshot": True, "screenshot_path": "shot1.png"})},
        {"tool": "generate_image", "result": _ok({"image_path": "gen_abc123.png"})},
        {"tool": "generate_image", "result": _ok({"image_path": "gen_abc123.png"})},
        {"tool": "generate_image", "result": _ok({"image_path": "../evil.png"})},
        {"tool": "terminal", "result": _ok({"stdout": "hi"})},
    ]
    images = _collect_final_images(log)
    assert {"shot": "shot1.png"} in images
    assert {"image": "gen_abc123.png"} in images
    assert len(images) == 2


def test_final_images_bounded():
    log = [{"tool": "generate_image", "result": _ok({"image_path": f"gen_{i:04d}.png"})} for i in range(10)]
    assert len(_collect_final_images(log)) == 6
