"""Bulk-index the extracted page text into OpenSearch (Bonsai).

Reads page text from data/fulltext/*.jsonl, joins per-document metadata from
docs/data/index.json by doc_id, and bulk-indexes one OpenSearch document per
page (_id = "{doc_id}:{page}"). Deterministic ids make re-runs idempotent.

Connection comes from .search.env (gitignored):
    SEARCH_ENGINE=opensearch
    SEARCH_URL=https://KEY:SECRET@cluster-xxxx.bonsaisearch.net:443

    uv run python scripts/ingest_opensearch.py --dry-run         # no cluster; print sample docs
    uv run python scripts/ingest_opensearch.py --batch p1215d-eua  # index one batch (test)
    uv run python scripts/ingest_opensearch.py                    # full load
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
INDEX = ROOT / "docs" / "data" / "index.json"
FULLTEXT = DATA / "fulltext"
MAPPING = ROOT / "search" / "opensearch_mapping.json"
ENV = ROOT / ".search.env"
INDEX_NAME = "foia_pages"

MARKER_RE = re.compile(r"\(b\)\(\d+\)(?:\([A-F]\))?", re.I)

STUDY_PATTERNS = [
    re.compile(r"c4591\d{3}", re.I),
    re.compile(r"bnt162-?\d+", re.I),
    re.compile(r"mrna-1273-p\d{3}(?:-add\d+)?", re.I),
    re.compile(r"\bp\d{3}\b", re.I),
    re.compile(r"bimo", re.I),
]


def study_of(filename: str) -> str:
    for pat in STUDY_PATTERNS:
        m = pat.search(filename or "")
        if m:
            return m.group(0).lower()
    return "misc"


def load_env() -> dict:
    env = {}
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def _rows(obj):
    return obj if isinstance(obj, list) else next(v for v in obj.values() if isinstance(v, list))


def build_meta() -> dict:
    meta = {}
    for r in _rows(json.load(open(INDEX))):
        url = r.get("individual_url") or r.get("hosted_url") or r.get("ican_url") or r.get("zip_url")
        meta[r["id"]] = {
            "filename": r.get("filename"),
            "company": r.get("company"),
            "license": r.get("license"),
            "age_group": r.get("age_group"),
            "module": r.get("module"),
            "study": study_of(r.get("filename") or ""),
            "batch_code": r.get("batch_code"),
            "bates_start": r.get("bates_start"),
            "bates_end": r.get("bates_end"),
            "zip_source": r.get("zip_source"),
            "total_pages": r.get("page_count"),
            "url": url,
            "bates_text": " ".join(str(x) for x in (r.get("bates_start"), r.get("bates_end"),
                                                     r.get("filename")) if x),
        }
    return meta


def doc_stream(meta: dict, batch: str | None):
    files = sorted(FULLTEXT.glob("*.jsonl"))
    if batch:
        files = [FULLTEXT / f"{batch}.jsonl"]
    for fp in files:
        if not fp.exists():
            continue
        with open(fp, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                m = meta.get(rec["doc_id"])
                if not m:
                    continue
                text = rec.get("text", "")
                body = {"doc_id": rec["doc_id"], "page": rec["page"], "text": text,
                        "markers": sorted({mk.upper() for mk in MARKER_RE.findall(text)})}
                body.update(m)
                yield {"_index": INDEX_NAME, "_id": f'{rec["doc_id"]}:{rec["page"]}', "_source": body}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", help="only this batch_code's jsonl")
    ap.add_argument("--dry-run", action="store_true", help="build docs, print a sample, do not connect")
    ap.add_argument("--batch-size", type=int, default=2000)
    args = ap.parse_args()

    meta = build_meta()
    print(f"metadata for {len(meta):,} documents loaded")

    if args.dry_run:
        n = 0
        for d in doc_stream(meta, args.batch):
            if n < 3:
                sample = dict(d["_source"])
                sample["text"] = sample["text"][:160] + ("…" if len(sample["text"]) > 160 else "")
                print(json.dumps({"_id": d["_id"], "_source": sample}, ensure_ascii=False, indent=2))
            n += 1
        print(f"\nDRY RUN: {n:,} page-documents would be indexed into '{INDEX_NAME}'. No cluster contacted.")
        return

    from opensearchpy import OpenSearch, helpers

    env = load_env()
    url = env.get("SEARCH_URL") or os.environ.get("SEARCH_URL")
    if not url:
        raise SystemExit("SEARCH_URL missing — create .search.env (see search/ docs).")
    client = OpenSearch([url], timeout=60, max_retries=3, retry_on_timeout=True)

    mapping = json.load(open(MAPPING))
    if not client.indices.exists(index=INDEX_NAME):
        client.indices.create(index=INDEX_NAME, body=mapping)
        print(f"created index '{INDEX_NAME}'")
    # speed up bulk load: no replicas, no refresh
    client.indices.put_settings(index=INDEX_NAME,
                                body={"index": {"number_of_replicas": 0, "refresh_interval": "-1"}})
    ok, errs = helpers.bulk(client, doc_stream(meta, args.batch),
                            chunk_size=args.batch_size, raise_on_error=False, stats_only=False)
    print(f"indexed {ok:,} docs; {len(errs)} errors")
    # restore production settings + make searchable
    client.indices.put_settings(index=INDEX_NAME,
                                body={"index": {"number_of_replicas": 1, "refresh_interval": "30s"}})
    client.indices.refresh(index=INDEX_NAME)
    print(f"count now: {client.count(index=INDEX_NAME)['count']:,}")


if __name__ == "__main__":
    main()
