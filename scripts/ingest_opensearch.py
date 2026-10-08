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

# OCR split-word rejoin: fold curated splits ("TREATME NT" -> "TREATMENT") so
# search finds them. Curated list from scripts/ocr_split_report.py review.
SPLIT_APPROVED_FILE = DATA / "ocr_split_approved.json"
TOKEN_RE = re.compile(r"[A-Za-z]+")
try:
    SPLIT_APPROVED = set(json.load(open(SPLIT_APPROVED_FILE)).get("words", []))
except Exception:
    SPLIT_APPROVED = set()


def rejoin_splits(text: str) -> str:
    """Merge adjacent fragments split across whitespace when their lowercased
    concatenation is an approved join (Injec ion -> injection). Only touches
    those specific gaps; all other text/formatting is preserved."""
    if not SPLIT_APPROVED or not text:
        return text
    toks = [(m.group(), m.start(), m.end()) for m in TOKEN_RE.finditer(text)]
    out, last, j = [], 0, 0
    while j < len(toks) - 1:
        a, _as, ae = toks[j]
        b, bs, _be = toks[j + 1]
        gap = text[ae:bs]
        if gap and not gap.strip() and len(a) >= 2 and len(b) <= 4 \
                and len(a) + len(b) >= 5 and (a + b).lower() in SPLIT_APPROVED:
            out.append(text[last:ae])   # keep 'a', drop the whitespace gap
            last = bs
            j += 2
        else:
            j += 1
    out.append(text[last:])
    return "".join(out)

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


def doc_stream(meta: dict, batch: str | None, index_name: str = INDEX_NAME):
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
                text = rejoin_splits(rec.get("text", ""))
                body = {"doc_id": rec["doc_id"], "page": rec["page"], "text": text,
                        "markers": sorted({mk.upper() for mk in MARKER_RE.findall(text)})}
                body.update(m)
                yield {"_index": index_name, "_id": f'{rec["doc_id"]}:{rec["page"]}', "_source": body}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", help="only this batch_code's jsonl")
    ap.add_argument("--index", default=INDEX_NAME, help="target index name (default foia_pages)")
    ap.add_argument("--dry-run", action="store_true", help="build docs, print a sample, do not connect")
    ap.add_argument("--batch-size", type=int, default=2000)
    args = ap.parse_args()
    index_name = args.index

    meta = build_meta()
    print(f"metadata for {len(meta):,} documents loaded")

    if args.dry_run:
        n = 0
        for d in doc_stream(meta, args.batch, index_name):
            if n < 3:
                sample = dict(d["_source"])
                sample["text"] = sample["text"][:160] + ("…" if len(sample["text"]) > 160 else "")
                print(json.dumps({"_id": d["_id"], "_source": sample}, ensure_ascii=False, indent=2))
            n += 1
        print(f"\nDRY RUN: {n:,} page-documents would be indexed into '{index_name}'. No cluster contacted.")
        return

    from opensearchpy import OpenSearch, helpers

    env = load_env()
    url = env.get("SEARCH_URL") or os.environ.get("SEARCH_URL")
    if not url:
        raise SystemExit("SEARCH_URL missing — create .search.env (see search/ docs).")
    client = OpenSearch([url], timeout=60, max_retries=3, retry_on_timeout=True)

    mapping = json.load(open(MAPPING))
    if not client.indices.exists(index=index_name):
        client.indices.create(index=index_name, body=mapping)
        print(f"created index '{index_name}'")
    # speed up bulk load: no replicas, no refresh
    client.indices.put_settings(index=index_name,
                                body={"index": {"number_of_replicas": 0, "refresh_interval": "-1"}})
    ok, errs = helpers.bulk(client, doc_stream(meta, args.batch, index_name),
                            chunk_size=args.batch_size, raise_on_error=False, stats_only=False)
    print(f"indexed {ok:,} docs; {len(errs)} errors")
    # restore production settings + make searchable
    client.indices.put_settings(index=index_name,
                                body={"index": {"number_of_replicas": 1, "refresh_interval": "30s"}})
    client.indices.refresh(index=index_name)
    print(f"count now: {client.count(index=index_name)['count']:,}")


if __name__ == "__main__":
    main()
