"""Upload non-.xpt documents to Cloudflare R2 so each gets a direct link.

Default is fill-gap: only upload files that have NO individual phmpt link today
(i.e. basename absent from data/individual_urls.json). --rehost-all uploads every
non-.xpt file. Idempotent via data/r2_uploads.json (keyed by zip_source||member_name);
re-runs skip anything already uploaded at the same size.

Key scheme (permalink): files/{batch_code}/{sha256_12}-{safe_basename}

    uv run python scripts/host_individual_files.py --batch p1215d-eua --limit 20 --prefix test/   # smoke test
    uv run python scripts/host_individual_files.py                                                 # full fill-gap run
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

try:
    import zipfile_deflate64 as zipfile
except Exception:
    import zipfile

from tqdm import tqdm

import _r2

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TOC = DATA / "toc.json"
INDIVIDUAL = DATA / "individual_urls.json"
ZIPS = DATA / "zips"


def _rows(obj):
    return obj if isinstance(obj, list) else next(v for v in obj.values() if isinstance(v, list))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", help="only this batch_code")
    ap.add_argument("--limit", type=int, help="max files (smoke test)")
    ap.add_argument("--prefix", default="", help="key prefix, e.g. 'test/' for a throwaway smoke test")
    ap.add_argument("--rehost-all", action="store_true", help="upload every non-xpt file, not just the gap")
    args = ap.parse_args()

    env = _r2.load_env()
    s3 = _r2.client(env)
    bucket = env["R2_BUCKET"]
    ledger = _r2.load_ledger()

    have_individual = set(json.load(open(INDIVIDUAL))) if INDIVIDUAL.exists() else set()
    toc = _rows(json.load(open(TOC)))

    jobs = []
    for m in toc:
        name = m["member_name"]
        base = os.path.basename(name)
        if base.lower().endswith(".xpt"):
            continue
        if args.batch and m.get("batch_code") != args.batch:
            continue
        if not args.rehost_all and base in have_individual:
            continue  # phmpt already has a link — leave it canonical
        jobs.append(m)
    if args.limit:
        jobs = jobs[: args.limit]

    print(f"{len(jobs)} candidate files ({'rehost-all' if args.rehost_all else 'fill-gap'}"
          f"{', prefix=' + args.prefix if args.prefix else ''})")

    zip_cache: dict[str, zipfile.ZipFile] = {}
    uploaded = skipped = errors = 0
    for m in tqdm(jobs, desc="upload"):
        regkey = f"{m['zip_source']}||{m['member_name']}"
        prior = ledger["individual"].get(regkey)
        if prior and prior.get("bytes") == (m.get("uncompressed_size") or 0) and prior.get("key", "").startswith(args.prefix):
            skipped += 1
            continue
        zpath = ZIPS / m["batch_code"] / m["zip_source"]
        if not zpath.exists():
            errors += 1
            continue
        try:
            if m["zip_source"] not in zip_cache:
                zip_cache[m["zip_source"]] = zipfile.ZipFile(zpath)
            data = zip_cache[m["zip_source"]].read(m["member_name"])
        except Exception:
            errors += 1
            continue
        base = os.path.basename(m["member_name"])
        key = f"{args.prefix}files/{m['batch_code']}/{_r2.sha256_bytes(data)[:12]}-{_r2.safe_name(base)}"
        try:
            s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType=_r2.content_type(base))
        except Exception as e:
            errors += 1
            tqdm.write(f"upload failed {base}: {type(e).__name__}")
            continue
        ledger["individual"][regkey] = {
            "key": key, "public_url": _r2.public_url(env, key),
            "basename": base, "bytes": len(data),
        }
        uploaded += 1
        if uploaded % 100 == 0:
            _r2.save_ledger(ledger)
    _r2.save_ledger(ledger)
    print(f"uploaded {uploaded} | skipped {skipped} | errors {errors}")
    print(f"ledger: {_r2.LEDGER}")


if __name__ == "__main__":
    main()
