"""Download every zip listed in data/zips.json from S3.

Serial (one at a time), resume-aware (partial files are continued via
HTTP Range), and idempotent (re-running skips files already at the right
size). S3 is not behind Cloudflare, so plain httpx is enough — no
Playwright needed.

Output: data/zips/<batch_code>/<filename>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
from tqdm import tqdm

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ZIPS_DIR = DATA / "zips"
LISTING = DATA / "zips.json"

CHUNK = 1 << 20  # 1 MiB
TIMEOUT = httpx.Timeout(30.0, connect=15.0, read=60.0)
HEADERS = {"User-Agent": "phmpt-foia-toc/0.1 (research mirror)"}


def expected_size(client: httpx.Client, url: str) -> int:
    """Get the true byte length from S3 via HEAD."""
    r = client.head(url, follow_redirects=True)
    r.raise_for_status()
    cl = r.headers.get("content-length")
    if cl is None:
        raise RuntimeError(f"no Content-Length for {url}")
    return int(cl)


def download_one(client: httpx.Client, url: str, dest: Path) -> tuple[str, int]:
    """Returns ('skip' | 'resume' | 'fresh', bytes_written)."""
    target_size = expected_size(client, url)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists():
        have = dest.stat().st_size
        if have == target_size:
            return "skip", 0
        if have > target_size:
            print(f"  on-disk ({have}) > expected ({target_size}); restarting")
            dest.unlink()
            have = 0
    else:
        have = 0

    mode = "ab" if have > 0 else "wb"
    headers = {"Range": f"bytes={have}-"} if have > 0 else {}
    status = "resume" if have > 0 else "fresh"

    written = 0
    with client.stream("GET", url, headers=headers, follow_redirects=True) as r:
        r.raise_for_status()
        # Range request → 206; full GET → 200. Both fine.
        if have > 0 and r.status_code != 206:
            # Server ignored Range; restart from zero.
            print(f"  server did not honor Range (status {r.status_code}); restarting")
            r.close()
            dest.unlink()
            return download_one(client, url, dest)
        with dest.open(mode) as f, tqdm(
            total=target_size,
            initial=have,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            desc=dest.name,
            leave=False,
        ) as bar:
            for chunk in r.iter_bytes(CHUNK):
                f.write(chunk)
                written += len(chunk)
                bar.update(len(chunk))

    final = dest.stat().st_size
    if final != target_size:
        raise RuntimeError(
            f"size mismatch for {dest}: got {final}, expected {target_size}"
        )
    return status, written


def main() -> None:
    if not LISTING.exists():
        print(f"error: {LISTING} not found — run crawl_listing.py first")
        sys.exit(1)
    rows = json.loads(LISTING.read_text())
    print(f"loaded {len(rows)} zip entries from {LISTING}")

    ZIPS_DIR.mkdir(parents=True, exist_ok=True)

    skipped = resumed = fresh = 0
    total_written = 0
    failures: list[tuple[str, str]] = []

    with httpx.Client(timeout=TIMEOUT, headers=HEADERS) as client:
        for i, row in enumerate(rows, 1):
            code = row["batch_code"] or "unknown"
            dest = ZIPS_DIR / code / row["filename"]
            print(f"[{i:>3}/{len(rows)}] {row['filename']}  ({row['size_text']})")
            try:
                status, wrote = download_one(client, row["url"], dest)
            except Exception as e:
                print(f"  FAIL: {e}")
                failures.append((row["filename"], str(e)))
                continue
            if status == "skip":
                skipped += 1
                print("  already complete")
            elif status == "resume":
                resumed += 1
                total_written += wrote
                print(f"  resumed; wrote {wrote / 1024**2:.1f} MiB")
            else:
                fresh += 1
                total_written += wrote
                print(f"  downloaded {wrote / 1024**2:.1f} MiB")

    print()
    print(f"done. fresh={fresh}, resumed={resumed}, skipped={skipped}, failed={len(failures)}")
    print(f"total bytes written: {total_written / 1024**3:.2f} GiB")
    if failures:
        print("failures:")
        for name, err in failures:
            print(f"  {name}: {err}")
        sys.exit(2)


if __name__ == "__main__":
    main()
