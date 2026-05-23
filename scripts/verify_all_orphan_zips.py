"""Download and verify ALL 308 orphan zip files against main zip bundles.

Builds on check_orphan_outliers.py but processes ALL orphan zip files,
not just the ones that don't pattern-match. This gives us complete
verification instead of relying on pattern matching assumptions.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from collections import defaultdict

import httpx
import zipfile_deflate64 as zipfile
from playwright.sync_api import sync_playwright
from tqdm import tqdm

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / ".profile"
DATA = ROOT / "data"
ORPHANS = DATA / "orphans.json"
TOC = DATA / "toc.json"
SCRATCH = ROOT / ".scratch" / "all-orphan-zips"

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

def inspect(zp: Path, known_files: set[str]) -> dict:
    """Read central directory, classify each inner file vs known basenames."""
    try:
        with zipfile.ZipFile(zp) as zf:
            members = [i for i in zf.infolist() if not i.is_dir()]
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

    inners = [i.filename.rsplit("/", 1)[-1] for i in members]
    dup = [n for n in inners if n in known_files]
    new = [n for n in inners if n not in known_files]

    return {
        "inner_count": len(inners),
        "inner_files": inners,
        "duplicate": dup,
        "new": new
    }

def main() -> None:
    # Load data
    toc = json.loads(TOC.read_text())
    orphans = json.loads(ORPHANS.read_text())

    # Create filename -> zip_source mapping from TOC
    known_files = {row["member_name"].rsplit("/", 1)[-1] for row in toc}
    file_to_zip = {}
    for row in toc:
        filename = row["member_name"].rsplit("/", 1)[-1]
        file_to_zip[filename] = row["zip_source"]

    print(f"Loaded {len(toc):,} files from {len(set(row['zip_source'] for row in toc))} main zip bundles")

    # Get ALL orphan zip files
    orphan_zips = [o for o in orphans if o["filename"].lower().endswith(".zip")]
    s3_orphans = [o for o in orphan_zips if "amazonaws.com" in o["url"]]
    phmpt_orphans = [o for o in orphan_zips if "phmpt.org" in o["url"]]

    print(f"Total orphan zip files: {len(orphan_zips)}")
    print(f"  S3 URLs: {len(s3_orphans)}")
    print(f"  phmpt.org URLs: {len(phmpt_orphans)}")
    print()

    SCRATCH.mkdir(parents=True, exist_ok=True)
    saved: dict[str, Path] = {}

    # S3 first — fast, parallel-safe
    for o in tqdm(s3_orphans, desc="Downloading S3 orphan zips"):
        dest = SCRATCH / o["filename"]
        if not dest.exists():
            try:
                download_s3(o["url"], dest)
            except Exception as e:
                print(f"  S3 FAIL {o['filename']}: {e}")
                continue
        saved[o["url"]] = dest

    # phmpt.org via Playwright — serial
    if phmpt_orphans:
        print(f"Downloading {len(phmpt_orphans)} phmpt.org orphan zips...")
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE),
                headless=False,
                accept_downloads=True,
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            for o in tqdm(phmpt_orphans, desc="phmpt downloads"):
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
    print(f"Downloaded {len(saved):,} of {len(orphan_zips):,} orphan zips to {SCRATCH}")
    print()

    # Analyze each orphan zip
    results = {
        "summary": {"total": 0, "duplicates": 0, "new": 0, "mixed": 0, "errors": 0},
        "by_main_zip": defaultdict(list),
        "new_files": [],
        "duplicates": [],
        "mixed": [],
        "errors": []
    }

    for o in orphan_zips:
        zp = saved.get(o["url"])
        if not zp or not zp.exists():
            continue

        result = inspect(zp, known_files)
        if "error" in result:
            results["summary"]["errors"] += 1
            results["errors"].append({
                "orphan_zip": o["filename"],
                "error": result["error"]
            })
            continue

        results["summary"]["total"] += 1

        if result["new"] and result["duplicate"]:
            # Mixed: some files new, some duplicate
            results["summary"]["mixed"] += 1
            results["mixed"].append({
                "orphan_zip": o["filename"],
                "orphan_url": o["url"],
                "product_page": o["product_page"],
                "inner_files": result["inner_files"],
                "duplicates": result["duplicate"],
                "new_files": result["new"],
                "duplicate_count": len(result["duplicate"]),
                "new_count": len(result["new"])
            })
        elif result["new"]:
            # All files are new
            results["summary"]["new"] += 1
            results["new_files"].append({
                "orphan_zip": o["filename"],
                "orphan_url": o["url"],
                "product_page": o["product_page"],
                "inner_files": result["inner_files"],
                "new_files": result["new"]
            })
        else:
            # All files are duplicates
            results["summary"]["duplicates"] += 1
            # Find which main zips contain these files
            main_zips = set()
            for dup_file in result["duplicate"]:
                if dup_file in file_to_zip:
                    main_zips.add(file_to_zip[dup_file])

            dup_record = {
                "orphan_zip": o["filename"],
                "orphan_url": o["url"],
                "product_page": o["product_page"],
                "inner_files": result["inner_files"],
                "duplicate_files": result["duplicate"],
                "main_zip_sources": list(main_zips)
            }
            results["duplicates"].append(dup_record)

            # Add to by_main_zip mapping
            for main_zip in main_zips:
                results["by_main_zip"][main_zip].append(dup_record)

    # Convert defaultdict to regular dict for JSON serialization
    results["by_main_zip"] = dict(results["by_main_zip"])

    # Print summary
    print("=" * 60)
    print(f"VERIFICATION RESULTS:")
    print(f"  Total orphan zips analyzed: {results['summary']['total']:,}")
    print(f"  Fully duplicate (all files already in main zips): {results['summary']['duplicates']:,}")
    print(f"  Fully new (no files in main zips): {results['summary']['new']:,}")
    print(f"  Mixed (some duplicate, some new): {results['summary']['mixed']:,}")
    print(f"  Errors: {results['summary']['errors']:,}")
    print()

    # Top main zips with most duplicates
    zip_counts = [(zip_name, len(orphans)) for zip_name, orphans in results["by_main_zip"].items()]
    zip_counts.sort(key=lambda x: x[1], reverse=True)

    print("Top 10 main zips with most orphan duplicates:")
    for zip_name, count in zip_counts[:10]:
        print(f"  {zip_name}: {count} orphan zip files")

    print()
    if results["new_files"]:
        print(f"NEW FILES ({len(results['new_files'])} orphan zips):")
        for nf in results["new_files"][:10]:  # Show first 10
            print(f"  {nf['orphan_zip']}")
            for inner in nf["inner_files"][:3]:  # Show first 3 inner files
                print(f"    - {inner}")

    if results["mixed"]:
        print(f"\nMIXED FILES ({len(results['mixed'])} orphan zips):")
        for mix in results["mixed"][:5]:  # Show first 5
            print(f"  {mix['orphan_zip']}: {mix['duplicate_count']} dup + {mix['new_count']} new")

    # Save complete results
    output_file = DATA / "complete_orphan_verification.json"
    output_file.write_text(json.dumps(results, indent=2))
    print(f"\nComplete results saved to: {output_file}")

if __name__ == "__main__":
    main()