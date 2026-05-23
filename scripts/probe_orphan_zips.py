"""Size probe for the .zip orphans (phmpt-only zips).

Most of these URLs are gated by Cloudflare, so we drive Playwright's
request context using the persistent profile (same trick as the
listing crawl). Each request is a small Range-GET — no full downloads.

Output: data/orphan_zip_sizes.json   { url: bytes }
        console: histogram + total bytes
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from playwright.sync_api import sync_playwright
from tqdm import tqdm

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / ".profile"
DATA = ROOT / "data"
ORPHANS = DATA / "orphans.json"
OUT = DATA / "orphan_zip_sizes.json"


def size_of(request_ctx, url: str) -> tuple[int | None, str]:
    """Returns (bytes, mode). Modes: 'head', 'range', 'range-200', 'fail:...'."""
    # Try a 1-byte Range-GET directly; that's the universal trick.
    try:
        r = request_ctx.get(url, headers={"Range": "bytes=0-0"})
    except Exception as e:
        return None, f"fail:exc={type(e).__name__}"
    if r.status == 206:
        cr = r.headers.get("content-range", "")
        if "/" in cr:
            total = cr.split("/", 1)[1].strip()
            if total.isdigit():
                return int(total), "range"
    if r.status == 200:
        cl = r.headers.get("content-length")
        if cl and cl.isdigit():
            return int(cl), "range-200"
    return None, f"fail:http{r.status}"


def main() -> None:
    orphans = json.loads(ORPHANS.read_text())
    zip_urls = [o["url"] for o in orphans if o["filename"].lower().endswith(".zip")]
    print(f"HEAD-checking {len(zip_urls)} orphan zip URLs...")

    sizes: dict[str, int | None] = {}
    modes: dict[str, str] = {}
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            headless=True,
        )
        with tqdm(total=len(zip_urls)) as bar:
            for url in zip_urls:
                size, mode = size_of(ctx.request, url)
                sizes[url] = size
                modes[url] = mode
                bar.update(1)
        ctx.close()

    OUT.write_text(json.dumps(sizes, indent=2))

    mode_counter = Counter(modes.values())
    print()
    print("how we got each size:")
    for mode, n in mode_counter.most_common():
        print(f"  {n:>4}  {mode}")

    known = [s for s in sizes.values() if s is not None]
    failed = sum(1 for s in sizes.values() if s is None)
    total = sum(known)
    print()
    print(f"sized: {len(known)}  /  failed: {failed}")
    print(f"total bytes: {total:,}  ({total / 1024**3:.2f} GiB)")
    print()

    buckets = [
        ("< 10 MB",       0,           10 * 1024**2),
        ("10 – 50 MB",    10 * 1024**2, 50 * 1024**2),
        ("50 – 200 MB",   50 * 1024**2, 200 * 1024**2),
        ("200 MB – 1 GB", 200 * 1024**2, 1 * 1024**3),
        ("1 – 5 GB",      1 * 1024**3,   5 * 1024**3),
        ("> 5 GB",        5 * 1024**3,   1 << 60),
    ]
    print(f"{'bucket':<18} {'count':>6} {'cumulative bytes':>20}")
    cum = 0
    for label, lo, hi in buckets:
        in_bucket = [s for s in known if lo <= s < hi]
        bucket_bytes = sum(in_bucket)
        cum += bucket_bytes
        print(f"  {label:<16} {len(in_bucket):>6} {cum / 1024**3:>17.2f} GiB")

    # Smallest first — useful if the user wants to start with cheap files.
    print()
    top_small = sorted(known)[:5]
    top_big = sorted(known)[-5:]
    print(f"smallest 5: {[f'{s/1024**2:.1f} MiB' for s in top_small]}")
    print(f"largest 5:  {[f'{s/1024**3:.2f} GiB' for s in top_big]}")


if __name__ == "__main__":
    main()
