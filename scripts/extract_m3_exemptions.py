"""Detect FOIA exemption markers in every M3 PDF.

For each PDF under data/files/, iterate pages and find every occurrence
of the FOIA exemption marker pattern, e.g. `(b)(4)`, `(b) (4)`,
`(b)(7)(C)`, `(b) (7) (C)`.

Per-file results are cached at data/cache/m3_exemptions/<filename>.json
so re-runs are fast. Aggregated output written to data/m3_exemptions.json.

Pages with very low text density are flagged as `ocr_candidate`, since
their redaction labels are likely raster (image) and would need OCR to
detect — a separate future pass.

Usage:
    uv run python scripts/extract_m3_exemptions.py            # all M3
    uv run python scripts/extract_m3_exemptions.py --rebuild  # ignore cache
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import fitz  # PyMuPDF
from tqdm import tqdm

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FILES_DIR = DATA / "files"
CACHE_DIR = DATA / "cache" / "m3_exemptions"
INDEX = ROOT / "docs" / "data" / "index.json"
OUT = DATA / "m3_exemptions.json"

# Matches (b)(N) and (b)(N)(X) with arbitrary whitespace between tokens.
# Captures the digits and optional subpart so we can normalize the marker.
MARKER_RE = re.compile(r"\(\s*b\s*\)\s*\(\s*(\d+)\s*\)(?:\s*\(\s*([A-F])\s*\))?")

# Pages with fewer than this many text characters are flagged as
# potentially image-only (OCR would be needed to detect markers).
OCR_TEXT_THRESHOLD = 30


def normalize_marker(num: str, subpart: str | None) -> str:
    """Return canonical form e.g. '(b)(4)' or '(b)(7)(C)'."""
    return f"(b)({num})" + (f"({subpart})" if subpart else "")


def scan_pdf(pdf_path: Path) -> dict:
    """Open one PDF and return a record of exemption markers per page."""
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

    total_pages = doc.page_count
    exemption_pages: list[dict] = []
    ocr_candidate_pages: list[int] = []

    try:
        for page_num in range(total_pages):
            page = doc.load_page(page_num)
            text = page.get_text("text") or ""

            if len(text.strip()) < OCR_TEXT_THRESHOLD:
                ocr_candidate_pages.append(page_num + 1)

            # Tally markers by canonical form on this page
            per_marker: Counter[str] = Counter()
            for m in MARKER_RE.finditer(text):
                per_marker[normalize_marker(m.group(1), m.group(2))] += 1

            for marker, count in per_marker.items():
                exemption_pages.append({
                    "page": page_num + 1,
                    "marker": marker,
                    "count": count,
                })
    finally:
        doc.close()

    return {
        "total_pages": total_pages,
        "exemption_pages": exemption_pages,
        "ocr_candidate_pages": ocr_candidate_pages,
    }


def main(rebuild: bool = False) -> None:
    if not FILES_DIR.exists():
        sys.exit(f"{FILES_DIR} missing — run scripts/extract_m3_files.py first")

    if not INDEX.exists():
        sys.exit(f"{INDEX} missing — run scripts/build_index.py first")

    index = json.loads(INDEX.read_text())
    m3_records = {
        r["filename"]: r
        for r in index
        if r.get("module") == "M3"
        and r.get("extension") == "pdf"
    }
    print(f"{len(m3_records)} M3 PDFs registered in index")

    # Match extracted files to index records
    pdfs: list[tuple[Path, dict]] = []
    for batch_dir in sorted(FILES_DIR.iterdir()):
        if not batch_dir.is_dir():
            continue
        for pdf_path in sorted(batch_dir.glob("*_M3_*.pdf")):
            rec = m3_records.get(pdf_path.name)
            if rec is None:
                # Could be in a different module; skip silently
                continue
            pdfs.append((pdf_path, rec))

    print(f"{len(pdfs)} M3 PDFs found on disk")
    print()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    by_marker: Counter[str] = Counter()
    files_with_markers = 0
    files_ocr_flagged = 0
    files_errored: list[str] = []

    for pdf_path, rec in tqdm(pdfs, desc="scanning", unit="pdf"):
        cache_path = CACHE_DIR / f"{pdf_path.name}.json"
        if cache_path.exists() and not rebuild:
            scan = json.loads(cache_path.read_text())
        else:
            scan = scan_pdf(pdf_path)
            cache_path.write_text(json.dumps(scan, indent=2))

        if "error" in scan:
            files_errored.append(pdf_path.name)
            results.append({
                "filename": pdf_path.name,
                "id": rec["id"],
                "batch_code": rec.get("batch_code"),
                "company": rec.get("company"),
                "license": rec.get("license"),
                "error": scan["error"],
            })
            continue

        if scan["exemption_pages"]:
            files_with_markers += 1
        if scan["ocr_candidate_pages"]:
            files_ocr_flagged += 1

        for entry in scan["exemption_pages"]:
            by_marker[entry["marker"]] += entry["count"]

        results.append({
            "filename": pdf_path.name,
            "id": rec["id"],
            "batch_code": rec.get("batch_code"),
            "company": rec.get("company"),
            "license": rec.get("license"),
            "total_pages": scan["total_pages"],
            "exemption_pages": scan["exemption_pages"],
            "ocr_candidate_pages": scan["ocr_candidate_pages"],
        })

    total_occurrences = sum(by_marker.values())
    summary = {
        "files_processed": len(pdfs),
        "files_with_exemptions": files_with_markers,
        "files_ocr_flagged": files_ocr_flagged,
        "files_errored": files_errored,
        "total_marker_occurrences": total_occurrences,
        "by_exemption": dict(sorted(by_marker.items(), key=lambda kv: -kv[1])),
    }

    OUT.write_text(json.dumps({"summary": summary, "files": results}, indent=2))

    print()
    print("=" * 60)
    print("M3 EXEMPTION SCAN COMPLETE")
    print("=" * 60)
    print(f"Files processed:       {len(pdfs)}")
    print(f"Files with markers:    {files_with_markers}")
    print(f"Files OCR-flagged:     {files_ocr_flagged}")
    print(f"Files with errors:     {len(files_errored)}")
    print(f"Total marker occurrences: {total_occurrences}")
    print()
    print("By exemption (top 10):")
    for marker, count in list(summary["by_exemption"].items())[:10]:
        print(f"  {marker:>12}  {count:>6}")
    print()
    print(f"Wrote: {OUT}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--rebuild", action="store_true", help="ignore per-file cache")
    args = p.parse_args()
    main(rebuild=args.rebuild)
