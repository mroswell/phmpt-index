"""Headful variant of crawl_files.py.

Cloudflare re-challenges headless Chromium, so this drives a visible window with
the same persistent profile that passed bootstrap. Collects every individual-file
URL from the 5 phmpt.org product pages and writes data/individual_urls.json +
data/orphans.json (same outputs/format as crawl_files.py).
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / ".profile"
DATA = ROOT / "data"
TOC = DATA / "toc.json"
OUT_URLS = DATA / "individual_urls.json"
OUT_ORPHANS = DATA / "orphans.json"
BASE = "https://phmpt.org"

PRODUCT_SLUGS = [
    "pfizer-16-plus-documents",
    "moderna-documents",
    "pfizer-12-15-documents",
    "pfizer-court-documents",
    "pfizer-12-15-and-moderna-court-documents",
]


def parse_total(info_text: str):
    m = re.search(r"([\d,]+)", info_text)
    return int(m.group(1).replace(",", "")) if m else None


def basename_of_url(href: str) -> str:
    path = urllib.parse.urlparse(href).path
    return urllib.parse.unquote(path.rsplit("/", 1)[-1])


def crawl_page(page, slug: str):
    url = f"{BASE}/{slug}/"
    print(f"\n--- {slug} ---")
    last = None
    for attempt in range(5):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            last = None
            break
        except Exception as e:
            last = e
            print(f"  goto attempt {attempt+1} failed: {type(e).__name__}; retrying")
            time.sleep(3)
    if last:
        raise last
    # wait through any challenge for the table to appear
    ok = False
    for _ in range(45):
        try:
            if page.locator("table.posts-data-table tbody tr").count() > 0:
                ok = True
                break
        except Exception:
            pass
        page.wait_for_timeout(1000)
    if not ok:
        page.wait_for_selector("table.posts-data-table tbody tr", timeout=30000)

    info_text = page.locator(".dataTables_info").first.inner_text()
    total = parse_total(info_text)
    print(f"  info: {info_text!r}  -> total {total}")
    page.evaluate(
        "() => { const s=document.querySelector('.dataTables_length select');"
        " window.jQuery(s).val('-1').trigger('change'); }"
    )
    if total:
        page.wait_for_function(
            "expected => document.querySelectorAll('table.posts-data-table tbody tr').length >= expected",
            arg=total, timeout=45000,
        )
    rows = page.eval_on_selector_all(
        "table.posts-data-table tbody tr a.dlp-download-link",
        "links => links.map(a => a.href)",
    )
    pairs = [(basename_of_url(u), u) for u in rows]
    print(f"  collected: {len(pairs)} download links")
    if total and len(pairs) != total:
        print(f"  WARN: expected {total} but got {len(pairs)}")
    return total, pairs


def main() -> None:
    individual: dict[str, str] = {}
    page_of: dict[str, str] = {}
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE), headless=False, viewport={"width": 1400, "height": 1000})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(60000)
        for slug in PRODUCT_SLUGS:
            try:
                _, pairs = crawl_page(page, slug)
            except Exception as e:
                print(f"  FAIL on {slug}: {e}")
                continue
            for base, url in pairs:
                if base in individual:
                    continue
                individual[base] = url
                page_of[base] = slug
        ctx.close()

    OUT_URLS.write_text(json.dumps(individual, indent=2, sort_keys=True), encoding="utf-8")
    # Orphans = phmpt files NOT present in any zip bundle (same definition as
    # crawl_files.py). Filtering to zip-absent basenames is essential — writing
    # ALL individual URLs here bloats build_index with thousands of bogus rows.
    toc = json.loads(TOC.read_text()) if TOC.exists() else []
    zip_basenames = {row["member_name"].rsplit("/", 1)[-1] for row in toc}
    orphans = [{"filename": b, "url": individual[b], "product_page": "/" + page_of[b] + "/"}
               for b in sorted(individual) if b not in zip_basenames]
    OUT_ORPHANS.write_text(json.dumps(orphans, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT_URLS} ({len(individual):,} individual URLs)")
    print(f"wrote {OUT_ORPHANS} ({len(orphans):,} phmpt-only orphans)")


if __name__ == "__main__":
    main()
