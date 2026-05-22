"""Crawl phmpt.org's per-product pages to collect every individual-file URL.

For each product page (5 total), expand the DataTables widget to "All",
collect every `<a>` whose href points under /wp-content/uploads/, and
key by the URL's basename.

Outputs:
  data/individual_urls.json   { filename: url }
  data/orphans.json           rows for filenames present on phmpt.org
                              but not in data/toc.json
And prints a console summary contrasting phmpt vs. our zip inventory.

phmpt.org sits behind Cloudflare, so we drive a real Chromium with the
profile from bootstrap.py. The actual file URLs are public and
unprotected.
"""

from __future__ import annotations

import json
import re
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


def parse_total(info_text: str) -> int | None:
    """`.dataTables_info` reads e.g. '2,369 documents' — strip the comma."""
    m = re.search(r"([\d,]+)", info_text)
    return int(m.group(1).replace(",", "")) if m else None


def basename_of_url(href: str) -> str:
    """URL basename, %-decoded. The path's last segment is the filename."""
    path = urllib.parse.urlparse(href).path
    return urllib.parse.unquote(path.rsplit("/", 1)[-1])


def crawl_page(page, slug: str) -> tuple[int | None, list[tuple[str, str]]]:
    """Returns (expected_total, [(basename, url), ...]) for one product page."""
    url = f"{BASE}/{slug}/"
    print(f"\n--- {slug} ---")
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    page.wait_for_selector("table.posts-data-table tbody tr", timeout=20000)

    info_text = page.locator(".dataTables_info").first.inner_text()
    total = parse_total(info_text)
    print(f"  info: {info_text!r}  → total {total}")

    # Ask DataTables to render every row at once.
    page.evaluate(
        """() => {
          const sel = document.querySelector('.dataTables_length select');
          if (!sel) throw new Error('no length select found');
          window.jQuery(sel).val('-1').trigger('change');
        }"""
    )
    if total:
        page.wait_for_function(
            "expected => document.querySelectorAll('table.posts-data-table tbody tr').length >= expected",
            arg=total,
            timeout=30000,
        )

    rows = page.eval_on_selector_all(
        "table.posts-data-table tbody tr a.dlp-download-link",
        "links => links.map(a => a.href)",
    )
    pairs = [(basename_of_url(u), u) for u in rows]
    print(f"  collected: {len(pairs)} download links")
    if total and len(pairs) != total:
        print(f"  WARN: expected {total} but got {len(pairs)} links — investigate")
    return total, pairs


def main() -> None:
    if not TOC.exists():
        raise SystemExit(f"missing {TOC} — run extract_toc.py first")
    toc = json.loads(TOC.read_text())
    zip_basenames = {row["member_name"].rsplit("/", 1)[-1] for row in toc}
    print(f"loaded {len(toc):,} zip-member rows  ({len(zip_basenames):,} distinct basenames)")

    DATA.mkdir(exist_ok=True)

    individual: dict[str, str] = {}
    page_of: dict[str, str] = {}
    duplicates: list[tuple[str, str, str]] = []  # (basename, kept_page, dupe_page)
    expected_totals: dict[str, int | None] = {}

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            headless=False,
            viewport={"width": 1400, "height": 1000},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(60000)
        for slug in PRODUCT_SLUGS:
            try:
                total, pairs = crawl_page(page, slug)
            except Exception as e:
                print(f"  FAIL on {slug}: {e}")
                continue
            expected_totals[slug] = total
            for basename, url in pairs:
                if basename in individual:
                    duplicates.append((basename, page_of[basename], slug))
                    continue
                individual[basename] = url
                page_of[basename] = slug
        ctx.close()

    OUT_URLS.write_text(json.dumps(individual, indent=2, sort_keys=True), encoding="utf-8")

    matched = sum(1 for b in individual if b in zip_basenames)
    phmpt_only = [b for b in individual if b not in zip_basenames]
    zip_only_count = sum(1 for b in zip_basenames if b not in individual)

    orphans = [
        {"filename": b, "url": individual[b], "product_page": "/" + page_of[b] + "/"}
        for b in sorted(phmpt_only)
    ]
    OUT_ORPHANS.write_text(json.dumps(orphans, indent=2), encoding="utf-8")

    print()
    print("=" * 60)
    print(f"wrote {OUT_URLS}   ({len(individual):,} URLs)")
    print(f"wrote {OUT_ORPHANS} ({len(orphans):,} orphans)")
    print()
    print(f"phmpt total:           {len(individual):,}")
    print(f"zip-member basenames:  {len(zip_basenames):,}")
    print(f"matched (both sides):  {matched:,}")
    print(f"phmpt-only (orphans):  {len(phmpt_only):,}")
    print(f"zip-only (no phmpt):   {zip_only_count:,}")
    if duplicates:
        print()
        print(f"duplicates across product pages: {len(duplicates)}  (first 5):")
        for b, kept, dupe in duplicates[:5]:
            print(f"  {b}  kept={kept}  dupe={dupe}")
    print()
    print("per-page expected vs. captured:")
    for slug, total in expected_totals.items():
        captured = sum(1 for b, p in page_of.items() if p == slug)
        marker = "" if total is None or captured == total else "  ← MISMATCH"
        print(f"  {slug}: total={total} captured={captured}{marker}")


if __name__ == "__main__":
    main()
