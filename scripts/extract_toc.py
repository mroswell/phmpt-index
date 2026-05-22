"""Build the per-member TOC from the downloaded zips.

For each zip in data/zips/, read its central directory to enumerate
members (no extraction). For each PDF member, stream the bytes from the
zip into PyMuPDF and grab the page count — still no disk extraction.

Per-zip results are cached to data/cache/<zip>.json so reruns skip work
that's already been done. Cross-zip work is parallelized with a process
pool.

Final output: data/toc.json — flat list across every zip.

Usage:
    uv run python scripts/extract_toc.py            # all zips
    uv run python scripts/extract_toc.py --limit 1  # smoke test on one
    uv run python scripts/extract_toc.py --rebuild  # ignore cache
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import zipfile_deflate64 as zipfile  # drop-in stdlib zipfile + Deflate64 support
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import fitz  # PyMuPDF
from tqdm import tqdm

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ZIPS_DIR = DATA / "zips"
CACHE = DATA / "cache"
LISTING = DATA / "zips.json"
OUT = DATA / "toc.json"

# Skip page count for PDFs larger than this many bytes uncompressed.
# Reading huge PDFs into memory just to count pages isn't worth it.
PAGE_COUNT_MAX_BYTES = 2 * 1024**3  # 2 GiB


def process_zip(args: tuple[Path, str, str | None]) -> dict:
    """Worker: read one zip's TOC. Returns dict with rows + counters.

    args = (zip_path, zip_url, batch_code)
    """
    zip_path, zip_url, batch_code = args
    rows: list[dict] = []
    pdfs_counted = 0
    pdfs_skipped = 0
    pdf_errors = 0

    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            try:
                modified = datetime(*info.date_time).isoformat()
            except (TypeError, ValueError):
                modified = None

            page_count = None
            name_lower = info.filename.lower()
            if name_lower.endswith(".pdf"):
                if info.file_size > PAGE_COUNT_MAX_BYTES:
                    pdfs_skipped += 1
                else:
                    try:
                        with zf.open(info) as f:
                            pdf_bytes = f.read()
                        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                        page_count = doc.page_count
                        doc.close()
                        pdfs_counted += 1
                    except Exception:
                        pdf_errors += 1
                        page_count = None

            rows.append(
                {
                    "member_name":       info.filename,
                    "uncompressed_size": info.file_size,
                    "compressed_size":   info.compress_size,
                    "modified":          modified,
                    "page_count":        page_count,
                    "zip_source":        zip_path.name,
                    "zip_url":           zip_url,
                    "batch_code":        batch_code,
                }
            )

    return {
        "zip_name": zip_path.name,
        "rows": rows,
        "pdfs_counted": pdfs_counted,
        "pdfs_skipped": pdfs_skipped,
        "pdf_errors": pdf_errors,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="process only the first N zips")
    ap.add_argument("--rebuild", action="store_true", help="ignore cache and re-read every zip")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 1))
    args = ap.parse_args()

    if not LISTING.exists():
        print(f"error: {LISTING} not found — run crawl_listing.py first")
        sys.exit(1)
    listing = json.loads(LISTING.read_text())
    url_by_name = {row["filename"]: row["url"] for row in listing}
    code_by_name = {row["filename"]: row["batch_code"] for row in listing}

    zip_paths: list[Path] = []
    for code_dir in sorted(ZIPS_DIR.iterdir()) if ZIPS_DIR.exists() else []:
        if not code_dir.is_dir():
            continue
        zip_paths.extend(sorted(code_dir.glob("*.zip")))
    if args.limit:
        zip_paths = zip_paths[: args.limit]
    if not zip_paths:
        print("error: no zips found under data/zips/ — run download_zips.py first")
        sys.exit(1)
    print(f"found {len(zip_paths)} zips on disk")

    CACHE.mkdir(parents=True, exist_ok=True)
    pending: list[tuple[Path, str, str | None]] = []
    cached_results: list[dict] = []
    for zp in zip_paths:
        cache_path = CACHE / (zp.name + ".json")
        if cache_path.exists() and not args.rebuild:
            try:
                cached_results.append(json.loads(cache_path.read_text()))
                continue
            except json.JSONDecodeError:
                pass  # bad cache, redo it
        pending.append((zp, url_by_name.get(zp.name, ""), code_by_name.get(zp.name)))

    print(f"cache hit: {len(cached_results)};  to process: {len(pending)}")

    results: list[dict] = list(cached_results)
    if pending:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(process_zip, t): t[0].name for t in pending}
            with tqdm(total=len(futures), desc="zips") as bar:
                for fut in as_completed(futures):
                    zname = futures[fut]
                    try:
                        res = fut.result()
                    except Exception as e:
                        bar.write(f"FAIL {zname}: {e}")
                        bar.update(1)
                        continue
                    (CACHE / (zname + ".json")).write_text(json.dumps(res))
                    results.append(res)
                    bar.write(
                        f"  {zname}: {len(res['rows']):>6} members, "
                        f"{res['pdfs_counted']} PDF pages counted, "
                        f"{res['pdfs_skipped']} too big, {res['pdf_errors']} errors"
                    )
                    bar.update(1)

    # Flatten + write the master TOC.
    all_rows: list[dict] = []
    for res in results:
        all_rows.extend(res["rows"])

    OUT.write_text(json.dumps(all_rows, indent=2))
    total_members = len(all_rows)
    total_pages = sum(r["page_count"] or 0 for r in all_rows)
    pdf_rows = sum(1 for r in all_rows if r["member_name"].lower().endswith(".pdf"))
    counted = sum(1 for r in all_rows if r["page_count"] is not None)
    print()
    print(f"wrote {total_members:,} member rows to {OUT}")
    print(f"  PDFs:                {pdf_rows:,}")
    print(f"  PDFs with page count:{counted:,}")
    print(f"  total pages:         {total_pages:,}")


if __name__ == "__main__":
    main()
