"""Extract every M3 PDF from the downloaded ZIPs to data/files/.

All 369 M3 files live inside ZIPs already on disk under data/zips/. We
extract them to data/files/{batch_code}/{filename}.pdf so the layout
mirrors the ZIP organization and scales when we later cover all modules.

Idempotent: skips a file if the destination already exists.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import zipfile_deflate64 as zipfile  # matches scripts/extract_toc.py
from tqdm import tqdm

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ZIPS_DIR = DATA / "zips"
FILES_DIR = DATA / "files"
INDEX = ROOT / "docs" / "data" / "index.json"


def main(module: str = "M3") -> None:
    if not INDEX.exists():
        sys.exit(f"Missing {INDEX} — run scripts/build_index.py first")

    index = json.loads(INDEX.read_text())
    targets = [
        r for r in index
        if r.get("module") == module
        and r.get("extension") == "pdf"
        and r.get("zip_source")
    ]
    print(f"{len(targets)} {module} PDFs to extract")

    # Group by ZIP so we only open each archive once
    by_zip: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in targets:
        by_zip[(r["batch_code"], r["zip_source"])].append(r)

    print(f"Across {len(by_zip)} ZIP bundles")
    print()

    extracted = 0
    skipped = 0
    missing_zip = 0
    missing_member = 0

    for (batch_code, zip_name), rows in by_zip.items():
        zip_path = ZIPS_DIR / batch_code / zip_name
        if not zip_path.exists():
            print(f"⚠️  ZIP missing, skipping {len(rows)} files: {zip_path}")
            missing_zip += len(rows)
            continue

        out_dir = FILES_DIR / batch_code
        out_dir.mkdir(parents=True, exist_ok=True)

        # Decide what's actually needed (skip files already extracted)
        needed = [r for r in rows if not (out_dir / r["filename"]).exists()]
        if not needed:
            skipped += len(rows)
            continue

        skipped += len(rows) - len(needed)

        with zipfile.ZipFile(zip_path) as zf:
            # Build a basename -> ZipInfo map so we can lookup by filename
            # (member_name in toc may include subdirs, but per explore agent
            # M3 toc entries have no subdirs — still, be defensive)
            by_basename: dict[str, zipfile.ZipInfo] = {}
            for info in zf.infolist():
                if info.is_dir():
                    continue
                base = info.filename.rsplit("/", 1)[-1]
                by_basename[base] = info

            for r in tqdm(needed, desc=zip_name, unit="pdf"):
                fname = r["filename"]
                info = by_basename.get(fname)
                if info is None:
                    missing_member += 1
                    continue
                dest = out_dir / fname
                with zf.open(info) as src, dest.open("wb") as dst:
                    while True:
                        chunk = src.read(1 << 20)  # 1 MiB
                        if not chunk:
                            break
                        dst.write(chunk)
                extracted += 1

    print()
    print(f"Extracted: {extracted}")
    print(f"Already on disk (skipped): {skipped}")
    print(f"Member not found in ZIP: {missing_member}")
    print(f"ZIP file missing: {missing_zip}")
    print(f"Output directory: {FILES_DIR}")


if __name__ == "__main__":
    module = sys.argv[1] if len(sys.argv) > 1 else "M3"
    main(module)
