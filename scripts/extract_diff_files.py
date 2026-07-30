"""Extract the trove (this-release) copy of every document that was redacted
differently, into the trial-foia site so both versions can be opened side by
side, and scan each per-page so we can point to the pages where the redactions
differ.

Output:
  FDA-FOIA-2026-6007/site/diff-files/<filename>          the trove PDF
  data/cache/diff_perpage/<md5>.json                     per-page markers (cache)
Run:  uv run python scripts/extract_diff_files.py
"""

from __future__ import annotations

import csv
import hashlib
import json
import multiprocessing as mp
import os
import sys
from pathlib import Path

import zipfile_deflate64 as zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _scan_core import scan_pdf_bytes_perpage
from build_trove_exemptions import canon_markers

ROOT = Path(__file__).resolve().parent.parent
DIFFS = ROOT / "FDA-FOIA-2026-6007" / "site" / "redaction_diffs.json"
INV = ROOT / "FDA-FOIA-2026-6007" / "file_inventory.csv"
BUNDLES = ROOT / ".scratch" / "muckrock_bundles"
OUTDIR = ROOT / "FDA-FOIA-2026-6007" / "site" / "diff-files"
CACHE = ROOT / "data" / "cache" / "diff_perpage"
WORKERS = 8


def _member_index(zpath):
    with zipfile.ZipFile(zpath) as z:
        return {os.path.basename(n): n for n in z.namelist() if not n.endswith("/")}


def _worker(task):
    bundle_path, member, filename = task
    dest = OUTDIR / filename
    cp = CACHE / f"{hashlib.md5(filename.encode()).hexdigest()}.json"
    try:
        with zipfile.ZipFile(bundle_path) as z:
            data = z.read(member)
        if not dest.exists():
            dest.write_bytes(data)
        if cp.exists():
            per = json.loads(cp.read_text())
        else:
            r = scan_pdf_bytes_perpage(data)
            per = {"total_pages": r.get("total_pages"),
                   "pages": {p: canon_markers(mk) for p, mk in r.get("pages", {}).items()}}
            cp.write_text(json.dumps(per))
        return filename, len(data), per
    except Exception as e:
        return filename, 0, {"error": str(e)}


def main():
    OUTDIR.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    diffs = json.loads(DIFFS.read_text())
    inv = {r["filename"]: r["bundle"] for r in csv.DictReader(open(INV))}

    bundle_idx = {}
    tasks = []
    for d in diffs:
        fn = d["filename"]
        b = inv.get(fn)
        if not b:
            continue
        bz = BUNDLES / b
        if not bz.exists():
            continue
        if b not in bundle_idx:
            bundle_idx[b] = _member_index(bz)
        member = bundle_idx[b].get(fn)
        if member:
            tasks.append((str(bz), member, fn))

    print(f"{len(tasks)} files to extract + per-page scan", flush=True)
    sizes, perpage = {}, {}
    with mp.Pool(WORKERS) as pool:
        for i, (fn, sz, per) in enumerate(pool.imap_unordered(_worker, tasks, 4), 1):
            sizes[fn] = sz
            perpage[fn] = per
            if i % 100 == 0:
                print(f"  {i}/{len(tasks)}", flush=True)
    json.dump({"sizes": sizes}, open(ROOT / "data" / "diff_file_sizes.json", "w"))
    print(f"\nextracted {len(sizes)} files to {OUTDIR}")
    print(f"  total {sum(sizes.values())/1e9:.2f} GB")


if __name__ == "__main__":
    main()
