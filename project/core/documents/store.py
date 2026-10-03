"""
Document store - owns uploaded office/PDF documents end to end.

Single responsibility: convert Workspace documents to organized markdown
(via MarkItDown), split into 300-word chunks (whole words only), embed
each chunk, and answer semantic chunk queries per file.

Ownership:
  - Source files live ONLY in X5Agent/Workspace (the model device).
    file_id is the Workspace-relative path, e.g. "report.pdf".
  - Chunk embeddings live in X5Agent/Data/vector/document_embeddings.json
    (writes ALWAYS Data, same rule as every other vector store).
  - When a Workspace file is deleted, its embeddings are deleted too:
    every search/ensure checks existence first, and purge_missing()
    removes all orphans (called best-effort from workspace list,
    server workspace APIs, and the context builder).

Limits (bounded, never unbounded):
  - max file size 10MB, max 2000 chunks per file,
  - max 20 vision-enhanced PDF pages per file, vision pool of 4 workers.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

# Files MarkItDown converts locally without any cloud key.
# Office + PDF + plain data formats (mirrors `pip install "markitdown[all]"`).
SUPPORTED_EXTENSIONS = frozenset({
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".csv",
    ".pptx", ".ppt", ".html", ".htm", ".txt", ".md",
    ".json", ".xml",
})

# Fixed vision prompt for scanned/empty PDF pages (< 50 chars of text).
VISION_PAGE_PROMPT = (
    "Extract everything written on this page in the same language "
    "without hallucination or distortion."
)

CHUNK_WORDS = 300
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_CHUNKS = 2000
MAX_VISION_PAGES = 20
VISION_WORKERS = 4
THIN_PAGE_CHARS = 50


def is_supported(filename: str) -> bool:
    """True when the file extension is convertible by MarkItDown."""
    if not isinstance(filename, str) or not filename.strip():
        return False
    return Path(filename.strip()).suffix.lower() in SUPPORTED_EXTENSIONS


def normalize_file_id(file_id: Any) -> str:
    """Validate a model-supplied file id (Workspace-relative path)."""
    if not isinstance(file_id, str):
        raise TypeError("file_id must be string")
    s = file_id.strip().replace("\\", "/")
    if not s:
        raise ValueError("file_id must be non-empty")
    if len(s) > 500:
        raise ValueError("file_id too long (max 500)")
    if s.startswith("/") or ".." in s.split("/") or "\x00" in s:
        raise ValueError("Blocked: file_id must stay inside Workspace.")
    if Path(s).suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type: {Path(s).suffix or '(no extension)'}. "
            f"Supported: {sorted(SUPPORTED_EXTENSIONS)}"
        )
    return s


def _workspace_file(file_id: str) -> Path:
    """Resolve a validated file_id strictly inside Workspace."""
    import sys as _sys
    proj = Path(__file__).resolve().parents[2]
    if str(proj) not in _sys.path:
        _sys.path.insert(0, str(proj))
    from core.paths import resolve_inside_workspace
    return resolve_inside_workspace(file_id)


def _store_path() -> Path:
    try:
        import sys as _sys
        proj = Path(__file__).resolve().parents[2]
        if str(proj) not in _sys.path:
            _sys.path.insert(0, str(proj))
        from core.paths import data_vector_file
        return data_vector_file("document_embeddings.json")
    except Exception:
        return Path(__file__).resolve().parents[2] / "storage" / "vector" / "document_embeddings.json"


def _load_registry() -> dict:
    try:
        p = _store_path()
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("files"), dict):
                return data
    except Exception:
        pass
    return {"files": {}}


def _save_registry(data: dict) -> None:
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def chunk_markdown(text: str, words: int = CHUNK_WORDS) -> list[str]:
    """Split markdown into ~300-word chunks. Split points are whitespace,
    so the last word of every chunk is always a complete word."""
    if not isinstance(text, str):
        raise TypeError("text must be string")
    if not isinstance(words, int) or words < 1:
        raise ValueError("words must be a positive integer")
    tokens = text.split()
    if not tokens:
        return []
    return [" ".join(tokens[i:i + words]) for i in range(0, len(tokens), words)][:MAX_CHUNKS]


def _pdf_page_texts(path: Path) -> list[str]:
    """Per-page text via pypdf. Never raises (empty list on failure)."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        return [str(page.extract_text() or "") for page in reader.pages[:500]]
    except Exception:
        return []


def _render_pdf_page(path: Path, index: int) -> Path | None:
    """Render one PDF page to a temp PNG for the vision model. None on failure."""
    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(str(path))
        try:
            if index < 0 or index >= len(pdf):
                return None
            bitmap = pdf[index].render(scale=2.0)
            pil_image = bitmap.to_pil()
        finally:
            try:
                pdf.close()
            except Exception:
                pass
        tmp = Path(tempfile.mkstemp(suffix=".png")[1])
        pil_image.save(str(tmp), format="PNG")
        return tmp
    except Exception:
        return None


def _vision_transcribe(image_path: Path) -> str:
    """Run the fixed extraction prompt on one page image. Empty on failure."""
    try:
        import sys as _sys
        proj = Path(__file__).resolve().parents[2]
        if str(proj) not in _sys.path:
            _sys.path.insert(0, str(proj))
        from models.vision.service import analyze_image
        out = analyze_image(image_path=str(image_path), prompt=VISION_PAGE_PROMPT)
        return out.strip() if isinstance(out, str) else ""
    except Exception:
        return ""


def _vision_enhance_pdf(path: Path, markdown: str) -> str:
    """Append vision transcriptions for PDF pages with < 50 chars of text.

    All thin pages are transcribed concurrently (ThreadPool, max 4 workers),
    never one-by-one. Failures keep the original text silently per page —
    conversion itself must never fail because vision is unavailable.
    """
    pages = _pdf_page_texts(path)
    if not pages:
        return markdown
    thin = [i for i, t in enumerate(pages) if len(t.strip()) < THIN_PAGE_CHARS]
    if not thin:
        return markdown
    thin = thin[:MAX_VISION_PAGES]

    def _one(index: int) -> tuple[int, str]:
        img = _render_pdf_page(path, index)
        if img is None:
            return index, ""
        try:
            return index, _vision_transcribe(img)
        finally:
            try:
                img.unlink(missing_ok=True)
            except Exception:
                pass

    transcribed: dict[int, str] = {}
    try:
        with ThreadPoolExecutor(max_workers=VISION_WORKERS) as pool:
            for index, text in pool.map(_one, thin):
                if text:
                    transcribed[index] = text
    except Exception:
        return markdown
    if not transcribed:
        return markdown
    extra = "".join(
        f"\n\n[Page {i + 1} vision transcription]\n{transcribed[i]}"
        for i in sorted(transcribed)
    )
    return markdown + extra


def convert_to_markdown(path: Path) -> str:
    """Convert one Workspace file to organized markdown. Raises on failure."""
    if not path.is_file():
        raise FileNotFoundError(f"Document not found: {path.name}")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Document too large (max 100MB)")
    try:
        from markitdown import MarkItDown
        result = MarkItDown().convert(str(path))
        markdown = str(result.text_content or "")
    except Exception as e:
        raise RuntimeError(f"Cannot convert {path.name}: {e}") from e
    if path.suffix.lower() == ".pdf":
        markdown = _vision_enhance_pdf(path, markdown)
    if not markdown.strip():
        raise ValueError(f"No readable text in {path.name}")
    return markdown


def delete_file_embeddings(file_id: str) -> bool:
    """Delete every embedding chunk of one file. Returns True if removed."""
    try:
        fid = file_id.strip().replace("\\", "/") if isinstance(file_id, str) else ""
    except Exception:
        return False
    if not fid:
        return False
    data = _load_registry()
    if fid not in data["files"]:
        return False
    try:
        del data["files"][fid]
        _save_registry(data)
        return True
    except Exception:
        return False


def purge_missing() -> int:
    """Delete embeddings whose Workspace file no longer exists. Never raises."""
    try:
        data = _load_registry()
        if not data["files"]:
            return 0
        gone = []
        for fid in list(data["files"]):
            try:
                if not _workspace_file(fid).is_file():
                    gone.append(fid)
            except Exception:
                gone.append(fid)
        for fid in gone:
            try:
                del data["files"][fid]
            except Exception:
                continue
        if gone:
            _save_registry(data)
        return len(gone)
    except Exception:
        return 0


def ensure_indexed(file_id: str) -> dict:
    """Convert + chunk + embed a Workspace file unless already indexed.

    If the Workspace file is gone, its embeddings are deleted and
    FileNotFoundError is raised. Re-indexes automatically when the file
    hash changed. Returns bounded metadata (never raw embeddings).
    """
    fid = normalize_file_id(file_id)
    path = _workspace_file(fid)
    if not path.is_file():
        delete_file_embeddings(fid)
        raise FileNotFoundError(f"Document not found in Workspace: {fid}")
    blob = path.read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    data = _load_registry()
    entry = data["files"].get(fid)
    if isinstance(entry, dict) and entry.get("hash") == digest and entry.get("chunks"):
        return _public_meta(fid, entry)
    markdown = convert_to_markdown(path)
    chunks = chunk_markdown(markdown)
    if not chunks:
        raise ValueError(f"No readable text in {fid}")
    try:
        import sys as _sys
        proj = Path(__file__).resolve().parents[2]
        if str(proj) not in _sys.path:
            _sys.path.insert(0, str(proj))
        from models.embedding.service import embed_texts
        vectors = embed_texts(chunks)
    except FileNotFoundError as e:
        raise FileNotFoundError(str(e)) from e
    stored = [{"index": i, "text": c, "embedding": v}
              for i, (c, v) in enumerate(zip(chunks, vectors))]
    data["files"][fid] = {
        "hash": digest,
        "size": len(blob),
        "chunks": stored,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _save_registry(data)
    return _public_meta(fid, data["files"][fid])


def _public_meta(fid: str, entry: dict) -> dict:
    chunks = entry.get("chunks") if isinstance(entry, dict) else []
    return {
        "file_id": fid,
        "size": entry.get("size", 0),
        "chunk_count": len(chunks) if isinstance(chunks, list) else 0,
        "updated_at": entry.get("updated_at", ""),
    }


def list_documents() -> list[dict]:
    """Workspace supported files + index state. Bounded, never raises."""
    try:
        purge_missing()
    except Exception:
        pass
    out: list[dict] = []
    try:
        import sys as _sys
        proj = Path(__file__).resolve().parents[2]
        if str(proj) not in _sys.path:
            _sys.path.insert(0, str(proj))
        from core.paths import workspace_root
        ws = workspace_root()
        if not ws.is_dir():
            return []
        found: list[str] = []
        for p in sorted(ws.rglob("*"))[:500]:
            try:
                if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS:
                    found.append(p.relative_to(ws).as_posix())
            except Exception:
                continue
            if len(found) >= 100:
                break
        indexed = _load_registry().get("files", {})
        for fid in found[:100]:
            entry = indexed.get(fid)
            if isinstance(entry, dict) and entry.get("chunks"):
                out.append({**_public_meta(fid, entry), "indexed": True})
            else:
                try:
                    size = (ws / fid).stat().st_size
                except Exception:
                    size = 0
                out.append({"file_id": fid, "size": size, "chunk_count": 0,
                            "updated_at": "", "indexed": False})
    except Exception:
        pass
    return out


def search(file_id: str, query: str, top_k: int = 3) -> dict:
    """Return the top_k chunks of one file most similar to query."""
    fid = normalize_file_id(file_id)
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be non-empty string")
    q = query.strip()
    if len(q) > 2000:
        raise ValueError("query too long (max 2000)")
    if not isinstance(top_k, int) or isinstance(top_k, bool):
        raise TypeError("top_k must be integer")
    if top_k < 1 or top_k > 20:
        raise ValueError("top_k must be 1..20")
    meta = ensure_indexed(fid)
    entry = _load_registry()["files"][fid]
    try:
        import sys as _sys
        proj = Path(__file__).resolve().parents[2]
        if str(proj) not in _sys.path:
            _sys.path.insert(0, str(proj))
        from models.embedding.service import embed_query
        import numpy as _np
        q_vec = _np.array(embed_query(q), dtype=_np.float32)
        scored = []
        for c in entry["chunks"]:
            try:
                score = float(_np.dot(q_vec, _np.array(c["embedding"], dtype=_np.float32)))
            except Exception:
                continue
            scored.append((score, c))
        scored.sort(reverse=True, key=lambda x: x[0])
        picked = scored[:top_k]
    except FileNotFoundError:
        raise
    except Exception as e:
        raise RuntimeError(f"Document search failed for {fid}: {e}") from e
    results = [{"index": c["index"], "text": c["text"], "score": round(s, 4)}
               for s, c in picked]
    return {"file_id": fid, "query": q, "top_k": top_k,
            "chunk_count": meta["chunk_count"], "results": results}
