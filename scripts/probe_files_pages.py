"""Discovery probe for phmpt.org's per-product individual-file pages.

Loads each of the 5 product pages once via the persistent profile and
dumps:
  - .scratch/<slug>.html        the rendered HTML
  - .scratch/<slug>_links.json  every <a href> found inside rows
  - console: total row count from .dataTables_info, plus a sample row

Used once before writing scripts/crawl_files.py so the crawler can be
written against the actual DOM.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / ".profile"
SCRATCH = ROOT / ".scratch"
BASE = "https://phmpt.org"

SLUGS = [
    "pfizer-16-plus-documents",
    "moderna-documents",
    "pfizer-12-15-documents",
    "pfizer-court-documents",
    "pfizer-12-15-and-moderna-court-documents",
]


def probe(page, slug: str) -> dict:
    url = f"{BASE}/{slug}/"
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    try:
        page.wait_for_selector(
            "table.posts-data-table tbody tr, table tbody tr, a[href*='.pdf'], a[href*='.zip']",
            timeout=20000,
        )
    except Exception as e:
        print(f"  warn: no obvious row selector matched: {e}")

    html = page.content()
    (SCRATCH / f"{slug}.html").write_text(html, encoding="utf-8")

    info_text = ""
    try:
        info_text = page.locator(".dataTables_info").first.inner_text(timeout=2000)
    except Exception:
        pass
    m = re.search(r"(\d+)", info_text)
    total = int(m.group(1)) if m else None

    row_count = 0
    sample = []
    try:
        rows_data = page.eval_on_selector_all(
            "table.posts-data-table tbody tr",
            """rows => rows.slice(0, 3).map(r => {
              const tds = r.querySelectorAll('td');
              const links = r.querySelectorAll('a');
              return {
                cells: [...tds].map(t => t.textContent.trim().slice(0, 80)),
                links: [...links].map(a => ({href: a.href, text: a.textContent.trim().slice(0,60)})),
              };
            })""",
        )
        sample = rows_data
        row_count = page.eval_on_selector_all("table.posts-data-table tbody tr", "rs => rs.length")
    except Exception as e:
        print(f"  warn: posts-data-table not found: {e}")

    all_links = page.eval_on_selector_all(
        "table.posts-data-table tbody tr a, .download-list a, .post-row a",
        "els => els.slice(0, 5).map(e => ({href: e.href, text: e.textContent.trim().slice(0,80)}))",
    )
    (SCRATCH / f"{slug}_links.json").write_text(json.dumps(all_links, indent=2), encoding="utf-8")

    return {
        "slug": slug,
        "url": url,
        "title": page.title(),
        "info_text": info_text,
        "total": total,
        "visible_rows": row_count,
        "sample_rows": sample,
        "html_bytes": len(html),
    }


def main() -> None:
    SCRATCH.mkdir(exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            headless=False,
            viewport={"width": 1400, "height": 1000},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(60000)
        results = []
        for slug in SLUGS:
            print(f"\n--- {slug} ---")
            try:
                r = probe(page, slug)
            except Exception as e:
                print(f"  FAIL: {e}")
                continue
            results.append(r)
            print(f"  title: {r['title']!r}")
            print(f"  info_text: {r['info_text']!r}  total = {r['total']}")
            print(f"  visible rows on first page: {r['visible_rows']}")
            print(f"  html bytes: {r['html_bytes']:,}")
            if r["sample_rows"]:
                print(f"  first row cells:  {r['sample_rows'][0]['cells']}")
                print(f"  first row links:  {r['sample_rows'][0]['links']}")
        ctx.close()

    (SCRATCH / "probe_summary.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\n\nwrote summary to {SCRATCH / 'probe_summary.json'}")


if __name__ == "__main__":
    main()
