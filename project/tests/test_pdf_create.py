"""pdf_create validation tests (no LLM, no network, deterministic)."""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from plugins.core_plugins.pdf_create import tool as pdf_tool
from plugins.core_plugins.pdf_create import events as pdf_events


def _slide(no=1, **over):
    base = {
        "slide_no": no,
        "title": f"Title {no}",
        "body": f"Body text for slide {no}.",
        "images": [],
        "fonts": [],
        "colors": {"primary": "#1A73E8", "secondary": "#FFFFFF"},
    }
    base.update(over)
    return base


def test_spec_loads_and_has_one_slide_example():
    spec = json.loads((PROJECT_ROOT / "plugins" / "core_plugins" / "pdf_create" / "spec.json").read_text(encoding="utf-8"))
    assert spec["name"] == "pdf_create"
    assert set(spec["inputs"]["action"]["enum"]) == {"create", "update", "get"}
    assert "slide_no" in spec["description"]


def test_system_prompt_exists_and_forbids_scripts():
    text = (PROJECT_ROOT / "prompts" / "pdf_slide_system.md").read_text(encoding="utf-8")
    assert "297mm" in text and "<script>" in text


def test_valid_slides_sorted_and_sequential():
    slides = pdf_tool._validate_slides([_slide(2), _slide(1)])
    assert [s["slide_no"] for s in slides] == [1, 2]


def test_rejects_gapped_numbers():
    with pytest.raises(ValueError):
        pdf_tool._validate_slides([_slide(1), _slide(3)])


def test_rejects_duplicate_numbers():
    with pytest.raises(ValueError):
        pdf_tool._validate_slides([_slide(1), _slide(1, title="Other")])


def test_rejects_bad_place_and_bad_color():
    with pytest.raises(ValueError):
        pdf_tool._validate_slides([_slide(1, images=[{"file": "a.png", "place": "top"}])])
    with pytest.raises(ValueError):
        pdf_tool._validate_slides([_slide(1, colors={"primary": "red"})])


def test_rejects_two_backgrounds_and_traversal_file():
    with pytest.raises(ValueError):
        pdf_tool._validate_slides([_slide(1, images=[
            {"file": "a.png", "place": "background"},
            {"file": "b.png", "place": "background"},
        ])])
    with pytest.raises(ValueError):
        pdf_tool._validate_slides([_slide(1, images=[{"file": "../evil.png", "place": "right"}])])


def test_rejects_too_many_slides():
    with pytest.raises(ValueError):
        pdf_tool._validate_slides([_slide(i) for i in range(1, 52)])


def test_hash_diff_detects_change():
    a = _slide(1)
    b = _slide(1, body="Different body.")
    assert pdf_tool._slide_hash(a) != pdf_tool._slide_hash(b)
    assert pdf_tool._slide_hash(a) == pdf_tool._slide_hash(_slide(1))


def test_sanitize_removes_scripts_and_handlers():
    dirty = '<div onclick="evil()">Hi</div><script>alert(1)</script><a href="javascript:evil()">x</a>'
    clean = pdf_tool._sanitize_html(dirty)
    assert "<script" not in clean.lower()
    assert "onclick" not in clean.lower()
    assert "javascript:" not in clean.lower()
    assert "Hi" in clean


def test_extract_html_prefers_document():
    raw = "intro\n```html\n<html><body><p>Slide</p></body></html>\n```"
    assert "<html>" in pdf_tool._extract_html(raw).lower()


def test_validate_html_rejects_empty_and_script():
    with pytest.raises(ValueError):
        pdf_tool._validate_html("   ", 1)
    with pytest.raises(ValueError):
        pdf_tool._validate_html("<div>ok</div><script>x</script>", 1)
    assert pdf_tool._validate_html("<div><h1>T</h1><p>B</p></div>", 1)


def test_combine_orders_and_breaks_pages():
    combined = pdf_tool._combine_html([
        ({"slide_no": 1}, "<div><h1>One</h1></div>"),
        ({"slide_no": 2}, "<div><h1>Two</h1></div>"),
    ])
    assert combined.index("One") < combined.index("Two")
    assert "pdf-page-break" in combined
    assert "A4 landscape" in combined


def test_events_andDispatcher_registered():
    assert pdf_events.SHOW_ARROW is True
    assert "Creating PDF" in pdf_events.format_event(action="create", pdf_name="rep")
    assert "Updating PDF" in pdf_events.format_event(action="update", task_id="abc123")
    from core.agent.loop.dispatcher import _DISCOVERABLE_MAP

    assert _DISCOVERABLE_MAP.get("pdf_create") == "plugins.core_plugins.pdf_create.tool"


def test_get_unknown_task_fails_clearly():
    with pytest.raises(FileNotFoundError):
        pdf_tool.execute(action="get", task_id="no-such-id")


def test_update_unknown_task_fails_clearly():
    with pytest.raises(FileNotFoundError):
        pdf_tool.execute(action="update", task_id="no-such-id", slides=[_slide(1)])


def test_execute_rejects_bad_action():
    with pytest.raises(ValueError):
        pdf_tool.execute(action="delete")


def test_validate_size_default_and_reject():
    assert pdf_tool._validate_size(None) == "A4-landscape"
    assert pdf_tool._validate_size("square") == "square"
    with pytest.raises(ValueError):
        pdf_tool._validate_size("B2")


def test_combine_uses_page_size():
    items = [({"slide_no": 1}, "<div><h1>One</h1></div>")]
    portrait = pdf_tool._combine_html(items, "A4-portrait")
    assert "A4 portrait" in portrait
    assert "210.0mm" in portrait and "297.0mm" in portrait
    default = pdf_tool._combine_html(items)
    assert "A4 landscape" in default


def test_chrome_binary_check_never_raises():
    assert pdf_tool._chrome_binary() is None or isinstance(pdf_tool._chrome_binary(), str)


def test_scope_css_prefixes_selectors_and_keeps_fontface():
    css = ("h1, .title { color: red; } body { margin: 0; } "
           "@font-face { font-family: X; src: url(x.ttf); }")
    out = pdf_tool._scope_css(css, "#pdfslide-01")
    assert "#pdfslide-01 h1" in out and "#pdfslide-01 .title" in out
    assert "#pdfslide-01 body" not in out and "#pdfslide-01" in out
    assert "@font-face" in out


def test_combine_keeps_each_slide_design():
    items = [({"slide_no": 1}, "<html><head><style>.accent { background: #1A73E8; }</style></head>"
                               "<body><div class='accent'>Hi</div></body></html>")]
    combined = pdf_tool._combine_html(items)
    assert ".accent" in combined and "#pdfslide-01" in combined


def test_chrome_print_and_merge_real(tmp_path):
    binary = pdf_tool._chrome_binary()
    if not binary:
        pytest.skip("no local Chrome")
    try:
        from pypdf import PdfReader
    except ImportError:
        pytest.skip("pypdf not installed")
    parts = []
    for i in (1, 2):
        src = tmp_path / f"s{i}.html"
        src.write_text(f"<html><body><h1>Slide {i}</h1></body></html>", encoding="utf-8")
        out = tmp_path / f"s{i}.pdf"
        pdf_tool._print_slide_pdf(binary, src, out)
        parts.append(out)
    dest = tmp_path / "merged.pdf"
    pdf_tool._merge_pdfs(parts, dest)
    assert len(PdfReader(str(dest)).pages) == 2


def test_check_assets_copies_root_image_into_task_folder():
    import shutil
    import uuid as _uuid

    from core.paths import workspace_root

    from PIL import Image

    ws = workspace_root()
    src = ws / f"ut_src_{_uuid.uuid4().hex[:6]}.png"
    task_rel = f"UT_Pdf_Img_{_uuid.uuid4().hex[:6]}"
    task_dir = ws / task_rel
    try:
        Image.new("RGB", (10, 10), (255, 0, 0)).save(str(src))
        task_dir.mkdir(parents=True, exist_ok=False)
        slides = [{"slide_no": 1, "title": "T", "body": "B",
                   "images": [{"file": src.name, "place": "right"}], "fonts": [], "colors": {}}]
        pdf_tool._check_assets(task_rel, slides)
        assert (task_dir / src.name).is_file()
    finally:
        try:
            src.unlink(missing_ok=True)
        except Exception:
            pass
        shutil.rmtree(str(task_dir), ignore_errors=True)


def test_check_assets_unknown_image_hints_context_ids():
    import shutil
    import uuid as _uuid

    from core.paths import workspace_root

    task_rel = f"UT_Pdf_Img_{_uuid.uuid4().hex[:6]}"
    (workspace_root() / task_rel).mkdir(parents=True, exist_ok=True)
    try:
        slides = [{"slide_no": 2, "title": "T", "body": "B",
                   "images": [{"file": "no_such_img_xyz.png", "place": "left"}], "fonts": [], "colors": {}}]
        with pytest.raises(FileNotFoundError, match="image_id"):
            pdf_tool._check_assets(task_rel, slides)
    finally:
        shutil.rmtree(str(workspace_root() / task_rel), ignore_errors=True)


def test_check_assets_resolves_image_id_via_memory(tmp_path):
    import shutil
    import uuid as _uuid

    from core.paths import workspace_root

    from PIL import Image

    blob = tmp_path / "real.png"
    Image.new("RGB", (8, 8), (0, 0, 255)).save(str(blob))
    task_rel = f"UT_Pdf_Img_{_uuid.uuid4().hex[:6]}"
    (workspace_root() / task_rel).mkdir(parents=True, exist_ok=True)
    orig = pdf_tool._find_image_entry
    pdf_tool._find_image_entry = lambda ref: {"image_id": ref, "path": str(blob)}
    try:
        slides = [{"slide_no": 1, "title": "T", "body": "B",
                   "images": [{"file": "a1b2c3d4", "place": "center"}], "fonts": [], "colors": {}}]
        pdf_tool._check_assets(task_rel, slides)
        assert (workspace_root() / task_rel / "a1b2c3d4").is_file()
    finally:
        pdf_tool._find_image_entry = orig
        shutil.rmtree(str(workspace_root() / task_rel), ignore_errors=True)


def test_bundled_default_font_covers_arabic_and_latin():
    font = PROJECT_ROOT / "plugins" / "core_plugins" / "pdf_create" / "fonts" / "Amiri-Regular.ttf"
    assert font.is_file()
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        pytest.skip("fontTools not installed")
    cmap = TTFont(str(font)).getBestCmap()
    assert 0x627 in cmap  # Arabic ALEF
    assert 0x41 in cmap  # Latin A


def test_hex_and_pdf_build_editable(tmp_path):
    assert pdf_tool._hex_to_rgb("#1A73E8") == (26, 115, 232)
    assert pdf_tool._hex_to_rgb("zzzzzz") == (0, 0, 0)
    out = tmp_path / "deck.pdf"
    items = [
        ({"slide_no": 1, "title": "One", "body": "First body.", "images": [], "fonts": [],
          "colors": {"primary": "#1A73E8", "secondary": "#FFFFFF"}}, "<div/>"),
        ({"slide_no": 2, "title": "Two", "body": "Second body.", "images": [], "fonts": [],
          "colors": {"primary": "#1A73E8", "secondary": "#FFFFFF"}}, "<div/>"),
    ]
    pdf_tool._build_pdf_fpdf(items, "no_such_folder_xyz", out)
    assert out.stat().st_size > 0
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(out))
        assert len(reader.pages) == 2
        assert "One" in (reader.pages[0].extract_text() or "")
    except ImportError:
        pass
