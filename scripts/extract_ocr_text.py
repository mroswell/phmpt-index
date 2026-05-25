"""OCR every PDF page flagged as `ocr_candidate` by the existing
scanners (i.e. pages where PyMuPDF's text extraction returned almost
nothing — almost certainly scanned image-only documents).

Pipeline:
  1. Collect every (filename, page) pair recorded as ocr_candidate
     across all per-file scan caches:
       data/cache/exemptions/M[1-5]/{filename}.json     (ocr_candidate_pages)
       data/cache/exemptions/individual/{filename}.json (ocr_candidate_pages)
       data/cache/pharmacovigilance/{filename}.json     (none — no ocr flag)
  2. For each pair (skipping those already in cache):
       a. Open the source PDF — from data/cache/individual_pdfs/ if it
          exists there, else stream from data/zips/{batch_code}/{zip}.
       b. Render page at 300 DPI with PyMuPDF.
       c. Run Tesseract OCR via pytesseract.
       d. Cache the text at data/cache/ocr_text/{filename}__p{NNNN}.txt.

Each output file is small; the directory could grow large.

Run:
    uv run python scripts/extract_ocr_text.py            # all pending
    uv run python scripts/extract_ocr_text.py --limit 50 # smoke test
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from collections import defaultdict
from pathlib import Path

import fitz  # PyMuPDF
import pytesseract
import zipfile_deflate64 as zipfile
from PIL import Image
from tqdm import tqdm

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
INDEX = ROOT / "docs" / "data" / "index.json"

INDIVIDUAL_PDF_CACHE = DATA / "cache" / "individual_pdfs"
ZIPS_DIR = DATA / "zips"
EXEMPTIONS_CACHE = DATA / "cache" / "exemptions"
OCR_CACHE = DATA / "cache" / "ocr_text"

DPI = 300


def collect_ocr_targets() -> dict[str, set[int]]:
    """Walk per-file scan caches and return {filename: {page_num, ...}}."""
    targets: dict[str, set[int]] = defaultdict(set)
    if not EXEMPTIONS_CACHE.exists():
        return targets

    for cache_file in EXEMPTIONS_CACHE.rglob("*.json"):
        try:
            doc = json.loads(cache_file.read_text())
        except Exception:
            continue
        # Cache filename is "{pdf_filename}.json"
        pdf_name = cache_file.name[:-5]  # strip .json
        pages = doc.get("ocr_candidate_pages") or []
        for p in pages:
            targets[pdf_name].add(p)
    return targets


def open_pdf_bytes(filename: str, idx_record: dict | None) -> bytes | None:
    """Return PDF bytes from local cache or from the source ZIP."""
    cache_pdf = INDIVIDUAL_PDF_CACHE / filename
    if cache_pdf.exists():
        return cache_pdf.read_bytes()
    if not idx_record:
        return None
    zip_src = idx_record.get("zip_source")
    if not zip_src:
        return None
    zip_path = ZIPS_DIR / idx_record["batch_code"] / zip_src
    if not zip_path.exists():
        return None
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if info.filename.rsplit("/", 1)[-1] == filename:
                    with zf.open(info) as f:
                        return f.read()
    except Exception:
        return None
    return None


def ocr_one_page(pdf_bytes: bytes, page_num: int) -> str | None:
    """Render a single PDF page and OCR it. Returns plain text."""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return None
    try:
        if page_num < 1 or page_num > doc.page_count:
            return None
        page = doc.load_page(page_num - 1)
        pix = page.get_pixmap(dpi=DPI)
        img_bytes = pix.tobytes("png")
        img = Image.open(io.BytesIO(img_bytes))
        return pytesseract.image_to_string(img) or ""
    except Exception:
        return None
    finally:
        doc.close()


def main(limit: int | None = None) -> None:
    if not INDEX.exists():
        sys.exit(f"{INDEX} missing — run scripts/build_index.py first")

    OCR_CACHE.mkdir(parents=True, exist_ok=True)

    targets = collect_ocr_targets()
    total_pages_flagged = sum(len(v) for v in targets.values())
    print(f"OCR candidates: {total_pages_flagged} pages across {len(targets)} files")

    # Build flat list of (filename, page) pairs that still need OCR
    todo: list[tuple[str, int]] = []
    cache_hits = 0
    for fname, pages in targets.items():
        for p in sorted(pages):
            if (OCR_CACHE / f"{fname}__p{p:04d}.txt").exists():
                cache_hits += 1
                continue
            todo.append((fname, p))

    print(f"Cache hits: {cache_hits}")
    print(f"To do: {len(todo)}")
    if limit:
        todo = todo[:limit]
        print(f"Limited to first {limit}")
    print()

    if not todo:
        return

    idx_records: dict[str, dict] = {}
    for r in json.loads(INDEX.read_text()):
        idx_records.setdefault(r["filename"], r)

    # Group todo by file so each PDF opens once
    todo_by_file: dict[str, list[int]] = defaultdict(list)
    for fname, p in todo:
        todo_by_file[fname].append(p)

    successes = 0
    failures: list[str] = []

    pbar = tqdm(total=len(todo), desc="OCR", unit="pg")
    for fname, pages in todo_by_file.items():
        idx_rec = idx_records.get(fname)
        body = open_pdf_bytes(fname, idx_rec)
        if body is None:
            failures.extend(f"{fname}__p{p:04d}" for p in pages)
            pbar.update(len(pages))
            continue

        for p in pages:
            text = ocr_one_page(body, p)
            if text is None:
                failures.append(f"{fname}__p{p:04d}")
            else:
                (OCR_CACHE / f"{fname}__p{p:04d}.txt").write_text(text, encoding="utf-8")
                successes += 1
            pbar.update(1)
    pbar.close()

    print()
    print("=" * 60)
    print(f"OCR PASS COMPLETE")
    print(f"  Successes:  {successes}")
    print(f"  Failures:   {len(failures)}")
    print(f"  OCR cache:  {OCR_CACHE}")
    if failures[:5]:
        print(f"  First failures: {failures[:5]}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, help="OCR only the first N pages (smoke test)")
    args = p.parse_args()
    main(limit=args.limit)
