"""Headful variant of crawl_listing.py.

Cloudflare re-challenges headless Chromium even with a cleared profile, so
this reuses the SAME persistent profile in a visible window (the fingerprint
that passed the interactive bootstrap). Writes to data/zips.new.json so the
authoritative data/zips.json is never clobbered; diff happens separately.

Retries the initial navigation to absorb the ERR_INTERNET_DISCONNECTED launch
race that can follow a force-killed browser instance.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / ".profile"
DATA = ROOT / "data"
TARGET = "https://phmpt.org/multiple-file-downloads/"
BATCH_PREFIXES = ["p1215d-eua", "p1215d", "pd-eua", "pd", "md-eua", "md"]


def batch_code(fn: str) -> str | None:
    n = fn.removesuffix(".zip").removesuffix(".ZIP")
    for c in BATCH_PREFIXES:
        if n == c or n.startswith(c + "-"):
            return c
    return None


def parse_size(t: str) -> int | None:
    m = re.match(r"([\d.]+)\s*(B|KB|MB|GB|TB)\b", t.strip(), re.I)
    if not m:
        return None
    mult = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
    return int(float(m.group(1)) * mult[m.group(2).upper()])


def main() -> None:
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE), headless=False,
            viewport={"width": 1400, "height": 1000},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(60000)

        last = None
        for attempt in range(5):
            try:
                page.goto(TARGET, wait_until="domcontentloaded", timeout=60000)
                last = None
                break
            except Exception as e:
                last = e
                print(f"  goto attempt {attempt+1} failed: {type(e).__name__}; retrying...")
                time.sleep(3)
        if last:
            raise last

        ok = False
        for _ in range(45):
            try:
                if page.locator("table.posts-data-table tbody tr").count() > 0:
                    ok = True
                    break
            except Exception:
                pass
            page.wait_for_timeout(1000)
        print("title:", repr(page.title()), "table_ready:", ok)
        if not ok:
            page.wait_for_selector("table.posts-data-table tbody tr", timeout=30000)

        info = page.locator(".dataTables_info").first.inner_text()
        m = re.search(r"(\d+)", info)
        total = int(m.group(1)) if m else None
        print("info:", info)

        page.evaluate(
            "()=>{const s=document.querySelector('.dataTables_length select');"
            "window.jQuery(s).val('-1').trigger('change');}"
        )
        if total:
            page.wait_for_function(
                "e=>document.querySelectorAll('table.posts-data-table tbody tr').length>=e",
                arg=total, timeout=30000,
            )

        rows = page.eval_on_selector_all(
            "table.posts-data-table tbody tr",
            """rows=>rows.map(r=>{const t=r.querySelectorAll('td');const a=r.querySelector('td.col-link a');
               return {filename:t[0]?t[0].textContent.trim():null,date:t[1]?t[1].getAttribute('data-sort'):null,
               date_text:t[1]?t[1].textContent.trim():null,size_text:t[2]?t[2].textContent.trim():null,
               url:a?a.href:null};})""",
        )
        ctx.close()

    out = []
    for r in rows:
        if not r.get("filename") or not r.get("url"):
            continue
        out.append({
            "filename": r["filename"], "url": r["url"], "date": r["date"],
            "date_text": r["date_text"], "size_text": r["size_text"],
            "size_bytes": parse_size(r["size_text"]) if r["size_text"] else None,
            "batch_code": batch_code(r["filename"]),
        })
    (DATA / "zips.new.json").write_text(json.dumps(out, indent=2))
    print("WROTE data/zips.new.json rows:", len(out))


if __name__ == "__main__":
    main()
