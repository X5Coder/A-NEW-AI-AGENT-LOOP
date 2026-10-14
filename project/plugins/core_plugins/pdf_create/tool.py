"""
pdf_create - professional PDF slide-deck builder (DISCOVERABLE behind tool_search).

One tool, three actions: create (new deck) / update (edit by task_id, only
changed slides regenerate) / get (stored slides JSON + latest PDF).

How it works (approved plan): the tool owns an internal single-shot session.
For every slide it sends the separate pdf_slide_system.md system prompt plus
the folder path, the full slides JSON, and ONLY the current slide as the
executive task. The model returns raw HTML text (it never sees tools and never
writes files). The tool writes each slide file itself, then merges everything
into combined.html and converts it once into an editable text PDF with
xhtml2pdf (no browser involved). Every deck lives in one Workspace folder
named after the PDF, so get returns the JSON together with the latest PDF.

Bounds: max 50 slides, slides JSON max 100KB, HTML max 500KB per slide,
max 10 images per slide, max 3 LLM attempts per slide.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from .events import format_event

MAX_SLIDES = 50
MAX_SLIDES_JSON_CHARS = 100_000
MAX_HTML_BYTES = 500_000
MAX_IMAGES_PER_SLIDE = 10
MAX_RETRIES_PER_SLIDE = 3

ALLOWED_ACTIONS = {"create", "update", "get"}
ALLOWED_PLACES = {"right", "left", "center", "background"}

# Page sizes: millimetre width/height + CSS @page rule + fpdf mapping.
# Default is the rectangle (A4 landscape) unless the user names a size.
PAGE_SIZES = {
    "A4-landscape": {"w": 297.0, "h": 210.0, "css": "A4 landscape", "orientation": "L", "format": "A4"},
    "A4-portrait": {"w": 210.0, "h": 297.0, "css": "A4 portrait", "orientation": "P", "format": "A4"},
    "Letter-landscape": {"w": 279.0, "h": 216.0, "css": "279mm 216mm", "orientation": "L", "format": "Letter"},
    "Letter-portrait": {"w": 216.0, "h": 279.0, "css": "279mm 216mm", "orientation": "P", "format": "Letter"},
    "square": {"w": 210.0, "h": 210.0, "css": "210mm 210mm", "orientation": "L", "format": (210.0, 210.0)},
}
DEFAULT_SIZE = "A4-landscape"
TASK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,16}$")
PDF_NAME_RE = re.compile(r"^[\w][\w \-]{0,59}$", re.UNICODE)
HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
RETRY_SENTENCE = ("The file was not created with the same slide name. "
                  "Retry creating it now (full HTML only, no explanation).")
# Reminder appended to every successful build: the agent reviews and refines
# before delivering, instead of handing the first render straight to the user.
REVIEW_LINE = ("Review what was produced in the task folder and refine any "
               "slide with action=update before presenting the PDF to the user.")


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _bootstrap() -> Path:
    import sys as _sys

    proj = _project_root()
    if str(proj) not in _sys.path:
        _sys.path.insert(0, str(proj))
    return proj


def _tasks_path() -> Path:
    try:
        _bootstrap()
        from core.paths import data_db_file

        return data_db_file("pdf_tasks.json")
    except Exception:
        return _project_root() / "storage" / "database" / "pdf_tasks.json"


def _legacy_tasks_path() -> Path:
    return _project_root() / "storage" / "database" / "pdf_tasks.json"


def _load_tasks() -> list[dict]:
    for cand in (_tasks_path(), _legacy_tasks_path()):
        try:
            if cand.exists():
                data = json.loads(cand.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
        except Exception:
            continue
    return []


def _save_tasks(tasks: list[dict]) -> None:
    path = _tasks_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _workspace_root() -> Path:
    _bootstrap()
    try:
        from core.paths import workspace_root as _ws

        ws = _ws()
        ws.mkdir(parents=True, exist_ok=True)
        return ws.resolve()
    except Exception:
        ws = _project_root().parents[0] / "X5Agent" / "Workspace"
        ws.mkdir(parents=True, exist_ok=True)
        return ws.resolve()


def _write_workspace_file(rel: str, content: str) -> Path:
    _bootstrap()
    from core.paths import resolve_inside_workspace

    target = resolve_inside_workspace(rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def _read_workspace_file(rel: str) -> str | None:
    try:
        _bootstrap()
        from core.paths import resolve_inside_workspace

        target = resolve_inside_workspace(rel)
        if target.is_file():
            return target.read_text(encoding="utf-8")
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------
# Validation (external input is untrusted: model-generated slides JSON)
# --------------------------------------------------------------------------

def _validate_action(v: Any) -> str:
    if not isinstance(v, str):
        raise TypeError("action must be string")
    act = v.strip().lower()
    if act not in ALLOWED_ACTIONS:
        raise ValueError("action must be one of create, update, get")
    return act


def _validate_task_id(v: Any, required: bool) -> str | None:
    if not required and v is None:
        return None
    if not isinstance(v, str):
        raise TypeError("task_id must be string")
    s = v.strip()
    if not s and required:
        raise ValueError("task_id is required for this action")
    if s and not TASK_ID_RE.match(s):
        raise ValueError("task_id is invalid")
    return s if s else None


def _validate_pdf_name(v: Any) -> str:
    if not isinstance(v, str):
        raise TypeError("pdf_name must be string")
    s = " ".join(v.strip().split())
    if not s:
        raise ValueError("pdf_name is required for create")
    if len(s) > 60 or not PDF_NAME_RE.match(s):
        raise ValueError("pdf_name must be 1..60 chars: letters, numbers, spaces, _ -")
    if ".." in s or "/" in s or "\\" in s:
        raise ValueError("pdf_name must be a single folder name")
    return s


def _validate_file_ref(v: Any, field: str, slide_no: int) -> str:
    if not isinstance(v, str):
        raise TypeError(f"slide {slide_no}: {field} must be string")
    s = v.strip().replace("\\", "/")
    if not s or len(s) > 100:
        raise ValueError(f"slide {slide_no}: {field} must be 1..100 chars")
    # Workspace-relative ids: bare names ("logo.png") or sub-paths
    # ("deck/logo.png"). Never absolute, never parent/dot escapes.
    parts = s.split("/")
    if s.startswith("/") or "\x00" in s or any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"slide {slide_no}: {field} must be a Workspace file id (e.g. logo.png)")
    return s.strip("/")


def _validate_hex(v: Any, field: str, slide_no: int) -> str:
    if not isinstance(v, str) or not HEX_RE.match(v.strip()):
        raise ValueError(f"slide {slide_no}: {field} must be a hex code like #1A73E8")
    return v.strip()


def _validate_size(v: Any) -> str:
    if v is None:
        return DEFAULT_SIZE
    if not isinstance(v, str):
        raise TypeError("size must be string")
    s = v.strip()
    if s not in PAGE_SIZES:
        raise ValueError(f"size must be one of {sorted(PAGE_SIZES)} (default {DEFAULT_SIZE})")
    return s


def _validate_slides(v: Any) -> list[dict]:
    if not isinstance(v, list):
        raise TypeError("slides must be an array of slide objects")
    if not v:
        raise ValueError("slides must contain at least 1 slide")
    if len(v) > MAX_SLIDES:
        raise ValueError(f"too many slides (max {MAX_SLIDES})")
    try:
        if len(json.dumps(v, ensure_ascii=False)) > MAX_SLIDES_JSON_CHARS:
            raise ValueError("slides JSON too large (max 100KB)")
    except ValueError:
        raise
    except Exception:
        raise ValueError("slides must be JSON-serializable")
    cleaned: list[dict] = []
    seen: set[int] = set()
    for raw in v:
        if not isinstance(raw, dict):
            raise ValueError("each slide must be an object")
        no = raw.get("slide_no")
        if isinstance(no, bool) or not isinstance(no, int) or not 1 <= no <= MAX_SLIDES:
            raise ValueError("each slide needs slide_no 1..50")
        if no in seen:
            raise ValueError(f"slide {no}: duplicate slide_no")
        seen.add(no)
        title = raw.get("title")
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 200:
            raise ValueError(f"slide {no}: title must be 1..200 chars")
        body = raw.get("body")
        if not isinstance(body, str) or not body.strip() or len(body.strip()) > 5000:
            raise ValueError(f"slide {no}: body must be 1..5000 chars")
        images = raw.get("images", [])
        if images is None:
            images = []
        if not isinstance(images, list) or len(images) > MAX_IMAGES_PER_SLIDE:
            raise ValueError(f"slide {no}: images must be a list of max {MAX_IMAGES_PER_SLIDE}")
        clean_images = []
        backgrounds = 0
        for img in images:
            if not isinstance(img, dict):
                raise ValueError(f"slide {no}: each image must be an object")
            place = img.get("place")
            if not isinstance(place, str) or place.strip().lower() not in ALLOWED_PLACES:
                raise ValueError(f"slide {no}: image place must be right, left, center or background")
            p = place.strip().lower()
            backgrounds += 1 if p == "background" else 0
            clean_images.append({"file": _validate_file_ref(img.get("file"), "image file", no), "place": p})
        if backgrounds > 1:
            raise ValueError(f"slide {no}: only one background image is allowed")
        fonts = raw.get("fonts", [])
        if fonts is None:
            fonts = []
        if not isinstance(fonts, list) or len(fonts) > 5:
            raise ValueError(f"slide {no}: fonts must be a list of max 5")
        clean_fonts = []
        for font in fonts:
            if not isinstance(font, dict):
                raise ValueError(f"slide {no}: each font must be an object")
            usage = str(font.get("usage") or "body").strip().lower()[:20] or "body"
            clean_fonts.append({"file": _validate_file_ref(font.get("file"), "font file", no), "usage": usage})
        colors = raw.get("colors", {})
        if colors is None:
            colors = {}
        if not isinstance(colors, dict):
            raise ValueError(f"slide {no}: colors must be an object")
        clean_colors: dict = {}
        for key in ("primary", "secondary"):
            if colors.get(key) is not None:
                clean_colors[key] = _validate_hex(colors.get(key), f"colors.{key}", no)
        gradient = colors.get("gradient")
        if gradient is not None:
            if not isinstance(gradient, dict):
                raise ValueError(f"slide {no}: colors.gradient must be an object")
            clean_colors["gradient"] = {
                "from": _validate_hex(gradient.get("from"), "colors.gradient.from", no),
                "to": _validate_hex(gradient.get("to"), "colors.gradient.to", no),
                "direction": str(gradient.get("direction") or "to-right").strip().lower()[:20] or "to-right",
            }
        cleaned.append({
            "slide_no": no,
            "title": " ".join(title.strip().split()),
            "body": body.strip(),
            "images": clean_images,
            "fonts": clean_fonts,
            "colors": clean_colors,
        })
    cleaned.sort(key=lambda s: s["slide_no"])
    numbers = [s["slide_no"] for s in cleaned]
    if numbers != list(range(1, len(numbers) + 1)):
        raise ValueError("slide_no must be sequential starting at 1 with no gaps")
    return cleaned


def _slide_hash(slide: dict) -> str:
    raw = json.dumps(slide, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


MAX_IMAGE_BYTES = 25 * 1024 * 1024


def _find_image_entry(ref: str) -> dict | None:
    """Find an image_memory entry by image_id (or exact name, newest first)."""
    try:
        _bootstrap()
        from core.memory.image_memory.service import get_image, list_images

        hit = get_image(ref)
        if isinstance(hit, dict) and hit.get("path"):
            return hit
        for entry in reversed(list_images(include_temp=True)):
            if isinstance(entry, dict) and entry.get("name") == ref and entry.get("path"):
                return entry
    except Exception:
        pass
    return None


def _check_assets(task_rel: str, slides: list[dict]) -> None:
    """Ensure every slide image exists INSIDE the task folder.

    Resolution order per reference (which may be a Workspace file name, a
    file_id from [Attached files], or an image_id from context):
    task folder -> Workspace root (copied in) -> image_memory entry
    (copied in). Copying keeps the deck self-contained so standalone slide
    files resolve their <img src> relatively wherever they print.
    Raises FileNotFoundError hinting at exact context ids otherwise.
    """
    ws = _workspace_root()
    _bootstrap()
    from core.paths import resolve_inside_workspace as _jail
    try:
        _jail(task_rel)
    except ValueError:
        raise ValueError(f"Blocked: PDF folder must stay inside Workspace") from None
    for slide in slides:
        no = slide["slide_no"]
        for img in slide["images"]:
            name = img["file"]
            try:
                task_target = _jail(f"{task_rel}/{name}")
            except ValueError:
                task_target = None
            try:
                ws_target = _jail(name)
            except ValueError:
                ws_target = None
            if ((task_target is not None and task_target.is_file())
                    or (ws_target is not None and ws_target.is_file())):
                if task_target is None:
                    raise FileNotFoundError(f"slide {no}: file not found in Workspace: {name}")
                if not task_target.is_file():
                    try:
                        assert ws_target is not None and ws_target.is_file()
                        data = ws_target.read_bytes()
                        if len(data) > MAX_IMAGE_BYTES:
                            raise ValueError(f"slide {no}: image too large (max 25MB): {name}")
                        task_target.parent.mkdir(parents=True, exist_ok=True)
                        task_target.write_bytes(data)
                    except ValueError:
                        raise
                    except Exception as e:
                        raise RuntimeError(f"slide {no}: cannot copy image: {name} ({e})") from e
                continue
            entry = _find_image_entry(name)
            if entry is not None:
                try:
                    src = Path(str(entry["path"]))
                    if not src.is_file():
                        raise FileNotFoundError(f"slide {no}: image_id file is gone: {name}")
                    if task_target is None:
                        raise FileNotFoundError(f"slide {no}: file not found in Workspace: {name}")
                    data = src.read_bytes()
                    if len(data) > MAX_IMAGE_BYTES:
                        raise ValueError(f"slide {no}: image too large (max 25MB): {name}")
                    task_target.parent.mkdir(parents=True, exist_ok=True)
                    task_target.write_bytes(data)
                    continue
                except (FileNotFoundError, ValueError):
                    raise
                except Exception as e:
                    raise RuntimeError(f"slide {no}: cannot copy image: {name} ({e})") from e
            raise FileNotFoundError(
                f"slide {no}: file not found in Workspace: {name} — use the exact "
                "file_id from [Attached files] or the image_id from context.")
        for font in slide["fonts"]:
            name = font["file"]
            # Fonts are copied into the deck exactly like images: @font-face
            # uses the bare file name, so a root-only font would 404 when
            # Chrome prints the standalone slide file.
            try:
                task_target = _jail(f"{task_rel}/{name}")
            except ValueError:
                task_target = None
            try:
                ws_target = _jail(name)
            except ValueError:
                ws_target = None
            task_ok = task_target is not None and task_target.is_file()
            ws_ok = ws_target is not None and ws_target.is_file()
            if not (task_ok or ws_ok):
                raise FileNotFoundError(f"slide {no}: file not found in Workspace: {name}")
            if not task_ok:
                try:
                    assert task_target is not None and ws_target is not None and ws_target.is_file()
                    data = ws_target.read_bytes()
                    if len(data) > MAX_IMAGE_BYTES:
                        raise ValueError(f"slide {no}: font too large (max 25MB): {name}")
                    task_target.parent.mkdir(parents=True, exist_ok=True)
                    task_target.write_bytes(data)
                except ValueError:
                    raise
                except Exception as e:
                    raise RuntimeError(f"slide {no}: cannot copy font: {name} ({e})") from e


# --------------------------------------------------------------------------
# Internal single-shot session (same AIP/1 path the loop uses)
# --------------------------------------------------------------------------

def _system_prompt() -> str:
    proj = _bootstrap()
    path = proj / "prompts" / "pdf_slide_system.md"
    try:
        if path.exists():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return text
    except Exception:
        pass
    return ("You generate one fixed-size slide HTML document. "
            "Reply with raw HTML only, no explanation, no animation, no scripts.")


def _llm_html(system: str, user: str) -> str:
    """One bounded model call returning raw HTML text. Raises RuntimeError."""
    _bootstrap()
    from core.ai_models.store import require_text_model
    from core.ai_models.engine import is_aip_record

    record = require_text_model()
    if is_aip_record(record):
        from core.ai_models import aip as _aip

        canonical = {
            "version": 1,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        try:
            res = _aip.execute(record, canonical, "text")
            content = (res.get("text") if isinstance(res, dict) else "") or ""
        except Exception as e:
            raise RuntimeError(
                f"Slide generation failed ({e}). The AI model service is unreachable "
                "right now; try again in a few minutes."
            ) from e
    else:
        from core.ai_models.engine import chat as _legacy_chat

        try:
            content = _legacy_chat(
                record,
                [{"role": "user", "content": user}],
                {"system": system},
            )
        except Exception as e:
            raise RuntimeError(
                f"Slide generation failed ({e}). The AI model service is unreachable "
                "right now; try again in a few minutes."
            ) from e
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("Slide generation returned empty content from the text model.")
    return content.strip()


def _extract_html(raw: str) -> str:
    text = raw.strip()
    fence = re.search(r"```(?:html)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fence and fence.group(1).strip():
        text = fence.group(1).strip()
    match = re.search(r"<html[\s\S]*</html\s*>", text, re.IGNORECASE)
    if match:
        return match.group(0).strip()
    return text


def _sanitize_html(html: str) -> str:
    """Remove executable/animated constructs. Deterministic, never raises."""
    try:
        clean = re.sub(r"<script[\s\S]*?</script\s*>", "", html, flags=re.IGNORECASE)
        clean = re.sub(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"javascript\s*:", "#", clean, flags=re.IGNORECASE)
        clean = re.sub(r"@keyframes\s+[\w-]+\s*\{(?:[^{}]|\{[^{}]*\})*\}", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"animation\s*:[^;}]*[;}]", ";", clean, flags=re.IGNORECASE)
        return clean
    except Exception:
        return html


def _validate_html(html: str, slide_no: int) -> str:
    if not html.strip():
        raise ValueError(f"slide {slide_no}: empty HTML")
    if len(html.encode("utf-8")) > MAX_HTML_BYTES:
        raise ValueError(f"slide {slide_no}: HTML too large (max 500KB)")
    low = html.lower()
    if "<script" in low or "javascript:" in low:
        raise ValueError(f"slide {slide_no}: HTML contains forbidden scripts")
    if not ("<" in html and any(tag in low for tag in ("<html", "<div", "<section", "<body", "<h1", "<h2", "<p"))):
        raise ValueError(f"slide {slide_no}: HTML has no slide content")
    return html


def _slug(title: str) -> str:
    slug = re.sub(r"[^\w\-]+", "_", title.strip(), flags=re.UNICODE).strip("_")
    slug = re.sub(r"_+", "_", slug)[:30].strip("_")
    return slug or "slide"


def _slide_filename(slide: dict) -> str:
    return f"slide_{slide['slide_no']:02d}_{_slug(slide['title'])}.html"


def _internal_context() -> str:
    """What the internal slide session can see and decide on (no tool calls).

    Same catalog the main agent browses (core + discoverable, name + purpose)
    plus the live Workspace file map with exact ids. The model cannot call
    tools from here — it decides by name and references files by their exact
    id inside the slide JSON/HTML. Bounded (~12KB), never raises.
    """
    parts: list[str] = []
    try:
        _bootstrap()
        from core.tools.registry import list_core_specs, list_discoverable_specs

        lines: list[str] = []
        seen: set[str] = set()
        for spec in (list_core_specs() or []) + (list_discoverable_specs() or []):
            if not isinstance(spec, dict):
                continue
            nm = spec.get("name")
            if not isinstance(nm, str) or not nm or nm in seen:
                continue
            seen.add(nm)
            lines.append(f"- {nm}: {str(spec.get('description') or '')[:100]}")
            if len(lines) >= 80:
                break
        if lines:
            parts.append("[Visible tools — the same catalog the main agent uses. "
                         "Decide by name which files/ids you need; you cannot call "
                         "tools here, only reference exact file ids.]\n" + "\n".join(lines))
    except Exception:
        pass
    try:
        _bootstrap()
        from core.agent.context.builder import _workspace_block

        block = _workspace_block()
        if block:
            parts.append(block + "\n(Reference every file by its id exactly as shown.)")
    except Exception:
        pass
    try:
        return "\n\n".join(parts)[:12000]
    except Exception:
        return ""


def _slide_user_prompt(pdf_name: str, folder_rel: str, slides: list[dict], slide: dict,
                       old_html: str | None = None, size: str = DEFAULT_SIZE,
                       context: str = "") -> str:
    all_json = json.dumps(slides, ensure_ascii=False, indent=1)
    current = json.dumps(slide, ensure_ascii=False, indent=1)
    page = PAGE_SIZES.get(size) or PAGE_SIZES[DEFAULT_SIZE]
    parts = [
        f"PDF name: {pdf_name}",
        f"Workspace folder (relative): {folder_rel}",
        f"Page size: {size} ({page['w']}mm x {page['h']}mm) — design exactly for this page.",
        f"All slides JSON:\n{all_json}",
    ]
    if isinstance(context, str) and context.strip():
        parts.append(context.strip()[:12000])
    if old_html:
        parts.append(f"OLD HTML of slide {slide['slide_no']} (rewrite it fully):\n{old_html[:20000]}")
        parts.append(f"NEW slide data (apply every change, keep the design language):\n{current}")
        parts.append(f"Regenerate the COMPLETE file for slide {slide['slide_no']} now.")
    else:
        parts.append(f"CURRENT slide to create (this file only, named like slide_{slide['slide_no']:02d}_*):\n{current}")
        parts.append(f"Create the file content for slide {slide['slide_no']} now.")
    return "\n\n".join(parts)


class _UserCancelled(RuntimeError):
    """Raised when STOP is pressed mid-deck: aborts before the next slide."""


def _is_cancelled_flag(flag: Any) -> bool:
    """Whether the loop's cancel event is set. Duck-typed, never raises."""
    try:
        return bool(flag is not None and flag.is_set())
    except Exception:
        return False


def _check_cancelled(flag: Any) -> None:
    if _is_cancelled_flag(flag):
        raise _UserCancelled("cancelled by user (STOP) — stopping before the next slide.")


def _ensure_deck_file(task_rel: str, base: str) -> str | None:
    """Copy a real Workspace file into the deck (self-containment net).

    The slide HTML sometimes references a genuine Workspace file the model
    forgot to declare (typically a font). When the bare name exists at the
    Workspace root it is copied next to the HTML and accepted; invented
    names still fail. Bare names only, jail-resolved, size-capped.
    Returns the bare name, else None. Never raises.
    """
    try:
        if not base or "/" in base or "\\" in base or base in (".", ".."):
            return None
        _bootstrap()
        from core.paths import resolve_inside_workspace as _jail

        target = _jail(f"{task_rel}/{base}")
        if target.is_file():
            return base
        src = _jail(base)
        if not src.is_file():
            return None
        data = src.read_bytes()
        if len(data) > MAX_IMAGE_BYTES:
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return base
    except Exception:
        return None


def _normalize_image_srcs(html: str, slide: dict, task_rel: str) -> str:
    """Rewrite every <img src> and CSS url() to the verified deck-relative id.

    The slide model sometimes writes an absolute path, an API URL or an
    invented name instead of the exact file id — Chrome then prints a
    silently missing image. Each reference is matched (basename,
    URL-decoded) against the slide's declared images and replaced with the
    exact declared id, whose file the tool already placed next to the HTML.
    A reference matching neither the declaration nor a real deck file fails
    clearly instead of rendering a broken image. Never touches data-URLs
    or fragment-only refs (e.g. url(#gradient)).
    """
    declared: dict[str, str] = {}
    for img in slide.get("images") or []:
        if isinstance(img, dict) and img.get("file"):
            ref = str(img["file"])
            declared[ref.split("/")[-1].lower()] = ref
    if not declared:
        return html
    no = slide.get("slide_no")

    def _candidates(raw: str) -> list[str]:
        """Basename guesses for one src: path tail plus ?path=/file=/id= values."""
        s = raw.strip().strip("'\"").replace("\\", "/")
        head, _, query = s.partition("?")
        head = head.split("#")[0]
        out: list[str] = []
        base = unquote(head.rsplit("/", 1)[-1]).strip()
        if base:
            out.append(base)
        if query:
            for part in query.split("&"):
                k, _, v = part.partition("=")
                if k.strip().lower() in ("path", "file", "src", "id", "name", "image") and v.strip():
                    b = unquote(v.strip().split("#")[0].replace("\\", "/").rsplit("/", 1)[-1]).strip()
                    if b and b not in out:
                        out.append(b)
        return out

    def _resolve_ref(raw: str) -> str:
        s = raw.strip().strip("'\"")
        if not s or s.lower().startswith("data:"):
            return raw
        cands = _candidates(raw)
        if not cands:
            return raw
        for cand in cands:
            hit = declared.get(cand.lower())
            if hit:
                return hit
        for cand in cands:
            if _ensure_deck_file(task_rel, cand):
                return cand
        raise ValueError(f"slide {no}: image not found: {raw[:80]}")

    def _img_re(m: Any) -> str:
        return m.group(1) + _resolve_ref(m.group(2)) + m.group(3)

    out = re.sub(r'(<img\b[^>]*?\bsrc\s*=\s*")([^"]*)(")', _img_re, html, flags=re.IGNORECASE)
    out = re.sub(r"(<img\b[^>]*?\bsrc\s*=\s*')([^']*)(')", _img_re, out, flags=re.IGNORECASE)

    def _url_re(m: Any) -> str:
        return m.group(1) + _resolve_ref(m.group(2)) + m.group(3)

    out = re.sub(r'(url\(\s*")([^"]*)("\s*\))', _url_re, out, flags=re.IGNORECASE)
    out = re.sub(r"(url\(\s*')([^']*)('\s*\))", _url_re, out, flags=re.IGNORECASE)
    out = re.sub(r'(url\(\s*)([^"\'()\s][^()]*?)(\s*\))', _url_re, out, flags=re.IGNORECASE)
    return out


def _run_slide(system: str, user: str, slide_no: int, cancel_flag: Any = None) -> str:
    """Single slide with bounded retries. Returns sanitized valid HTML."""
    last_error = "unknown error"
    prompt = user
    for _ in range(MAX_RETRIES_PER_SLIDE):
        _check_cancelled(cancel_flag)
        try:
            raw = _llm_html(system, prompt)
            return _validate_html(_sanitize_html(_extract_html(raw)), slide_no)
        except (RuntimeError, ValueError) as e:
            last_error = str(e)[:200]
            prompt = user + "\n\n" + RETRY_SENTENCE
            continue
    raise RuntimeError(f"slide {slide_no} failed after {MAX_RETRIES_PER_SLIDE} attempts: {last_error}")


def _progress_line(slide: dict, first: bool = False) -> str:
    title = slide["title"][:80]
    no = slide["slide_no"]
    if first and no == 1:
        return f"Slide 1 created: {title}"
    return f"Slide {no} created: {title}"


def _merge_line(count: int) -> str:
    return f"Merged {count} slides into PDF"


def _extract_styles(html: str) -> list[str]:
    """All <style> block contents of one slide document. Never raises."""
    try:
        return [m.group(1) for m in re.finditer(r"<style[^>]*>([\s\S]*?)</style\s*>", html, re.IGNORECASE)]
    except Exception:
        return []


def _scope_css(css: str, scope: str) -> str:
    """Prefix every rule selector with `scope` so merged slides never clash.

    @font-face/@page stay global by nature; @media inner rules are scoped
    recursively; page-level selectors (html/body/:root) map onto the scope.
    Unknown at-rules are kept verbatim. Deterministic, never raises.
    """
    try:
        css = re.sub(r"/\*[\s\S]*?\*/", "", css)
        out: list[str] = []
        i, n = 0, len(css)

        def _block_end(start: int) -> int:
            depth = 0
            k = start
            while k < n:
                if css[k] == "{":
                    depth += 1
                elif css[k] == "}":
                    depth -= 1
                    if depth == 0:
                        return k
                k += 1
            return n - 1

        while i < n:
            while i < n and css[i].isspace():
                i += 1
            if i >= n:
                break
            if css[i] == "@":
                j = css.find("{", i)
                semi = css.find(";", i)
                if j == -1 or (semi != -1 and semi < j):
                    out.append(css[i:semi + 1 if semi != -1 else n])
                    i = semi + 1 if semi != -1 else n
                    continue
                k = _block_end(j)
                block = css[i:k + 1]
                i = k + 1
                head = block[:block.find("{")].strip().lower()
                if head.startswith("@font-face") or head.startswith("@page"):
                    out.append(block)
                elif head.startswith("@media"):
                    inner = block[block.find("{") + 1:block.rfind("}")]
                    out.append(block[:block.find("{") + 1] + _scope_css(inner, scope) + "}")
                else:
                    out.append(block)
            else:
                j = css.find("{", i)
                if j == -1:
                    break
                selectors = css[i:j]
                k = _block_end(j)
                body = css[j:k + 1]
                i = k + 1
                parts = []
                for sel in selectors.split(","):
                    s = sel.strip()
                    if not s:
                        continue
                    rest = re.sub(r"^(html|body|:root)\b", "", s, flags=re.IGNORECASE).strip()
                    if rest != s:
                        parts.append(scope if not rest else scope + " " + rest)
                    else:
                        parts.append(scope + " " + s)
                if parts:
                    out.append(", ".join(parts) + body)
        return "\n".join(out)
    except Exception:
        return ""


def _combine_html(items: list[tuple[dict, str]], size: str = DEFAULT_SIZE) -> str:
    page = PAGE_SIZES.get(size) or PAGE_SIZES[DEFAULT_SIZE]
    w, h, css = page["w"], page["h"], page["css"]
    bodies = []
    styles = []
    for slide, html in items:
        no = slide["slide_no"]
        scope = f"#pdfslide-{no:02d}"
        for block in _extract_styles(html):
            scoped = _scope_css(block, scope)
            if scoped.strip():
                styles.append(scoped)
        inner = html
        body_match = re.search(r"<body[^>]*>([\s\S]*)</body\s*>", html, re.IGNORECASE)
        if body_match:
            inner = body_match.group(1)
        else:
            html_match = re.search(r"<html[^>]*>([\s\S]*)</html\s*>", html, re.IGNORECASE)
            if html_match:
                inner = html_match.group(1)
        bodies.append(
            f'<section class="pdf-slide" data-slide="{no}">\n<div id="pdfslide-{no:02d}">\n{inner}\n</div>\n</section>'
        )
    # Sections already carry page-break-after; no extra break elements
    # (an empty breaker would print a blank page in Chrome).
    joined = "\n".join(bodies)
    styles_blob = "\n".join(styles)
    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<title>Combined slides</title>
<style>
@page {{ size: {css}; margin: 0; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; padding: 0; }}
.pdf-slide {{ width: {w}mm; height: {h}mm; overflow: hidden; page-break-after: always; position: relative; }}
.pdf-slide:last-child {{ page-break-after: auto; }}
.pdf-page-break {{ page-break-after: always; height: 0; }}
img {{ max-width: 100%; }}
{styles_blob}
</style>
</head>
<body>
{joined}
</body>
</html>"""


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    """Hex color to 0..255 triple. Never raises (falls back to black)."""
    try:
        s = value.strip().lstrip("#")
        if len(s) == 3:
            s = "".join(c * 2 for c in s)
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except Exception:
        return (0, 0, 0)


def _resolve_asset(task_rel: str, name: str) -> Path | None:
    """Find an image/font file: task folder first, then Workspace root."""
    try:
        _bootstrap()
        from core.paths import resolve_inside_workspace

        for cand in (f"{task_rel}/{name}", name):
            target = resolve_inside_workspace(cand)
            if target.is_file():
                return target
    except Exception:
        pass
    return None


def _shape_ar(text: str) -> str:
    """Reshape Arabic text for correct visual rendering in the PDF.

    fpdf2 writes glyphs as given; without reshaping Arabic letters appear
    disconnected. Uses arabic-reshaper + python-bidi when available (both
    pure Python, Windows + Linux). Falls back to raw text. Never raises.
    """
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display

        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text


def _printable_copy(html: str) -> str:
    """Temp-print variant of one slide/combined document (originals untouched).

    Headless Chrome --print-to-pdf drops every raster image when the ROOT
    <html> carries dir=rtl (or body gets CSS direction:rtl) — verified
    experimentally — while inner-container direction prints fine and looks
    identical. So the print copy drops the root dir attribute; when the root
    was rtl and no CSS direction exists anywhere, top-level children keep
    their alignment via body>*{direction:rtl} (inner containers print
    correctly). Pure string transform, deterministic. Never raises.
    """
    try:
        text = html if isinstance(html, str) else ""
        if not text:
            return html
        m = re.search(r"<html\b[^>]*>", text, re.IGNORECASE)
        if not m:
            return html
        tag = m.group(0)
        dm = re.search(r"""\sdir\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""", tag, re.IGNORECASE)
        root_rtl = bool(dm) and dm.group(1).strip("\"'").strip().lower() == "rtl"
        if dm:
            text = text[:m.start()] + tag[:dm.start()] + tag[dm.end():] + text[m.end():]
        elif not root_rtl:
            return html
        if root_rtl:
            styles = "".join(
                bl for bl in re.findall(r"<style[^>]*>([\s\S]*?)</style\s*>", text, re.IGNORECASE))
            if "direction" not in styles.lower():
                inject = "<style>body>*{direction:rtl;unicode-bidi:isolate;}</style>"
                hm = re.search(r"<head[^>]*>", text, re.IGNORECASE)
                if hm:
                    text = text[:hm.end()] + inject + text[hm.end():]
                else:
                    text = inject + text
        return text
    except Exception:
        return html


def _chrome_binary() -> str | None:
    """Local Chrome/Chromium binary for headless HTML printing. Never raises.

    Uses the installed browser binary directly (no browsing session, no
    library): it only converts the tool's own combined.html file to PDF.
    """
    try:
        import shutil as _shutil

        for cand in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
            found = _shutil.which(cand)
            if found:
                return found
        import os as _os

        if _os.name == "nt":
            for cand in (
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            ):
                if Path(cand).is_file():
                    return cand
        else:
            for cand in ("/usr/bin/google-chrome", "/usr/bin/chromium",
                         "/usr/bin/chromium-browser", "/snap/bin/chromium"):
                if Path(cand).is_file():
                    return cand
        env = _os.environ.get("CHROME_BIN", "").strip()
        if env and Path(env).is_file():
            return env
    except Exception:
        pass
    return None


def _build_pdf_chrome(combined_path: Path, pdf_path: Path) -> None:
    """Print combined.html to PDF with headless Chrome (exact site render).

    Chrome prints the real rendered page: full CSS, positioned images,
    gradients and decorations all land in the PDF at the chosen @page size,
    while text stays real selectable PDF text. Raises RuntimeError when
    Chrome is missing or the print fails (caller falls back to fpdf2).
    """
    binary = _chrome_binary()
    if not binary:
        raise RuntimeError("no local Chrome/Chromium binary found")
    import subprocess as _sp

    # Same root-rtl print bug as per-slide printing: print a printable
    # sibling copy (relative image/font refs keep resolving), never the
    # stored file itself. Removed afterwards.
    print_src: Path = combined_path
    print_tmp: Path | None = None
    try:
        fixed = _printable_copy(combined_path.read_text(encoding="utf-8"))
        if fixed != combined_path.read_text(encoding="utf-8"):
            print_tmp = combined_path.with_name(combined_path.stem + ".print.html")
            print_tmp.write_text(fixed, encoding="utf-8")
            print_src = print_tmp
    except Exception:
        print_src = combined_path
    url = print_src.resolve().as_uri()
    cmd = [binary, "--headless", "--disable-gpu", "--no-sandbox",
           "--hide-scrollbars", "--print-to-pdf-no-header",
           f"--print-to-pdf={str(pdf_path.resolve())}", url]
    try:
        try:
            proc = _sp.run(cmd, capture_output=True, text=True, timeout=120)
        except FileNotFoundError as e:
            raise RuntimeError("Chrome binary not executable") from e
        except _sp.TimeoutExpired as e:
            raise RuntimeError("Chrome print timed out") from e
        if proc.returncode != 0:
            raise RuntimeError(f"Chrome print failed: {(proc.stderr or '')[:200]}")
        try:
            if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
                raise RuntimeError("Chrome print produced an empty file.")
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Chrome print failed: {e}") from e
    finally:
        try:
            if print_tmp is not None and print_tmp.is_file():
                print_tmp.unlink()
        except Exception:
            pass


def _build_pdf_fpdf(items: list[tuple[dict, str]], task_rel: str, pdf_path: Path,
                    size: str = DEFAULT_SIZE) -> None:
    """Fallback renderer (pure Python): editable text + images per slide.

    Used only when no local Chrome exists. Keeps every text and image at the
    chosen page size, but cannot reproduce advanced CSS (it is a clean
    professional page, not the site render).
    """
    page = PAGE_SIZES.get(size) or PAGE_SIZES[DEFAULT_SIZE]
    page_w, page_h = float(page["w"]), float(page["h"])
    try:
        from fpdf import FPDF
    except Exception as e:
        raise RuntimeError(
            "PDF library missing: install it once with 'pip install fpdf2' "
            "then call pdf_create again."
        ) from e
    try:
        pdf = FPDF(orientation=str(page["orientation"]), unit="mm", format=page["format"])
        pdf.set_auto_page_break(False)
        pdf.set_margins(0, 0, 0)
        # Base Unicode fonts (bundled, OFL): Amiri renders Arabic + Latin,
        # Cairo renders Latin-first text. Each slide picks by its own script;
        # missing bundles fall back to Helvetica (Latin-only). Slide TTFs
        # below may override per usage.
        has_ar_base = False
        has_en_base = False
        try:
            _ar_bundle = _bundled_font(DEFAULT_AR_FONT)
            if _ar_bundle is not None:
                pdf.add_font("pdfbase", "", str(_ar_bundle))
                has_ar_base = True
        except Exception:
            has_ar_base = False
        try:
            _en_bundle = _bundled_font(DEFAULT_EN_FONT)
            if _en_bundle is not None:
                pdf.add_font("pdfbase-en", "", str(_en_bundle))
                has_en_base = True
        except Exception:
            has_en_base = False
        for slide, _html in items:
            try:
                _slide_text = f"{slide.get('title') or ''}\n{slide.get('body') or ''}"
                if not _has_arabic(_slide_text) and has_en_base:
                    base_family = "pdfbase-en"
                elif has_ar_base:
                    base_family = "pdfbase"
                else:
                    base_family = "pdfbase-en" if has_en_base else "Helvetica"
            except Exception:
                base_family = "pdfbase" if has_ar_base else ("pdfbase-en" if has_en_base else "Helvetica")
            colors = slide.get("colors") or {}
            gradient = colors.get("gradient") or {}
            bg_hex = gradient.get("from") or colors.get("secondary") or "#FFFFFF"
            bg = _hex_to_rgb(bg_hex if isinstance(bg_hex, str) else "#FFFFFF")
            primary = _hex_to_rgb(colors.get("primary") if isinstance(colors.get("primary"), str) else "#111111")
            pdf.add_page()
            pdf.set_fill_color(*bg)
            pdf.rect(0, 0, page_w, page_h, "F")
            family_title, family_body = base_family, base_family
            for font in slide.get("fonts") or []:
                src = _resolve_asset(task_rel, str(font.get("file") or ""))
                if src is None or src.suffix.lower() not in (".ttf", ".otf"):
                    continue
                fam = f"slide{slide['slide_no']}{'t' if font.get('usage') == 'title' else 'b'}"
                try:
                    pdf.add_font(fam, "", str(src))
                    if font.get("usage") == "title":
                        family_title = fam
                    else:
                        family_body = fam
                except Exception:
                    continue
            for img in slide.get("images") or []:
                if img.get("place") != "background":
                    continue
                src = _resolve_asset(task_rel, img.get("file") or "")
                if src is None:
                    continue
                try:
                    pdf.image(str(src), x=0, y=0, w=page_w, h=page_h)
                except Exception:
                    continue
                break
            side = next((i for i in (slide.get("images") or [])
                         if i.get("place") in ("right", "left")), None)
            text_x, text_w = 15.0, page_w - 30.0
            if side is not None:
                src = _resolve_asset(task_rel, side.get("file") or "")
                if src is not None:
                    try:
                        img_w = min(80.0, page_w / 3)
                        img_x = (page_w - 15 - img_w) if side.get("place") == "right" else 15
                        if side.get("place") == "right":
                            text_x, text_w = 15.0, page_w - 30.0 - img_w - 10.0
                        else:
                            text_x, text_w = 15.0 + img_w + 10.0, page_w - 30.0 - img_w - 10.0
                        pdf.image(str(src), x=img_x, y=40, w=img_w)
                    except Exception:
                        text_x, text_w = 15.0, page_w - 30.0
            try:
                pdf.set_xy(text_x, 18)
                pdf.set_text_color(*primary)
                pdf.set_font(family_title, "", 26)
                pdf.multi_cell(text_w, 12, _shape_ar(slide["title"]), align="C")
                pdf.set_x(text_x)
                pdf.set_text_color(30, 30, 30)
                pdf.set_font(family_body, "", 14)
                pdf.multi_cell(text_w, 8, _shape_ar(slide["body"]))
            except Exception as e:
                raise RuntimeError(f"slide {slide['slide_no']}: PDF text failed: {e}") from e
            for img in slide.get("images") or []:
                if img.get("place") != "center":
                    continue
                src = _resolve_asset(task_rel, img.get("file") or "")
                if src is None:
                    continue
                try:
                    img_w = min(120.0, page_w - 60.0)
                    y = min(pdf.get_y() + 6, page_h - 60.0)
                    pdf.image(str(src), x=(page_w - img_w) / 2, y=y, w=img_w)
                except Exception:
                    continue
        pdf.output(str(pdf_path))
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"PDF conversion failed: {e}") from e
    try:
        if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
            raise RuntimeError("PDF conversion produced an empty file.")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"PDF conversion failed: {e}") from e


def _print_slide_pdf(binary: str, slide_path: Path, out_path: Path, timeout: int = 60) -> None:
    """Print ONE standalone slide document (exact page size) to a one-page PDF.

    Raises RuntimeError on any failure (caller falls back). Never raises
    anything else.
    """
    import subprocess as _sp

    url = slide_path.resolve().as_uri()
    cmd = [binary, "--headless", "--disable-gpu", "--no-sandbox",
           "--hide-scrollbars", "--print-to-pdf-no-header",
           f"--print-to-pdf={str(out_path.resolve())}", url]
    try:
        proc = _sp.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as e:
        raise RuntimeError("Chrome binary not executable") from e
    except _sp.TimeoutExpired as e:
        raise RuntimeError("Chrome print timed out") from e
    if proc.returncode != 0:
        raise RuntimeError(f"Chrome print failed: {(proc.stderr or '')[:200]}")
    try:
        if not out_path.is_file() or out_path.stat().st_size == 0:
            raise RuntimeError("Chrome print produced an empty file.")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Chrome print failed: {e}") from e


def _merge_pdfs(parts: list[Path], dest: Path) -> None:
    """Concatenate per-slide PDFs into one file (stdlib + pypdf)."""
    try:
        from pypdf import PdfWriter
    except Exception as e:
        raise RuntimeError(
            "PDF library missing: install it once with 'pip install pypdf' "
            "then call pdf_create again."
        ) from e
    try:
        writer = PdfWriter()
        for part in parts:
            writer.append(str(part))
        with open(str(dest), "wb") as fh:
            writer.write(fh)
    except Exception as e:
        raise RuntimeError(f"PDF merge failed: {e}") from e
    try:
        if not dest.is_file() or dest.stat().st_size == 0:
            raise RuntimeError("PDF merge produced an empty file.")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"PDF merge failed: {e}") from e


def _render_pdf(items: list[tuple[dict, str]], task_rel: str, combined_path: Path,
                pdf_path: Path, size: str = DEFAULT_SIZE, slide_rels: list[str] | None = None) -> str:
    """Render the final PDF, most faithful first. Returns the renderer name.

    1. Headless Chrome prints each standalone slide file exactly as designed
       (full CSS at the chosen page size; text stays selectable), then the
       pages are merged into one PDF. 2. Pure-Python fpdf2 fallback
    (editable text + images, simple professional pages) when no local Chrome
    exists. Raises RuntimeError only when both fail.
    """
    binary = _chrome_binary()
    if binary and slide_rels:
        tmpdir = None
        try:
            _bootstrap()
            from core.paths import resolve_inside_workspace as _resolve

            page = PAGE_SIZES.get(size) or PAGE_SIZES[DEFAULT_SIZE]
            tmpdir = _resolve(f"{task_rel}/.print_tmp")
            tmpdir.mkdir(parents=True, exist_ok=True)

            def _stage_asset(name: str) -> None:
                """Copy one declared image/font next to the temp print copies.

                The temp slides live in .print_tmp/, so their relative refs
                would otherwise resolve inside .print_tmp/ (missing) instead
                of the deck folder. Bounded, jailed, never raises.
                """
                try:
                    base = str(name or "").strip().replace("\\", "/").strip("/")
                    if not base or base.startswith("/") or ".." in base.split("/"):
                        return
                    for cand in (f"{task_rel}/{base}", base):
                        try:
                            src = _resolve(cand)
                        except Exception:
                            continue
                        if src.is_file():
                            try:
                                dst = tmpdir / base
                                if not dst.is_file():
                                    dst.parent.mkdir(parents=True, exist_ok=True)
                                    data = src.read_bytes()
                                    if len(data) > MAX_IMAGE_BYTES:
                                        return
                                    dst.write_bytes(data)
                            except Exception:
                                pass
                            return
                except Exception:
                    pass

            parts: list[Path] = []
            for idx, rel in enumerate(slide_rels):
                src = _resolve(rel)
                if not src.is_file():
                    raise RuntimeError(f"slide file missing for print: {rel}")
                try:
                    _slide = items[idx][0] if idx < len(items) else {}
                except Exception:
                    _slide = {}
                if isinstance(_slide, dict):
                    for _img in (_slide.get("images") or []):
                        if isinstance(_img, dict):
                            _stage_asset(_img.get("file"))
                    for _font in (_slide.get("fonts") or []):
                        if isinstance(_font, dict):
                            _stage_asset(_font.get("file"))
                # Print a temp copy with the task @page injected: standalone
                # slide files carry their own design CSS but no @page rule,
                # and Chrome would otherwise default to Letter portrait.
                # _printable_copy also drops the root dir (an RTL root makes
                # Chrome print zero images). Originals are never modified.
                html = _printable_copy(src.read_text(encoding="utf-8"))
                inject = f"<style>@page {{ size: {page['css']}; margin: 0; }}</style>"
                if re.search(r"<head[^>]*>", html, re.IGNORECASE):
                    html = re.sub(r"<head[^>]*>", lambda m: m.group(0) + inject, html,
                                  count=1, flags=re.IGNORECASE)
                else:
                    html = inject + html
                tmp_src = tmpdir / f"slide_{idx:02d}.html"
                tmp_src.write_text(html, encoding="utf-8")
                out = tmpdir / (src.stem + ".pdf")
                _print_slide_pdf(binary, tmp_src, out)
                parts.append(out)
            if not parts:
                raise RuntimeError("no slides to print")
            _merge_pdfs(parts, pdf_path)
            return "chrome"
        except Exception as e:
            print(f"[pdf_create] chrome per-slide print failed, trying fallback: {str(e)[:200]}")
        finally:
            try:
                if tmpdir is not None:
                    import shutil as _shutil

                    _shutil.rmtree(str(tmpdir), ignore_errors=True)
            except Exception:
                pass
    elif binary:
        try:
            _build_pdf_chrome(combined_path, pdf_path)
            return "chrome"
        except Exception as e:
            print(f"[pdf_create] chrome combined print failed, trying fallback: {str(e)[:200]}")
    _build_pdf_fpdf(items, task_rel, pdf_path, size)
    return "fpdf"


# --------------------------------------------------------------------------
# Bundled default fonts (inside the tool, never in Workspace)
# --------------------------------------------------------------------------

FONTS_DIR = Path(__file__).resolve().parent / "fonts"
DEFAULT_AR_FONT = "Amiri-Regular.ttf"  # Arabic + Latin serif (headings/Arabic)
DEFAULT_EN_FONT = "Cairo-Variable.ttf"  # Arabic + Latin sans (Latin-first text)
_AR_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")


def _bundled_font(name: str) -> Path | None:
    """One bundled font file, or None when it is not shipped. Never raises."""
    try:
        cand = FONTS_DIR / name
        return cand if cand.is_file() else None
    except Exception:
        return None


def _has_arabic(text: str) -> bool:
    try:
        return bool(_AR_RE.search(text or ""))
    except Exception:
        return False


def _default_font_name(slide: dict) -> str | None:
    """Bundled default for a slide: Amiri when Arabic, else Cairo.
    Each falls back to the other when its file is missing. None when
    neither is shipped. Never raises."""
    try:
        text = f"{slide.get('title') or ''}\n{slide.get('body') or ''}"
        first, second = (DEFAULT_AR_FONT, DEFAULT_EN_FONT) if _has_arabic(text) else (DEFAULT_EN_FONT, DEFAULT_AR_FONT)
        return first if _bundled_font(first) is not None else (second if _bundled_font(second) is not None else None)
    except Exception:
        return None


def _ensure_default_fonts(task_rel: str, slides: list[dict]) -> dict[str, str]:
    """Copy needed bundled defaults into the deck (fontless slides only).

    Returns {font_file_name: deck_relative_copy}. A slide that declares
    its own fonts keeps them — defaults never override user fonts.
    Missing bundle files are skipped silently. Never raises."""
    copied: dict[str, str] = {}
    try:
        needed: set[str] = set()
        for slide in slides:
            if isinstance(slide, dict) and not slide.get("fonts"):
                name = _default_font_name(slide)
                if name:
                    needed.add(name)
        if not needed:
            return copied
        _bootstrap()
        from core.paths import resolve_inside_workspace as _jail

        for name in sorted(needed):
            src = _bundled_font(name)
            if src is None:
                continue
            try:
                target = _jail(f"{task_rel}/{name}")
            except ValueError:
                continue
            try:
                if not target.is_file():
                    data = src.read_bytes()
                    if len(data) > MAX_IMAGE_BYTES:
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                copied[name] = f"{task_rel}/{name}"
            except Exception:
                continue
    except Exception:
        pass
    return copied


def _apply_default_font(html: str, slide: dict, task_rel: str) -> str:
    """Inject the bundled default @font-face into a fontless slide document.

    Slides with declared fonts are untouched. Pure string transform,
    deterministic. Never raises."""
    try:
        if not isinstance(html, str) or not html:
            return html
        if isinstance(slide, dict) and slide.get("fonts"):
            return html
        name = _default_font_name(slide if isinstance(slide, dict) else {})
        if not name:
            return html
        _bootstrap()
        from core.paths import resolve_inside_workspace as _jail

        try:
            if not _jail(f"{task_rel}/{name}").is_file():
                return html
        except ValueError:
            return html
        style = (f"<style>@font-face{{font-family:'pdfdefault';src:url('{name}');}}"
                 f"body{{font-family:'pdfdefault',Arial,sans-serif;}}</style>")
        if re.search(r"<head[^>]*>", html, re.IGNORECASE):
            return re.sub(r"<head[^>]*>", lambda m: m.group(0) + style, html,
                           count=1, flags=re.IGNORECASE)
        return style + html
    except Exception:
        return html


def _defaults_note() -> str:
    """One context line telling the slide session which defaults exist."""
    try:
        have = [n for n in (DEFAULT_AR_FONT, DEFAULT_EN_FONT) if _bundled_font(n) is not None]
        if not have:
            return ""
        return ("Bundled default fonts (already copied into the deck folder when "
                "a slide declares no fonts — reference by bare name only if you "
                "need them explicitly): " + ", ".join(have) + ".")
    except Exception:
        return ""


def _existing_slide_file(task_rel: str, slide_no: int) -> str | None:
    try:
        _bootstrap()
        from core.paths import resolve_inside_workspace

        folder = resolve_inside_workspace(task_rel)
        if not folder.is_dir():
            return None
        matches = sorted(folder.glob(f"slide_{slide_no:02d}_*.html"))
        if not matches:
            return None
        return f"{task_rel}/{matches[0].name}"
    except Exception:
        return None


def _find_task(task_id: str) -> tuple[list[dict], dict | None]:
    tasks = _load_tasks()
    for entry in tasks:
        if isinstance(entry, dict) and entry.get("task_id") == task_id:
            return tasks, entry
    return tasks, None


# --------------------------------------------------------------------------
# Public entry (called by the dispatcher with unpacked args)
# --------------------------------------------------------------------------

def execute(action: str = "create", pdf_name: str | None = None,
            slides: list | None = None, task_id: str | None = None,
            size: str | None = None, cancel_flag: Any = None) -> dict:
    act = _validate_action(action)

    if act == "get":
        tid = _validate_task_id(task_id, required=True)
        assert tid is not None
        _, entry = _find_task(tid)
        if entry is None:
            raise FileNotFoundError(f"task_id not found: {tid}")
        pdf_rel = str(entry.get("pdf_path") or "")
        _bootstrap()
        from core.paths import resolve_inside_workspace as _resolve_get

        if not pdf_rel or not _resolve_get(pdf_rel).is_file():
            raise FileNotFoundError(
                f"PDF file is missing from Workspace: {pdf_rel or '(no path)'} "
                f"(task {tid}) — rebuild it with action=update or action=create.")
        event = format_event(action=act, pdf_name=str(entry.get("pdf_name") or ""), task_id=tid)
        return {
            "event": event,
            "action": act,
            "task_id": tid,
            "pdf_name": entry.get("pdf_name"),
            "slides": entry.get("slides", []),
            "pdf_path": entry.get("pdf_path"),
            "file": entry.get("pdf_path"),
            "slides_done": entry.get("slides_done", []),
            "size": entry.get("size", DEFAULT_SIZE),
            "renderer": entry.get("renderer", ""),
            "updated_at": entry.get("updated_at"),
            "success": True,
        }

    if act == "create":
        name = _validate_pdf_name(pdf_name)
        clean_slides = _validate_slides(slides)
        page_size = _validate_size(size)
        tid = str(uuid.uuid4())[:8]
        task_rel = name
        ws = _workspace_root()
        # A deleted folder still leaves a task entry pointing at this path:
        # reusing the bare name would make two tasks share one file, so a
        # recorded folder collides exactly like an existing one on disk.
        if (ws / task_rel).exists() or any(
                isinstance(t, dict) and t.get("folder") == task_rel for t in _load_tasks()):
            task_rel = f"{name}_{tid}"
        folder = (ws / task_rel).resolve()
        try:
            folder.relative_to(ws)
        except ValueError:
            raise ValueError("Blocked: PDF folder must stay inside Workspace") from None
        folder.mkdir(parents=True, exist_ok=False)
        _check_assets(task_rel, clean_slides)
        _ensure_default_fonts(task_rel, clean_slides)
        _ctx = _internal_context()
        _note = _defaults_note()
        if _note:
            _ctx = (_ctx + "\n\n" + _note) if _ctx else _note
        system = _system_prompt()
        items: list[tuple[dict, str]] = []
        progress: list[str] = []
        first = True
        for slide in clean_slides:
            _check_cancelled(cancel_flag)
            user = _slide_user_prompt(name, task_rel, clean_slides, slide, size=page_size, context=_ctx)
            html = _run_slide(system, user, slide["slide_no"], cancel_flag)
            html = _validate_html(_apply_default_font(_normalize_image_srcs(html, slide, task_rel), slide, task_rel), slide["slide_no"])
            rel = f"{task_rel}/{_slide_filename(slide)}"
            _write_workspace_file(rel, html)
            items.append((slide, html))
            progress.append(_progress_line(slide, first=first))
            first = False
        progress.append(_merge_line(len(clean_slides)))
        combined = _combine_html(items, page_size)
        combined_rel = f"{task_rel}/combined.html"
        _write_workspace_file(combined_rel, combined)
        pdf_rel = f"{task_rel}/{_slug(name)}.pdf"
        _bootstrap()
        from core.paths import resolve_inside_workspace

        renderer = _render_pdf(items, task_rel, resolve_inside_workspace(combined_rel),
                               resolve_inside_workspace(pdf_rel), page_size,
                               [f"{task_rel}/{_slide_filename(s)}" for s in clean_slides])
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        entry = {
            "task_id": tid,
            "pdf_name": name,
            "folder": task_rel,
            "slides": clean_slides,
            "hashes": {str(s["slide_no"]): _slide_hash(s) for s in clean_slides},
            "files": {str(s["slide_no"]): f"{task_rel}/{_slide_filename(s)}" for s in clean_slides},
            "pdf_path": pdf_rel,
            "slides_done": [s["slide_no"] for s in clean_slides],
            "size": page_size,
            "renderer": renderer,
            "created_at": now,
            "updated_at": now,
        }
        tasks = _load_tasks()
        tasks.append(entry)
        if len(tasks) > 200:
            tasks = tasks[-200:]
        _save_tasks(tasks)
        return {
            "event": format_event(action=act, pdf_name=name, task_id=tid),
            "action": act,
            "task_id": tid,
            "pdf_name": name,
            "pdf_path": pdf_rel,
            "file": pdf_rel,
            "slides": clean_slides,
            "slides_done": [s["slide_no"] for s in clean_slides],
            "size": page_size,
            "renderer": renderer,
            "progress": progress,
            "review": REVIEW_LINE,
            "success": True,
        }

    # act == "update"
    tid = _validate_task_id(task_id, required=True)
    assert tid is not None
    clean_slides = _validate_slides(slides)
    tasks, entry = _find_task(tid)
    if entry is None:
        raise FileNotFoundError(f"task_id not found: {tid}")
    task_rel = str(entry.get("folder") or entry.get("pdf_name") or tid)
    name = str(entry.get("pdf_name") or task_rel)
    _check_assets(task_rel, clean_slides)
    _ensure_default_fonts(task_rel, clean_slides)
    old_hashes = entry.get("hashes") or {}
    if not isinstance(old_hashes, dict):
        old_hashes = {}
    old_files = entry.get("files") or {}
    if not isinstance(old_files, dict):
        old_files = {}
    changed = [s for s in clean_slides if old_hashes.get(str(s["slide_no"])) != _slide_hash(s)]
    new_numbers = {s["slide_no"] for s in clean_slides}
    removed = [int(k) for k in list(old_files.keys())
               if k.isdigit() and int(k) not in new_numbers]
    old_size = str(entry.get("size") or DEFAULT_SIZE)
    new_size = _validate_size(size) if size is not None else old_size
    size_changed = new_size != old_size
    if not changed and not removed and not size_changed:
        return {
            "event": format_event(action=act, pdf_name=name, task_id=tid),
            "action": act,
            "task_id": tid,
            "pdf_name": name,
            "changed": [],
            "pdf_path": entry.get("pdf_path"),
            "file": entry.get("pdf_path"),
            "slides": entry.get("slides", []),
            "message": "no changes detected",
            "success": True,
        }
    system = _system_prompt()
    _ctx = _internal_context()
    _note = _defaults_note()
    if _note:
        _ctx = (_ctx + "\n\n" + _note) if _ctx else _note
    progress: list[str] = []
    current_files: dict[str, str] = {}
    for slide in clean_slides:
        no = slide["slide_no"]
        key = str(no)
        if (old_hashes.get(key) == _slide_hash(slide) and old_files.get(key)
                and _read_workspace_file(str(old_files[key]))):
            current_files[key] = str(old_files[key])
            continue
        _check_cancelled(cancel_flag)
        old_html = None
        old_rel = old_files.get(key) or _existing_slide_file(task_rel, no)
        if old_rel:
            old_html = _read_workspace_file(str(old_rel))
        user = _slide_user_prompt(name, task_rel, clean_slides, slide, old_html=old_html, size=new_size, context=_ctx)
        html = _run_slide(system, user, no, cancel_flag)
        html = _validate_html(_apply_default_font(_normalize_image_srcs(html, slide, task_rel), slide, task_rel), no)
        rel = str(old_rel) if old_rel else f"{task_rel}/{_slide_filename(slide)}"
        _write_workspace_file(rel, html)
        current_files[key] = rel
        progress.append(_progress_line(slide))
    progress.append(_merge_line(len(clean_slides)))
    for no in removed:
        rel = str(old_files.get(str(no)) or "")
        if rel:
            try:
                _bootstrap()
                from core.paths import resolve_inside_workspace as _resolve

                target = _resolve(rel)
                if target.is_file():
                    target.unlink()
            except Exception:
                pass
    items: list[tuple[dict, str]] = []
    for slide in clean_slides:
        rel = current_files.get(str(slide["slide_no"]))
        html = _read_workspace_file(str(rel)) if rel else None
        if not html:
            raise RuntimeError(f"slide {slide['slide_no']}: HTML file missing after update")
        items.append((slide, html))
    combined = _combine_html(items, new_size)
    combined_rel = f"{task_rel}/combined.html"
    _write_workspace_file(combined_rel, combined)
    pdf_rel = str(entry.get("pdf_path") or f"{task_rel}/{_slug(name)}.pdf")
    _bootstrap()
    from core.paths import resolve_inside_workspace as _resolve2

    renderer = _render_pdf(items, task_rel, _resolve2(combined_rel), _resolve2(pdf_rel), new_size,
                           [current_files[str(s["slide_no"])] for s in clean_slides])
    entry["slides"] = clean_slides
    entry["hashes"] = {str(s["slide_no"]): _slide_hash(s) for s in clean_slides}
    entry["files"] = current_files
    entry["pdf_path"] = pdf_rel
    entry["slides_done"] = [s["slide_no"] for s in clean_slides]
    entry["size"] = new_size
    entry["renderer"] = renderer
    entry["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _save_tasks(tasks)
    return {
        "event": format_event(action=act, pdf_name=name, task_id=tid),
        "action": act,
        "task_id": tid,
        "pdf_name": name,
        "changed": [s["slide_no"] for s in changed],
        "removed": sorted(removed),
        "size_changed": size_changed,
        "pdf_path": pdf_rel,
        "file": pdf_rel,
        "slides": clean_slides,
        "slides_done": [s["slide_no"] for s in clean_slides],
        "size": new_size,
        "renderer": renderer,
        "progress": progress,
        "review": REVIEW_LINE,
        "success": True,
    }
