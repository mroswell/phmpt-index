"""Crawl the phmpt.org multiple-file-downloads listing.

Reads every row of the DataTables-backed plugin table and writes
`data/zips.json` with one record per zip batch:

    {
        "filename":    "md-eua-050126.zip",
        "url":         "https://mdata0612.s3.us-east-2.amazonaws.com/...zip",
        "date":        "2026-05-01",           # ISO from `data-sort` attr
        "date_text":   "May 1, 2026",
        "size_text":   "158 MB",
        "size_bytes":  165675827,              # approximate; HTTP confirms
        "batch_code":  "md-eua"
    }

The listing is wrapped in Cloudflare's challenge, so we drive a real
Chromium with the persistent profile saved by bootstrap.py. The zips
themselves are on AWS S3 and won't need a browser to download.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / ".profile"
DATA = ROOT / "data"
TARGET = "https://phmpt.org/multiple-file-downloads/"

# Order matters: longest prefix first so md-eua wins over md.
BATCH_PREFIXES = [
    "p1215d-eua",
    "p1215d",
    "pd-eua",
    "pd",
    "md-eua",
    "md",
]


def batch_code(filename: str) -> str | None:
    name = filename.removesuffix(".zip").removesuffix(".ZIP")
    for code in BATCH_PREFIXES:
        if name == code or name.startswith(code + "-"):
            return code
    return None


def parse_size(text: str) -> int | None:
    m = re.match(r"([\d.]+)\s*(B|KB|MB|GB|TB)\b", text.strip(), re.I)
    if not m:
        return None
    n = float(m.group(1))
    mult = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
    return int(n * mult[m.group(2).upper()])


def main() -> None:
    DATA.mkdir(exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            headless=False,
            viewport={"width": 1400, "height": 1000},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(60000)
        page.goto(TARGET, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        page.wait_for_selector("table.posts-data-table tbody tr", timeout=20000)

        total_text = page.locator(".dataTables_info").first.inner_text()
        m = re.search(r"(\d+)", total_text)
        total = int(m.group(1)) if m else None
        print(f"info text: {total_text!r}  -> total = {total}")

        # Ask DataTables to render every row, then wait until it has.
        page.evaluate(
            """() => {
              const sel = document.querySelector('.dataTables_length select');
              if (!sel) throw new Error('no length select found');
              // jQuery is loaded by DataTables; trigger its change handler.
              window.jQuery(sel).val('-1').trigger('change');
            }"""
        )
        if total:
            page.wait_for_function(
                "expected => document.querySelectorAll('table.posts-data-table tbody tr').length >= expected",
                arg=total,
                timeout=30000,
            )

        rows_data = page.eval_on_selector_all(
            "table.posts-data-table tbody tr",
            """rows => rows.map(r => {
              const tds = r.querySelectorAll('td');
              const link = r.querySelector('td.col-link a');
              return {
                filename:  tds[0] ? tds[0].textContent.trim() : null,
                date:      tds[1] ? tds[1].getAttribute('data-sort') : null,
                date_text: tds[1] ? tds[1].textContent.trim() : null,
                size_text: tds[2] ? tds[2].textContent.trim() : null,
                url:       link ? link.href : null,
              };
            })""",
        )
        ctx.close()

    rows = []
    for r in rows_data:
        if not r.get("filename") or not r.get("url"):
            print("warn: skipping row with missing fields:", r)
            continue
        rows.append(
            {
                "filename":   r["filename"],
                "url":        r["url"],
                "date":       r["date"],
                "date_text":  r["date_text"],
                "size_text":  r["size_text"],
                "size_bytes": parse_size(r["size_text"]) if r["size_text"] else None,
                "batch_code": batch_code(r["filename"]),
            }
        )

    unclassified = [r for r in rows if r["batch_code"] is None]
    if unclassified:
        print(f"warn: {len(unclassified)} zips have unknown batch code:")
        for r in unclassified[:5]:
            print("  ", r["filename"])

    out = DATA / "zips.json"
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    total_size = sum(r["size_bytes"] or 0 for r in rows)
    print(f"wrote {len(rows)} rows to {out}")
    print(f"approx total download size: {total_size / 1024**3:.1f} GB")

    by_code: dict[str, int] = {}
    for r in rows:
        by_code[r["batch_code"] or "?"] = by_code.get(r["batch_code"] or "?", 0) + 1
    print("rows per batch_code:")
    for code, n in sorted(by_code.items()):
        print(f"  {n:4d}  {code}")


if __name__ == "__main__":
    main()
