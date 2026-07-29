"""OCR-scan the NEW (non-PHMPT) documents of the FDA-FOIA-2026-6007 trove for
FOIA exemption markers, using the same marker regex as the PHMPT pipeline
(scripts/extract_module_exemptions.py) so the counts are directly comparable.

The 7,179 PHMPT-overlapping trove docs are NOT scanned here — their marker
counts are reused from docs/data/exemptions.json by build_trove_exemptions.py.
Only the ~238 status==NEW documents are scanned, because they are released only
in this production and are not in the PHMPT exemption dataset.

Per-file results are cached under data/cache/trove_new_exemptions/<md5>.json so
the scan is resumable and re-runs are cheap. Aggregated output:
data/trove_new_exemptions.json.

Run:  uv run python scripts/scan_trove_new_exemptions.py
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

import fitz  # PyMuPDF
import pytesseract
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "FDA-FOIA-2026-6007" / "site"
DOCS_JSON = SITE / "documents.json"
FILE_ROOTS = [
    ROOT / "FDA-FOIA-2026-6007" / "new-files",
    SITE / "files",
]
CACHE_DIR = ROOT / "data" / "cache" / "trove_new_exemptions"
OUT = ROOT / "data" / "trove_new_exemptions.json"

# identical to scripts/extract_module_exemptions.py
MARKER_RE = re.compile(r"\(\s*b\s*\)\s*\(\s*(\d+)\s*\)(?:\s*\(\s*([A-F])\s*\))?")
OCR_TEXT_THRESHOLD = 30
OCR_DPI = 300


def normalize_marker(num: str, subpart: str | None) -> str:
    return f"(b)({num})" + (f"({subpart})" if subpart else "")


def build_local_index() -> dict[str, str]:
    """basename -> absolute path, first match wins."""
    idx: dict[str, str] = {}
    for root in FILE_ROOTS:
        if not root.exists():
            continue
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                idx.setdefault(f, os.path.join(dirpath, f))
    return idx


def cache_path(filename: str) -> Path:
    h = hashlib.md5(filename.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{h}.json"


def page_text(page: fitz.Page) -> tuple[str, bool]:
    """Return (text, ocr_used). Use the text layer; OCR only if it's thin."""
    text = page.get_text()
    if len(text.strip()) >= OCR_TEXT_THRESHOLD:
        return text, False
    try:
        pix = page.get_pixmap(dpi=OCR_DPI)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        return pytesseract.image_to_string(img), True
    except Exception:
        return text, False


def scan_pdf(path: str, filename: str) -> dict:
    try:
        doc = fitz.open(path)
    except Exception as e:
        return {"filename": filename, "error": f"{type(e).__name__}: {e}",
                "total_markers": 0, "by_marker": {}}
    by_marker: Counter[str] = Counter()
    ocr_candidate_pages: list[int] = []
    pages_ocred = 0
    try:
        for i in range(doc.page_count):
            text, ocr_used = page_text(doc.load_page(i))
            if ocr_used:
                pages_ocred += 1
            if len(text.strip()) < OCR_TEXT_THRESHOLD:
                ocr_candidate_pages.append(i + 1)
            for m in MARKER_RE.finditer(text):
                by_marker[normalize_marker(m.group(1), m.group(2))] += 1
        total_pages = doc.page_count
    finally:
        doc.close()
    return {
        "filename": filename,
        "total_pages": total_pages,
        "total_markers": sum(by_marker.values()),
        "by_marker": dict(by_marker),
        "ocr_candidate_pages": ocr_candidate_pages,
        "pages_ocred": pages_ocred,
    }


def main() -> None:
    if not DOCS_JSON.exists():
        sys.exit(f"{DOCS_JSON} missing")
    docs = json.loads(DOCS_JSON.read_text())
    new_docs = [d for d in docs if d.get("status") == "NEW"]
    print(f"{len(new_docs)} NEW documents to scan", flush=True)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    local = build_local_index()

    records: list[dict] = []
    for n, d in enumerate(new_docs, 1):
        fn = d["filename"]
        cp = cache_path(fn)
        if cp.exists():
            records.append(json.loads(cp.read_text()))
            continue
        ext = (d.get("ext") or "").lower()
        path = local.get(fn)
        if path is None:
            rec = {"filename": fn, "error": "not found locally",
                   "total_markers": 0, "by_marker": {}}
        elif ext != "pdf":
            rec = {"filename": fn, "is_data_file": True,
                   "total_markers": 0, "by_marker": {}}
        else:
            rec = scan_pdf(path, fn)
        cp.write_text(json.dumps(rec))
        records.append(rec)
        print(f"[{n}/{len(new_docs)}] {rec.get('total_markers', 0):>6} markers  "
              f"{fn[:80]}", flush=True)

    summary = {
        "files": len(records),
        "files_with_markers": sum(1 for r in records if r.get("total_markers")),
        "files_errored": [r["filename"] for r in records if r.get("error")],
        "total_marker_occurrences": sum(r.get("total_markers", 0) for r in records),
        "by_marker": dict(sum((Counter(r.get("by_marker", {})) for r in records),
                              Counter())),
    }
    OUT.write_text(json.dumps({"summary": summary, "files": records}, indent=1))
    print(f"\nwrote {OUT}")
    print(f"  files: {summary['files']}  with markers: "
          f"{summary['files_with_markers']}  total markers: "
          f"{summary['total_marker_occurrences']}")
    print(f"  by_marker: {summary['by_marker']}")


if __name__ == "__main__":
    main()
