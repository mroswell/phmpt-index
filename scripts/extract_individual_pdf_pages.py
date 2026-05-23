#!/usr/bin/env python3
"""Extract page counts for individual PDF files from company-documents pages.

Approach:
1. Launch Playwright browser using persistent profile (Cloudflare-cleared).
2. Re-warm the session every REWARM_EVERY requests so cookies stay fresh.
3. Use the browser's APIRequestContext to fetch each PDF over HTTP —
   inherits cookies + User-Agent from the browser, so phmpt.org accepts it.
   (Direct httpx fails with 403; navigating with page.goto() renders PDFs
   inline instead of downloading them.)
4. Sleep MIN_DELAY..MAX_DELAY seconds between requests (jittered) so we
   don't trip Cloudflare's rate limiter.
5. If we see CIRCUIT_BREAK consecutive 403s, abort — session is dead and
   continuing will only deepen the block.
6. Open bytes with PyMuPDF, read page count, discard.
7. Cache results so the script is resumable.
"""

import json
import random
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright
import fitz  # PyMuPDF

MIN_DELAY = 3.0           # seconds between PDF requests (lower bound)
MAX_DELAY = 6.0           # upper bound — jitter looks more human
REWARM_EVERY = 20         # re-visit a normal product page every N PDFs
CIRCUIT_BREAK = 5         # bail out after this many consecutive 403s

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ORPHANS = DATA / "orphans.json"
CACHE_FILE = DATA / "individual_pdf_page_counts.json"
PROFILE = ROOT / ".profile"

# Force unbuffered output so progress appears in real time when run in background
sys.stdout.reconfigure(line_buffering=True)


def load_cache() -> dict:
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text())
    return {}


def save_cache(cache: dict) -> None:
    CACHE_FILE.write_text(json.dumps(cache, indent=2))


def fetch_pdf_bytes(request_ctx, url: str, referer: str | None) -> tuple[bytes | None, int | None]:
    """Fetch PDF bytes via authenticated browser HTTP context.

    Returns (body_or_None, http_status_or_None).
    """
    headers = {"Referer": referer} if referer else {}
    try:
        response = request_ctx.get(url, timeout=120000, headers=headers)
    except Exception as e:
        print(f"  ❌ request error: {e}")
        return None, None

    if not response.ok:
        print(f"  ❌ HTTP {response.status}")
        return None, response.status

    body = response.body()
    if not body:
        print(f"  ❌ empty body")
        return None, response.status
    return body, response.status


def page_count_from_bytes(body: bytes) -> int | None:
    try:
        doc = fitz.open(stream=body, filetype="pdf")
        n = doc.page_count
        doc.close()
        return n
    except Exception as e:
        print(f"  ❌ PyMuPDF: {e}")
        return None


def main() -> None:
    if not PROFILE.exists():
        print("Error: .profile not found. Run scripts/bootstrap.py first.")
        return

    orphans = json.loads(ORPHANS.read_text())
    individual_pdfs = [
        o for o in orphans
        if o["filename"].lower().endswith(".pdf")
        and not o["filename"].lower().endswith(".zip")
    ]
    print(f"Found {len(individual_pdfs)} individual PDFs total")

    cache = load_cache()
    successful_before = sum(1 for v in cache.values() if v is not None)
    print(f"Cache: {successful_before} previously successful, {len(cache) - successful_before} previously failed")

    # Retry anything that's missing or previously failed
    todo = [p for p in individual_pdfs if cache.get(p["url"]) is None]
    print(f"To process: {len(todo)} PDFs")

    if not todo:
        print("Nothing to do.")
        return

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            headless=True,
            accept_downloads=True,
        )
        page = context.pages[0] if context.pages else context.new_page()

        def warm_session(product_page: str = "/") -> bool:
            url = f"https://phmpt.org{product_page}"
            try:
                r = page.goto(url, timeout=60000, wait_until="domcontentloaded")
                ok = r is not None and r.status == 200
                print(f"  {'✅' if ok else '⚠️'} warm: {url} -> HTTP {r.status if r else '?'}")
                return ok
            except Exception as e:
                print(f"  ⚠️  warm-up failed: {e}")
                return False

        print("\nWarming phmpt.org session...")
        if not warm_session("/"):
            print("⛔ Cloudflare still blocking the homepage. Re-run scripts/bootstrap.py.")
            context.close()
            return

        request_ctx = context.request

        new_success = 0
        new_failure = 0
        consecutive_403 = 0

        for i, pdf in enumerate(todo, 1):
            url = pdf["url"]
            fname = pdf["filename"]
            referer = f"https://phmpt.org{pdf.get('product_page', '')}" if pdf.get("product_page") else None
            print(f"[{i}/{len(todo)}] {fname}")

            body, status = fetch_pdf_bytes(request_ctx, url, referer)

            if status == 403:
                consecutive_403 += 1
                if consecutive_403 >= CIRCUIT_BREAK:
                    print(f"\n⛔ Circuit breaker: {CIRCUIT_BREAK} consecutive 403s. Aborting to avoid deeper block.")
                    print("   Wait 30+ minutes before retrying, or re-run scripts/bootstrap.py.")
                    cache[url] = None
                    new_failure += 1
                    break
            else:
                consecutive_403 = 0

            if body is None:
                cache[url] = None
                new_failure += 1
            else:
                n = page_count_from_bytes(body)
                cache[url] = n
                if n is None:
                    new_failure += 1
                else:
                    new_success += 1
                    print(f"  ✅ {n} pages ({len(body):,} bytes)")

            # Save every 10
            if (new_success + new_failure) % 10 == 0:
                save_cache(cache)
                print(f"  💾 saved: +{new_success} success, +{new_failure} failed")

            # Re-warm session periodically — mimic browsing
            if i % REWARM_EVERY == 0 and i < len(todo):
                product = pdf.get("product_page") or "/"
                print(f"  🔄 re-warming session via {product}")
                warm_session(product)

            # Jittered delay between requests
            if i < len(todo):
                time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

        context.close()

    save_cache(cache)

    successful_after = sum(1 for v in cache.values() if v is not None)
    print("\n" + "=" * 60)
    print("DONE")
    print(f"  This run: +{new_success} successes, +{new_failure} failures")
    print(f"  Total cached successes: {successful_after} / {len(individual_pdfs)}")
    print(f"  Cache: {CACHE_FILE}")

    successful_counts = [v for v in cache.values() if v is not None]
    if successful_counts:
        print(f"\nPage stats: min={min(successful_counts)}, max={max(successful_counts)}, "
              f"avg={sum(successful_counts) / len(successful_counts):.1f}")


if __name__ == "__main__":
    main()