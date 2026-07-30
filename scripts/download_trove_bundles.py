"""Download the FDA-FOIA-2026-6007 MuckRock bundle zips for full redaction
verification. Resumable: skips complete files, resumes partial downloads with
HTTP Range. MuckRock CDN is not bot-protected, so plain httpx works.

Output: .scratch/muckrock_bundles/<bundle>.zip
Run:    uv run python scripts/download_trove_bundles.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
URLS = ROOT / "FDA-FOIA-2026-6007" / "download_urls.txt"
OUT = ROOT / ".scratch" / "muckrock_bundles"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    urls = [u.strip() for u in URLS.read_text().splitlines()
            if u.strip().lower().endswith(".zip")]
    print(f"{len(urls)} bundle zips to fetch\n", flush=True)

    for i, url in enumerate(urls, 1):
        name = url.rsplit("/", 1)[-1]
        dest = OUT / name
        with httpx.Client(timeout=60.0, follow_redirects=True) as c:
            # remote size
            head = c.head(url)
            total = int(head.headers.get("content-length", 0))
            have = dest.stat().st_size if dest.exists() else 0
            if total and have == total:
                print(f"[{i}/{len(urls)}] have  {name}  ({have/1e9:.1f} GB)", flush=True)
                continue
            mode = "ab" if 0 < have < total else "wb"
            headers = {"Range": f"bytes={have}-"} if mode == "ab" else {}
            gb = total / 1e9 if total else 0
            print(f"[{i}/{len(urls)}] {'resume' if mode=='ab' else 'get'} "
                  f"{name}  ({gb:.1f} GB)", flush=True)
            done = have
            with c.stream("GET", url, headers=headers) as r:
                r.raise_for_status()
                with open(dest, mode) as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)
                        done += len(chunk)
            print(f"        done {name}  ({done/1e9:.1f} GB)", flush=True)
    print("\nall bundles present", flush=True)


if __name__ == "__main__":
    main()
