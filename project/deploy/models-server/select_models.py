#!/usr/bin/env python3
"""اختيار النماذج + عرض حالتها وأحجامها (واجهة تفاعلية بعلامات ثم Enter).

المسؤولية: عرض كل نموذج (اسم/حجم/حالة)، تمكين المستخدم من تعليم ما يريد
تنزيله، وكتابة الاختيار في selection.json لبقية التدفق.

الاستخدام:
    python3 select_models.py            # واجهة تفاعلية (افتراضي: المعلّم = غير المكتمل)
    python3 select_models.py --status   # طباعة الحالة فقط بدون شبكة ولا واجهة
    python3 select_models.py --all      # اختيار كل غير المكتمل تلقائياً (بدون واجهة)

الإخراج: معرّفات النماذج المختارة إلى stdout (سطر واحد)، والواجهة إلى stderr،
والاختيار التفصيلي إلى selection.json.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODELS_ROOT = (HERE.parents[1] / "models").resolve()
MANIFEST = HERE / "models.yaml"
SIZES_CACHE = HERE / "sizes.json"
SELECTION = HERE / "selection.json"
MARKER = ".download_complete"

_STATUS_AR = {"ready": "محمّل ✅", "partial": "ناقص (جزئي)", "missing": "غير موجود"}


def _load_models() -> list[dict]:
    import yaml

    data = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    return list(data.get("models", []))


def model_status(subdir: str) -> str:
    """ready (علامة + ملفات) | partial (ملفات بدون علامة) | missing."""
    d = MODELS_ROOT / subdir
    if not d.exists():
        return "missing"
    try:
        has_files = any(p.is_file() and p.name != MARKER for p in d.rglob("*"))
    except OSError:
        return "missing"
    if not has_files:
        return "missing"
    return "ready" if (d / MARKER).exists() else "partial"


def _approx_bytes(m: dict) -> int:
    return int(m.get("approx_size_mb", 1000)) * 1024 * 1024


def resolve_sizes(models: list[dict]) -> list[dict]:
    """حجم كل نموذج الفعلي من Hugging Face، مع رجوع للكاش ثم للتقدير."""
    cached: dict[str, int] = {}
    try:
        for row in json.loads(SIZES_CACHE.read_text(encoding="utf-8")):
            cached[row["repo_id"]] = int(row.get("bytes", 0))
    except Exception:
        pass

    try:
        from huggingface_hub import HfApi

        api = HfApi()
    except Exception:
        api = None

    out: list[dict] = []
    fresh: list[dict] = []
    for m in models:
        repo = m["repo_id"]
        size = None
        if api is not None:
            try:
                info = api.model_info(repo, files_metadata=True)
                size = sum(int(s.size or 0) for s in (info.siblings or []))
            except Exception:
                size = None
        if size is None:
            size = cached.get(repo) or _approx_bytes(m)
        out.append({
            "id": m["id"],
            "repo_id": repo,
            "subdir": m["subdir"],
            "type": m.get("type", ""),
            "note": m.get("note", ""),
            "bytes": int(size),
        })
        fresh.append({"repo_id": repo, "bytes": int(size)})
    try:
        SIZES_CACHE.write_text(json.dumps(fresh, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return out


def _human(n: int) -> str:
    val = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < 1024:
            return f"{val:.2f} {unit}"
        val /= 1024
    return f"{val:.2f} PB"


def _read_key() -> str:
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            nxt = sys.stdin.read(1)
            if nxt == "[":
                third = sys.stdin.read(1)
                return {"A": "up", "B": "down"}.get(third, "other")
            return "esc"
        if ch in ("\r", "\n"):
            return "enter"
        if ch == " ":
            return "space"
        return ch.lower()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _interactive(entries: list[dict]) -> list[int]:
    """واجهة بعلامات: أسهم للتحريك، Space للتعليم، a للكل، Enter للتأكيد."""
    selected = {i for i, e in enumerate(entries) if e["status"] != "ready"}
    if not selected:
        selected = set(range(len(entries)))
    cursor = 0
    out = sys.stderr
    out.write("اختر النماذج اللي تحمّلها:\n")
    out.flush()
    lines = 0
    while True:
        if lines:
            out.write(f"\x1b[{lines}A")
        for i, e in enumerate(entries):
            mark = ">" if i == cursor else " "
            box = "x" if i in selected else " "
            label = f"{e['id']:<10}"
            out.write(f"\x1b[2K  {mark} [{box}] {label} {_human(e['bytes']):>10}   {_STATUS_AR[e['status']]}\n")
        out.write("\x1b[2K  ↑/↓ تحريك · Space تعليم · a الكل · Enter تأكيد\n")
        out.flush()
        lines = len(entries) + 1
        try:
            key = _read_key()
        except KeyboardInterrupt:
            out.write("\n")
            raise SystemExit(130)
        if key == "up":
            cursor = (cursor - 1) % len(entries)
        elif key == "down":
            cursor = (cursor + 1) % len(entries)
        elif key == "space":
            selected.symmetric_difference_update({cursor})
        elif key == "a":
            selected = set() if len(selected) == len(entries) else set(range(len(entries)))
        elif key == "esc":
            out.write("\nتم الإلغاء.\n")
            raise SystemExit(130)
        elif key == "enter":
            if not selected:
                continue
            out.write("\n")
            break
    return sorted(selected)


def _numbered(entries: list[dict]) -> list[int]:
    """بديل بدون طرفية تفاعلية: إدخال الأرقام مفصولة بمسافة."""
    out = sys.stderr
    out.write("لا توجد طرفية تفاعلية — اكتب أرقام النماذج التي تريدها مفصولة بمسافة\n")
    for i, e in enumerate(entries, 1):
        out.write(f"  {i}) {e['id']:<10} {_human(e['bytes']):>10}   {_STATUS_AR[e['status']]}\n")
    default = [i for i, e in enumerate(entries, 1) if e["status"] != "ready"] or list(range(1, len(entries) + 1))
    out.write(f"  [افتراضي: {' '.join(map(str, default))}] ثم Enter: ")
    out.flush()
    raw = sys.stdin.readline().strip()
    if not raw:
        return [i - 1 for i in default]
    picked = []
    for tok in raw.replace(",", " ").split():
        if tok.isdigit() and 1 <= int(tok) <= len(entries):
            picked.append(int(tok) - 1)
    return sorted(set(picked))


def main(argv: list[str]) -> int:
    try:
        models = _load_models()
    except Exception as e:
        print(f"تعذّر قراءة models.yaml: {e}", file=sys.stderr)
        return 1

    if "--status" in argv:
        for m in models:
            print(f"{m['id']}\t{model_status(m['subdir'])}\t{m['subdir']}")
        return 0

    if "--table" in argv:
        entries = resolve_sizes(models)
        for e in entries:
            e["status"] = model_status(e["subdir"])
        print(f"  {'النموذج':<10} {'الحجم':>12}   الحالة")
        print(f"  {'-'*10} {'-'*12}   {'-'*12}")
        for e in entries:
            print(f"  {e['id']:<10} {_human(e['bytes']):>12}   {_STATUS_AR[e['status']]}")
        return 0

    entries = resolve_sizes(models)
    for e in entries:
        e["status"] = model_status(e["subdir"])

    if "--all" in argv or not sys.stdin.isatty():
        # غير تفاعلي (--yes أو بلا طرفية): كل غير المكتمل تلقائياً.
        chosen = [i for i, e in enumerate(entries) if e["status"] != "ready"] or list(range(len(entries)))
    else:
        try:
            chosen = _interactive(entries)
        except Exception:
            chosen = _numbered(entries)

    if not chosen:
        print("لم تختر أي نموذج.", file=sys.stderr)
        return 1

    selection = [entries[i] for i in chosen]
    try:
        SELECTION.write_text(json.dumps(selection, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        print(f"تعذّر كتابة selection.json: {e}", file=sys.stderr)
        return 1

    print(" ".join(e["id"] for e in selection))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))