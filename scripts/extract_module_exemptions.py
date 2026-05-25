"""Detect FOIA exemption markers in every PDF for a given module.

Streams PDFs directly from the existing data/zips/*.zip archives so we
don't need extracted files on disk. Per-file results are cached at
data/cache/exemptions/<MODULE>/<filename>.json so re-runs are fast.

Aggregated output: data/<MODULE>_exemptions.json.

Pages with very low text density are flagged as `ocr_candidate`, since
their redaction labels are likely raster (image) and would need OCR to
detect — a separate future pass.

PDFs that don't have a zip_source (i.e. individual files from
company-documents pages) are skipped — phmpt.org's wp-content is
Cloudflare-blocked so we can't access their content. They're reported
in the summary as `skipped_no_zip`.

Usage:
    uv run python scripts/extract_module_exemptions.py M5
    uv run python scripts/extract_module_exemptions.py M2 --rebuild
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import fitz  # PyMuPDF
import zipfile_deflate64 as zipfile  # matches scripts/extract_toc.py
from tqdm import tqdm

from _pdf_text import get_page_text  # OCR-aware text extraction

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ZIPS_DIR = DATA / "zips"
CACHE_BASE = DATA / "cache" / "exemptions"
INDEX = ROOT / "docs" / "data" / "index.json"

MARKER_RE = re.compile(r"\(\s*b\s*\)\s*\(\s*(\d+)\s*\)(?:\s*\(\s*([A-F])\s*\))?")
OCR_TEXT_THRESHOLD = 30


def normalize_marker(num: str, subpart: str | None) -> str:
    return f"(b)({num})" + (f"({subpart})" if subpart else "")


def scan_pdf_bytes(pdf_bytes: bytes, filename: str) -> dict:
    """Scan PDF bytes for exemption markers; return per-page record.

    Uses the OCR cache via get_page_text() so image-only pages still
    contribute when they've been processed by extract_ocr_text.py.
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

    total_pages = doc.page_count
    exemption_pages: list[dict] = []
    ocr_candidate_pages: list[int] = []

    try:
        for page_num in range(total_pages):
            page = doc.load_page(page_num)
            text = get_page_text(filename, page, page_num + 1,
                                 ocr_threshold=OCR_TEXT_THRESHOLD)

            # Still flag as ocr_candidate if the page yielded too little
            # text — even after consulting OCR cache. That way a future
            # OCR re-run can target only the pages still missing.
            if len(text.strip()) < OCR_TEXT_THRESHOLD:
                ocr_candidate_pages.append(page_num + 1)

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


def main(module: str, rebuild: bool = False) -> None:
    if not INDEX.exists():
        sys.exit(f"{INDEX} missing — run scripts/build_index.py first")

    if not re.fullmatch(r"M[1-5]", module):
        sys.exit(f"Invalid module '{module}' — expected M1..M5")

    index = json.loads(INDEX.read_text())
    pdfs_in_mod = [
        r for r in index
        if r.get("module") == module
        and r.get("extension") == "pdf"
    ]
    with_zip = [r for r in pdfs_in_mod if r.get("zip_source")]
    without_zip = [r for r in pdfs_in_mod if not r.get("zip_source")]
    print(f"{len(pdfs_in_mod)} {module} PDFs in index "
          f"({len(with_zip)} in ZIPs, {len(without_zip)} individual-only)")

    cache_dir = CACHE_BASE / module
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Group by ZIP so each archive opens once
    by_zip: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in with_zip:
        by_zip[(r["batch_code"], r["zip_source"])].append(r)
    print(f"Across {len(by_zip)} ZIP bundles")
    print()

    results: list[dict] = []
    by_marker: Counter[str] = Counter()
    files_with_markers = 0
    files_ocr_flagged = 0
    files_errored: list[str] = []
    files_skipped_no_zip: list[str] = []
    files_zip_missing = 0

    for r in without_zip:
        files_skipped_no_zip.append(r["filename"])
        results.append({
            "filename": r["filename"],
            "id": r["id"],
            "batch_code": r.get("batch_code"),
            "company": r.get("company"),
            "license": r.get("license"),
            "module": module,
            "skipped": "no zip_source (individual-only file)",
        })

    # Process each ZIP once
    pbar = tqdm(total=sum(len(rs) for rs in by_zip.values()), desc=module, unit="pdf")
    for (batch_code, zip_name), rows in by_zip.items():
        zip_path = ZIPS_DIR / batch_code / zip_name
        if not zip_path.exists():
            print(f"⚠️  ZIP missing, skipping {len(rows)} files: {zip_path}")
            files_zip_missing += len(rows)
            pbar.update(len(rows))
            continue

        # Lazy-open the ZIP only if any file in it needs processing
        needs_open = any(
            rebuild or not (cache_dir / f"{r['filename']}.json").exists()
            for r in rows
        )

        zf = None
        try:
            if needs_open:
                zf = zipfile.ZipFile(zip_path)
                by_basename: dict[str, zipfile.ZipInfo] = {}
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    base = info.filename.rsplit("/", 1)[-1]
                    by_basename[base] = info

            for r in rows:
                fname = r["filename"]
                cache_path = cache_dir / f"{fname}.json"

                if cache_path.exists() and not rebuild:
                    scan = json.loads(cache_path.read_text())
                else:
                    info = by_basename.get(fname) if zf else None
                    if info is None:
                        scan = {"error": "member not found in ZIP"}
                    else:
                        with zf.open(info) as f:
                            pdf_bytes = f.read()
                        scan = scan_pdf_bytes(pdf_bytes, fname)
                    cache_path.write_text(json.dumps(scan, indent=2))

                if "error" in scan:
                    files_errored.append(fname)
                    results.append({
                        "filename": fname,
                        "id": r["id"],
                        "batch_code": r.get("batch_code"),
                        "company": r.get("company"),
                        "license": r.get("license"),
                        "module": module,
                        "error": scan["error"],
                    })
                else:
                    if scan["exemption_pages"]:
                        files_with_markers += 1
                    if scan["ocr_candidate_pages"]:
                        files_ocr_flagged += 1
                    for entry in scan["exemption_pages"]:
                        by_marker[entry["marker"]] += entry["count"]

                    results.append({
                        "filename": fname,
                        "id": r["id"],
                        "batch_code": r.get("batch_code"),
                        "company": r.get("company"),
                        "license": r.get("license"),
                        "module": module,
                        "total_pages": scan["total_pages"],
                        "exemption_pages": scan["exemption_pages"],
                        "ocr_candidate_pages": scan["ocr_candidate_pages"],
                    })
                pbar.update(1)
        finally:
            if zf is not None:
                zf.close()
    pbar.close()

    total_occurrences = sum(by_marker.values())
    summary = {
        "module": module,
        "files_in_index": len(pdfs_in_mod),
        "files_processed": len(pdfs_in_mod) - len(files_skipped_no_zip),
        "files_with_exemptions": files_with_markers,
        "files_ocr_flagged": files_ocr_flagged,
        "files_errored": files_errored,
        "files_zip_missing": files_zip_missing,
        "files_skipped_no_zip": files_skipped_no_zip,
        "total_marker_occurrences": total_occurrences,
        "by_exemption": dict(sorted(by_marker.items(), key=lambda kv: -kv[1])),
    }

    out_path = DATA / f"{module}_exemptions.json"
    out_path.write_text(json.dumps({"summary": summary, "files": results}, indent=2))

    print()
    print("=" * 60)
    print(f"{module} EXEMPTION SCAN COMPLETE")
    print("=" * 60)
    print(f"Files in index:        {len(pdfs_in_mod)}")
    print(f"Files processed:       {summary['files_processed']}")
    print(f"Files with markers:    {files_with_markers}")
    print(f"Files OCR-flagged:     {files_ocr_flagged}")
    print(f"Files errored:         {len(files_errored)}")
    print(f"Files w/o zip_source:  {len(files_skipped_no_zip)}")
    print(f"Files missing ZIP:     {files_zip_missing}")
    print(f"Total marker occurrences: {total_occurrences}")
    print()
    print("By exemption (top 10):")
    for marker, count in list(summary["by_exemption"].items())[:10]:
        print(f"  {marker:>14}  {count:>6}")
    print()
    print(f"Wrote: {out_path}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("module", help="Module to scan: M1, M2, M3, M4, or M5")
    p.add_argument("--rebuild", action="store_true", help="ignore per-file cache")
    args = p.parse_args()
    main(args.module.upper(), rebuild=args.rebuild)
