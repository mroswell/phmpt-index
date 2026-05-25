"""Scan PDFs we couldn't reach via PHMPT by downloading from ICAN instead.

Closes most of the 400-file gap left by extract_module_exemptions.py
(which skips files without zip_source because phmpt.org/wp-content is
Cloudflare-blocked). ICAN re-hosts ~232 of those 400 PDFs on its own
CDN with no Cloudflare interception — we can pull them directly.

For each target PDF:
  1. GET from ICAN URL via httpx
  2. Save bytes to data/cache/individual_pdfs/{filename}.pdf so the
     pharmacovigilance scanner (and any other future scanner) can
     re-read without re-downloading
  3. Scan for (b)(N) exemption markers with the same regex used in
     scripts/extract_module_exemptions.py
  4. Cache per-file JSON at data/cache/exemptions/individual/
  5. Aggregate to data/individual_exemptions.json

Run:
    uv run python scripts/scan_individual_via_ican.py
    uv run python scripts/scan_individual_via_ican.py --rebuild
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from collections import Counter
from pathlib import Path

import fitz  # PyMuPDF
import httpx
from tqdm import tqdm

from _pdf_text import get_page_text  # OCR-aware text extraction

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
INDEX = ROOT / "docs" / "data" / "index.json"
ICAN_COMPARISON = DATA / "ican_comparison.json"

CACHE_PDF = DATA / "cache" / "individual_pdfs"
CACHE_EX = DATA / "cache" / "exemptions" / "individual"
OUT = DATA / "individual_exemptions.json"

MARKER_RE = re.compile(r"\(\s*b\s*\)\s*\(\s*(\d+)\s*\)(?:\s*\(\s*([A-F])\s*\))?")
OCR_TEXT_THRESHOLD = 30

MIN_DELAY = 1.9  # seconds
MAX_DELAY = 3.0
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)


def normalize_marker(num: str, subpart: str | None) -> str:
    return f"(b)({num})" + (f"({subpart})" if subpart else "")


def scan_pdf_bytes(pdf_bytes: bytes, filename: str) -> dict:
    """Open PDF bytes; return per-page exemption record."""
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


def load_targets() -> list[dict]:
    """Find PDFs without zip_source that have an ICAN URL."""
    idx = json.loads(INDEX.read_text())
    ican_doc = json.loads(ICAN_COMPARISON.read_text())

    ican_url_by_fname: dict[str, str] = {}
    for d in ican_doc.get("ican_documents", []):
        fname = d.get("filename")
        url = d.get("ican_url")
        if fname and url and fname not in ican_url_by_fname:
            ican_url_by_fname[fname] = url

    targets: list[dict] = []
    for r in idx:
        if r.get("extension") != "pdf":
            continue
        if r.get("zip_source"):
            continue
        ican_url = ican_url_by_fname.get(r.get("filename"))
        if not ican_url:
            continue
        targets.append({
            "id": r["id"],
            "filename": r["filename"],
            "module": r.get("module"),
            "batch_code": r.get("batch_code"),
            "company": r.get("company"),
            "license": r.get("license"),
            "individual_url": r.get("individual_url"),
            "ican_url": ican_url,
        })
    return targets


def download_pdf(client: httpx.Client, url: str) -> bytes | None:
    """Download PDF; return bytes or None on failure."""
    try:
        r = client.get(url, timeout=60.0)
    except Exception as e:
        print(f"  ❌ request error: {type(e).__name__}: {e}")
        return None
    if r.status_code != 200:
        print(f"  ❌ HTTP {r.status_code}")
        return None
    body = r.content
    if not body or not body.startswith(b"%PDF"):
        print(f"  ❌ response is not a PDF ({len(body)} bytes, starts with {body[:8]!r})")
        return None
    return body


def main(rebuild: bool = False) -> None:
    if not INDEX.exists():
        sys.exit(f"{INDEX} missing — run scripts/build_index.py first")
    if not ICAN_COMPARISON.exists():
        sys.exit(f"{ICAN_COMPARISON} missing")

    CACHE_PDF.mkdir(parents=True, exist_ok=True)
    CACHE_EX.mkdir(parents=True, exist_ok=True)

    targets = load_targets()
    print(f"{len(targets)} individual PDFs with ICAN URLs to process")

    todo: list[tuple[dict, Path, Path]] = []
    for t in targets:
        cache_ex = CACHE_EX / f"{t['filename']}.json"
        cache_pdf = CACHE_PDF / t["filename"]
        if cache_ex.exists() and cache_pdf.exists() and not rebuild:
            continue
        todo.append((t, cache_ex, cache_pdf))

    print(f"Cache hits: {len(targets) - len(todo)} ; to do: {len(todo)}")
    print()

    results: list[dict] = []
    by_marker: Counter[str] = Counter()
    files_with_markers = 0
    files_ocr_flagged = 0
    files_errored: list[str] = []
    new_success = 0
    new_failure = 0

    # First pass: load existing cache for all targets so the output is full
    for t in targets:
        cache_ex = CACHE_EX / f"{t['filename']}.json"
        if cache_ex.exists() and not rebuild:
            scan = json.loads(cache_ex.read_text())
            results.append(_record(t, scan, by_marker_counter=by_marker))
            if "error" in scan:
                files_errored.append(t["filename"])
            else:
                if scan["exemption_pages"]:
                    files_with_markers += 1
                if scan["ocr_candidate_pages"]:
                    files_ocr_flagged += 1

    # Second pass: download + scan the to-do list
    if todo:
        with httpx.Client(
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        ) as client:
            for t, cache_ex, cache_pdf in tqdm(todo, desc="ICAN", unit="pdf"):
                fname = t["filename"]
                url = t["ican_url"]

                # If the PDF bytes are already cached but the exemption JSON
                # isn't, skip the download and just scan from disk
                if cache_pdf.exists() and not rebuild:
                    body = cache_pdf.read_bytes()
                else:
                    body = download_pdf(client, url)
                    if body is None:
                        scan = {"error": "download_failed"}
                        cache_ex.write_text(json.dumps(scan, indent=2))
                        files_errored.append(fname)
                        new_failure += 1
                        results.append(_record(t, scan, by_marker_counter=by_marker))
                        time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))
                        continue
                    cache_pdf.write_bytes(body)

                scan = scan_pdf_bytes(body, fname)
                cache_ex.write_text(json.dumps(scan, indent=2))

                if "error" in scan:
                    files_errored.append(fname)
                    new_failure += 1
                else:
                    new_success += 1
                    if scan["exemption_pages"]:
                        files_with_markers += 1
                    if scan["ocr_candidate_pages"]:
                        files_ocr_flagged += 1

                results.append(_record(t, scan, by_marker_counter=by_marker))

                # jittered delay before next download
                time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

    # Write aggregated file
    total_occurrences = sum(by_marker.values())
    summary = {
        "source": "individual PDFs downloaded via ICAN",
        "files_in_scope": len(targets),
        "files_processed": len(targets) - len(files_errored),
        "files_with_exemptions": files_with_markers,
        "files_ocr_flagged": files_ocr_flagged,
        "files_errored": files_errored,
        "total_marker_occurrences": total_occurrences,
        "by_exemption": dict(sorted(by_marker.items(), key=lambda kv: -kv[1])),
    }
    OUT.write_text(json.dumps({"summary": summary, "files": results}, indent=2))

    print()
    print("=" * 60)
    print("INDIVIDUAL-via-ICAN SCAN COMPLETE")
    print("=" * 60)
    print(f"Files in scope:        {len(targets)}")
    print(f"This run: +{new_success} new successes, +{new_failure} new failures")
    print(f"Files with markers:    {files_with_markers}")
    print(f"Files OCR-flagged:     {files_ocr_flagged}")
    print(f"Files errored (total): {len(files_errored)}")
    print(f"Total marker occurrences: {total_occurrences:,}")
    print()
    print("By exemption (top 10):")
    for marker, count in list(summary["by_exemption"].items())[:10]:
        print(f"  {marker:>14}  {count:>6}")
    print()
    print(f"Wrote: {OUT}")
    print(f"PDF bytes cached at: {CACHE_PDF}/")


def _record(t: dict, scan: dict, *, by_marker_counter: Counter) -> dict:
    """Convert one (target, scan) pair into the per-file output record."""
    base = {
        "filename": t["filename"],
        "id": t["id"],
        "batch_code": t.get("batch_code"),
        "company": t.get("company"),
        "license": t.get("license"),
        "module": t.get("module"),
    }
    if "error" in scan:
        base["error"] = scan["error"]
        return base
    base["total_pages"] = scan["total_pages"]
    base["exemption_pages"] = scan["exemption_pages"]
    base["ocr_candidate_pages"] = scan["ocr_candidate_pages"]
    for entry in scan["exemption_pages"]:
        by_marker_counter[entry["marker"]] += entry["count"]
    return base


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--rebuild", action="store_true", help="ignore caches and re-download")
    args = p.parse_args()
    main(rebuild=args.rebuild)
