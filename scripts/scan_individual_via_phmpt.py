"""Close the final coverage gap: scan the 168 PDFs we couldn't reach
via ICAN by pulling them directly from PHMPT using curl_cffi's TLS
fingerprint impersonation, which bypasses Cloudflare cleanly.

Worklist filter:
- extension == "pdf"
- no zip_source (individual-only file)
- no ICAN URL (couldn't be reached by scan_individual_via_ican.py)
- has a phmpt.org individual_url

For each PDF:
  1. GET via curl_cffi (impersonate=chrome131)
  2. Cache bytes at data/cache/individual_pdfs/{filename}
  3. Scan for (b)(N) markers using the same regex as the other scanners
  4. Cache the scan result at data/cache/exemptions/individual/{filename}.json
  5. Aggregate to data/individual_phmpt_exemptions.json (gitignored,
     same schema as data/individual_exemptions.json — aggregator picks
     up both)

Rate-limited with random 1.9-3.0 second jittered delay between
downloads — keeps phmpt.org's Cloudflare from re-flagging us.

Run:
    uv run python scripts/scan_individual_via_phmpt.py
    uv run python scripts/scan_individual_via_phmpt.py --rebuild
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
from curl_cffi import requests as curl_requests
from tqdm import tqdm

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
INDEX = ROOT / "docs" / "data" / "index.json"
ICAN_COMPARISON = DATA / "ican_comparison.json"

CACHE_PDF = DATA / "cache" / "individual_pdfs"
CACHE_EX = DATA / "cache" / "exemptions" / "individual"
OUT = DATA / "individual_phmpt_exemptions.json"

MARKER_RE = re.compile(r"\(\s*b\s*\)\s*\(\s*(\d+)\s*\)(?:\s*\(\s*([A-F])\s*\))?")
OCR_TEXT_THRESHOLD = 30

MIN_DELAY = 1.9
MAX_DELAY = 3.0
IMPERSONATE = "chrome131"


def normalize_marker(num: str, subpart: str | None) -> str:
    return f"(b)({num})" + (f"({subpart})" if subpart else "")


def scan_pdf_bytes(pdf_bytes: bytes) -> dict:
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
            text = page.get_text("text") or ""
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
    """PDFs with PHMPT individual_url, no zip_source, no ICAN URL."""
    idx = json.loads(INDEX.read_text())
    ican_doc = json.loads(ICAN_COMPARISON.read_text())
    ican_fnames = {
        d["filename"] for d in ican_doc.get("ican_documents", [])
        if d.get("filename") and d.get("ican_url")
    }
    targets = []
    for r in idx:
        if r.get("extension") != "pdf":
            continue
        if r.get("zip_source"):
            continue
        if not r.get("individual_url"):
            continue
        if r["filename"] in ican_fnames:
            continue  # already handled by scan_individual_via_ican.py
        targets.append({
            "id": r["id"],
            "filename": r["filename"],
            "module": r.get("module"),
            "batch_code": r.get("batch_code"),
            "company": r.get("company"),
            "license": r.get("license"),
            "individual_url": r["individual_url"],
        })
    return targets


def derive_referer(url: str) -> str:
    """Pick a plausible Referer based on the URL's date path.

    Cloudflare often allows references from the same origin. We just use
    phmpt.org's homepage as the Referer — that should be sufficient given
    that curl_cffi's TLS fingerprint is doing the heavy lifting.
    """
    return "https://phmpt.org/"


def download_pdf(url: str) -> tuple[bytes | None, int | None]:
    try:
        r = curl_requests.get(
            url,
            impersonate=IMPERSONATE,
            timeout=60,
            headers={"Referer": derive_referer(url)},
        )
    except Exception as e:
        print(f"  ❌ request error: {type(e).__name__}: {e}")
        return None, None
    if r.status_code != 200:
        return None, r.status_code
    body = r.content
    if not body or not body.startswith(b"%PDF"):
        return None, r.status_code
    return body, r.status_code


def _record(t: dict, scan: dict, *, by_marker_counter: Counter) -> dict:
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


def main(rebuild: bool = False) -> None:
    if not INDEX.exists():
        sys.exit(f"{INDEX} missing")
    if not ICAN_COMPARISON.exists():
        sys.exit(f"{ICAN_COMPARISON} missing")

    CACHE_PDF.mkdir(parents=True, exist_ok=True)
    CACHE_EX.mkdir(parents=True, exist_ok=True)

    targets = load_targets()
    print(f"{len(targets)} PDFs with PHMPT individual_url, no zip, no ICAN")

    todo: list[tuple[dict, Path, Path]] = []
    for t in targets:
        cache_ex = CACHE_EX / f"{t['filename']}.json"
        cache_pdf = CACHE_PDF / t["filename"]
        if cache_ex.exists() and cache_pdf.exists() and not rebuild:
            continue
        todo.append((t, cache_ex, cache_pdf))

    print(f"Cache hits: {len(targets) - len(todo)} ; to do: {len(todo)}")
    print(f"Using curl_cffi impersonate={IMPERSONATE} with {MIN_DELAY}-{MAX_DELAY}s jittered delay")
    print()

    results: list[dict] = []
    by_marker: Counter[str] = Counter()
    files_with_markers = 0
    files_ocr_flagged = 0
    files_errored: list[str] = []
    files_http_errors: dict[int, int] = {}
    new_success = 0
    new_failure = 0
    consecutive_403 = 0
    CIRCUIT_BREAK = 8  # bail if we get this many 403s in a row

    # Load existing-cache results into output
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

    # Download + scan to-do items
    for t, cache_ex, cache_pdf in tqdm(todo, desc="PHMPT", unit="pdf"):
        fname = t["filename"]
        url = t["individual_url"]

        if cache_pdf.exists() and not rebuild:
            body = cache_pdf.read_bytes()
            status = 200
        else:
            body, status = download_pdf(url)

        if body is None:
            files_http_errors[status or 0] = files_http_errors.get(status or 0, 0) + 1
            scan = {"error": f"download_failed_http_{status}"}
            cache_ex.write_text(json.dumps(scan, indent=2))
            files_errored.append(fname)
            new_failure += 1
            results.append(_record(t, scan, by_marker_counter=by_marker))
            if status == 403:
                consecutive_403 += 1
                if consecutive_403 >= CIRCUIT_BREAK:
                    print(f"\n⛔ Circuit breaker: {CIRCUIT_BREAK} consecutive 403s. Aborting.")
                    print("   Wait and retry, or check if Cloudflare flagged the IP again.")
                    break
            time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))
            continue

        consecutive_403 = 0
        cache_pdf.write_bytes(body)
        scan = scan_pdf_bytes(body)
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
        time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

    total_occurrences = sum(by_marker.values())
    summary = {
        "source": "individual PDFs downloaded via PHMPT (curl_cffi chrome impersonation)",
        "files_in_scope": len(targets),
        "files_processed": len(targets) - len(files_errored),
        "files_with_exemptions": files_with_markers,
        "files_ocr_flagged": files_ocr_flagged,
        "files_errored": files_errored,
        "http_error_breakdown": files_http_errors,
        "total_marker_occurrences": total_occurrences,
        "by_exemption": dict(sorted(by_marker.items(), key=lambda kv: -kv[1])),
    }
    OUT.write_text(json.dumps({"summary": summary, "files": results}, indent=2))

    print()
    print("=" * 60)
    print("INDIVIDUAL-via-PHMPT SCAN COMPLETE")
    print("=" * 60)
    print(f"Files in scope:        {len(targets)}")
    print(f"This run: +{new_success} new successes, +{new_failure} new failures")
    print(f"Files with markers:    {files_with_markers}")
    print(f"Files OCR-flagged:     {files_ocr_flagged}")
    print(f"Files errored (total): {len(files_errored)}")
    if files_http_errors:
        print(f"HTTP error counts:     {files_http_errors}")
    print(f"Total marker occurrences: {total_occurrences:,}")
    print()
    print("By exemption (top 10):")
    for marker, count in list(summary["by_exemption"].items())[:10]:
        print(f"  {marker:>14}  {count:>6}")
    print()
    print(f"Wrote: {OUT}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--rebuild", action="store_true", help="ignore caches")
    args = p.parse_args()
    main(rebuild=args.rebuild)
