"""Verify the ~45 orphan zips whose filenames DON'T pattern-match
anything we already have. Downloads each one, reads its central
directory (no extraction), and reports per-inner-file whether it's
already in our 8,069-row index or genuinely new.

Two transports because the orphans live on two hosts:
  - AWS S3 URLs: plain httpx
  - phmpt.org/wp-content URLs: Playwright page navigation
    (CF blocks Playwright's API requests but allows real navigations)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import zipfile_deflate64 as zipfile
from playwright.sync_api import sync_playwright
from tqdm import tqdm

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / ".profile"
DATA = ROOT / "data"
ORPHANS = DATA / "orphans.json"
TOC = DATA / "toc.json"
SCRATCH = ROOT / ".scratch" / "orphan-outliers"

LIKELY_EXTS = [".pdf", ".doc", ".docx", ".xpt", ".xlsx", ".txt", ".xls", ".xml"]


def find_outliers():
    """Return list of orphan dicts whose filenames don't pattern-match."""
    toc = json.loads(TOC.read_text())
    known = {row["member_name"].rsplit("/", 1)[-1] for row in toc}
    orphans = json.loads(ORPHANS.read_text())
    out = []
    for o in orphans:
        fn = o["filename"]
        if not fn.lower().endswith(".zip"):
            continue
        stem = fn[:-4]
        if any(stem + ext in known for ext in LIKELY_EXTS):
            continue
        out.append(o)
    return out, known


def download_s3(url: str, dest: Path) -> None:
    """S3 URLs are direct — no CF in front."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", url, timeout=120.0, follow_redirects=True) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)


def download_phmpt(page, url: str, dest: Path) -> None:
    """phmpt.org/wp-content URLs need a real browser navigation."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with page.expect_download(timeout=60000) as dl_info:
        try:
            page.goto(url)
        except Exception:
            pass  # goto can raise when the response is a download; that's fine
    download = dl_info.value
    download.save_as(str(dest))


def inspect(zp: Path, known: set[str]) -> dict:
    """Read central directory, classify each inner file vs known basenames."""
    try:
        with zipfile.ZipFile(zp) as zf:
            members = [i for i in zf.infolist() if not i.is_dir()]
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    inners = [i.filename.rsplit("/", 1)[-1] for i in members]
    dup = [n for n in inners if n in known]
    new = [n for n in inners if n not in known]
    return {"inner_count": len(inners), "duplicate": dup, "new": new}


def main() -> None:
    outliers, known = find_outliers()
    s3 = [o for o in outliers if "amazonaws.com" in o["url"]]
    phmpt = [o for o in outliers if "phmpt.org" in o["url"]]
    print(f"outliers: {len(outliers)}  ({len(s3)} S3, {len(phmpt)} phmpt.org)")
    print()

    SCRATCH.mkdir(parents=True, exist_ok=True)
    saved: dict[str, Path] = {}

    # S3 first — fast, parallel-safe.
    for o in tqdm(s3, desc="S3 downloads"):
        dest = SCRATCH / o["filename"]
        if not dest.exists():
            try:
                download_s3(o["url"], dest)
            except Exception as e:
                print(f"  S3 FAIL {o['filename']}: {e}")
                continue
        saved[o["url"]] = dest

    # phmpt.org via Playwright — serial.
    if phmpt:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE),
                headless=False,
                accept_downloads=True,
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            for o in tqdm(phmpt, desc="phmpt downloads"):
                dest = SCRATCH / o["filename"]
                if not dest.exists():
                    try:
                        download_phmpt(page, o["url"], dest)
                    except Exception as e:
                        print(f"  phmpt FAIL {o['filename']}: {e}")
                        continue
                saved[o["url"]] = dest
            ctx.close()

    print()
    print(f"downloaded {len(saved)} of {len(outliers)} outliers to {SCRATCH}")
    print()

    # Inspect each, compare members vs known
    tot_inner = tot_dup = tot_new = 0
    fully_dup = fully_new = mixed = errored = 0
    all_new_files: list[tuple[str, str]] = []  # (parent_zip, new_filename)

    for o in outliers:
        zp = saved.get(o["url"])
        if not zp or not zp.exists():
            continue
        result = inspect(zp, known)
        if "error" in result:
            errored += 1
            print(f"  ERR  {o['filename']}: {result['error']}")
            continue
        n_inner = result["inner_count"]
        n_dup = len(result["duplicate"])
        n_new = len(result["new"])
        tot_inner += n_inner
        tot_dup += n_dup
        tot_new += n_new
        if n_new == 0:
            fully_dup += 1
            print(f"  DUP   {o['filename']}  ({n_inner} inner, all already known)")
        elif n_dup == 0:
            fully_new += 1
            print(f"  NEW   {o['filename']}  ({n_inner} inner, all new)")
        else:
            mixed += 1
            print(f"  MIX   {o['filename']}  ({n_dup} known + {n_new} new)")
        for nf in result["new"]:
            all_new_files.append((o["filename"], nf))

    print()
    print("=" * 60)
    print(f"outlier zips inspected: {len(saved):,}")
    print(f"  fully-duplicate (every inner file already in our index): {fully_dup}")
    print(f"  fully-new       (no inner file in our index):             {fully_new}")
    print(f"  mixed:                                                    {mixed}")
    print(f"  errored:                                                  {errored}")
    print()
    print(f"total inner files across outliers: {tot_inner:,}")
    print(f"  duplicates of known files: {tot_dup:,}")
    print(f"  new (not in our index):    {tot_new:,}")
    if tot_new:
        print()
        print(f"first 10 new inner filenames:")
        for parent, nf in all_new_files[:10]:
            print(f"  {nf}    (from orphan zip: {parent})")


if __name__ == "__main__":
    main()
