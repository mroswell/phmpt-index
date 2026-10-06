"""Bundle the .xpt SAS datasets into per-company/per-study zips on R2.

.xpt files aren't viewable in a browser, so instead of individual links they're
grouped into one downloadable zip per (company, study) and uploaded to:
    datasets/{company}/{study}.zip   (stable key, overwritten in place)

Resumable per group via a members fingerprint in data/r2_uploads.json. A group is
rebuilt+reuploaded only if its membership changed. Builds each zip in tmp/xpt_bundles/
(gitignored) one at a time and deletes it after upload to save disk.

    uv run python scripts/bundle_xpt_datasets.py --study bimo         # one small group (test)
    uv run python scripts/bundle_xpt_datasets.py                      # all groups
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path

try:
    import zipfile_deflate64 as zipfile
except Exception:
    import zipfile

from boto3.s3.transfer import TransferConfig
from tqdm import tqdm

import _r2

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TOC = DATA / "toc.json"
ZIPS = DATA / "zips"
TMP = ROOT / "tmp" / "xpt_bundles"

STUDY_PATTERNS = [
    re.compile(r"c4591\d{3}", re.I),
    re.compile(r"bnt162-?\d+", re.I),
    re.compile(r"mrna-1273-p\d{3}(?:-add\d+)?", re.I),
    re.compile(r"\bp\d{3}\b", re.I),
    re.compile(r"bimo", re.I),
]


def company_of(batch_code: str) -> str:
    return "moderna" if (batch_code or "").startswith("md") else "pfizer"


def study_of(name: str) -> str:
    for pat in STUDY_PATTERNS:
        m = pat.search(name or "")
        if m:
            return m.group(0).lower().replace(" ", "")
    return "misc"


def _rows(obj):
    return obj if isinstance(obj, list) else next(v for v in obj.values() if isinstance(v, list))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--study", help="only groups whose study slug matches")
    ap.add_argument("--company", help="only this company (moderna|pfizer)")
    ap.add_argument("--prefix", default="", help="key prefix for a throwaway test, e.g. 'test/'")
    args = ap.parse_args()

    env = _r2.load_env()
    s3 = _r2.client(env)
    bucket = env["R2_BUCKET"]
    ledger = _r2.load_ledger()
    TMP.mkdir(parents=True, exist_ok=True)

    groups: dict[tuple, list] = {}
    for m in _rows(json.load(open(TOC))):
        if not os.path.basename(m["member_name"]).lower().endswith(".xpt"):
            continue
        co, st = company_of(m.get("batch_code")), study_of(m["member_name"])
        if args.company and co != args.company:
            continue
        if args.study and st != args.study:
            continue
        groups.setdefault((co, st), []).append(m)

    print(f"{len(groups)} (company, study) groups; "
          f"{sum(len(v) for v in groups.values())} .xpt members")
    xfer = TransferConfig(multipart_threshold=64 * 1024**2, multipart_chunksize=256 * 1024**2,
                          max_concurrency=4)

    for (co, st), members in sorted(groups.items()):
        regkey = f"{co}||{st}"
        fp = hashlib.sha256(
            "\n".join(sorted(f"{m['zip_source']}/{m['member_name']}:{m.get('uncompressed_size',0)}"
                             for m in members)).encode()).hexdigest()
        prior = ledger["datasets"].get(regkey)
        if prior and prior.get("members_fingerprint") == fp and prior.get("key", "").startswith(args.prefix):
            print(f"  skip {co}/{st} ({len(members)} members, unchanged)")
            continue

        key = f"{args.prefix}datasets/{co}/{st}.zip"
        out = TMP / f"{co}_{st}.zip"
        total = 0
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as oz:
            zc: dict[str, zipfile.ZipFile] = {}
            for m in tqdm(members, desc=f"{co}/{st}", leave=False):
                zp = ZIPS / m["batch_code"] / m["zip_source"]
                if not zp.exists():
                    continue
                if m["zip_source"] not in zc:
                    zc[m["zip_source"]] = zipfile.ZipFile(zp)
                data = zc[m["zip_source"]].read(m["member_name"])
                oz.writestr(os.path.basename(m["member_name"]), data)
                total += len(data)
        s3.upload_file(str(out), bucket, key,
                       ExtraArgs={"ContentType": "application/zip"}, Config=xfer)
        ledger["datasets"][regkey] = {
            "key": key, "public_url": _r2.public_url(env, key),
            "member_count": len(members), "members_fingerprint": fp, "bytes": out.stat().st_size,
        }
        _r2.save_ledger(ledger)
        out.unlink(missing_ok=True)
        print(f"  uploaded {co}/{st}: {len(members)} members, {total/1024**2:.0f} MB raw -> {key}")

    print(f"ledger: {_r2.LEDGER}")


if __name__ == "__main__":
    main()
