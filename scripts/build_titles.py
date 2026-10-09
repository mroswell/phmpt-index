"""Build docs/data/titles.json — per-document title + citation metadata used
by the Cite button.

Three sources, in priority order:
  1. Scraped titles from coviddocuments.com (data/external/cd-tagged-files/*.json),
     matched to index.json by filename basename.
  2. (optional) Claude-generated titles for untitled PDFs, read from the page
     text we already have in data/fulltext/*.jsonl. Requires ANTHROPIC_API_KEY
     and the `anthropic` package; resumable via data/cache/titles_claude.json.
  3. Clean filename-derived titles for everything still untitled (data files,
     or PDFs when the Claude step hasn't run).

    uv run python scripts/build_titles.py                 # no API: scraped + filename
    uv run python scripts/build_titles.py --claude        # + Claude-title untitled PDFs
    uv run python scripts/build_titles.py --claude --limit 20   # smoke test

Output: docs/data/titles.json  = { "<id>": {title, document_type?, date?, people?, src} }
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "docs" / "data" / "index.json"
SCRAPED = ROOT / "data" / "external" / "cd-tagged-files"
FULLTEXT = ROOT / "data" / "fulltext"
OUT = ROOT / "docs" / "data" / "titles.json"
CLAUDE_CACHE = ROOT / "data" / "cache" / "titles_claude.json"
MODEL = "claude-haiku-4-5"


def rows_of(obj):
    return obj if isinstance(obj, list) else next(v for v in obj.values() if isinstance(v, list))


def filename_title(filename: str) -> str:
    """Readable title from a filename: drop extension, underscores→spaces."""
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", os.path.basename(filename or ""))
    return re.sub(r"\s+", " ", stem.replace("_", " ")).strip()


def load_scraped() -> dict:
    m = {}
    for f in glob.glob(str(SCRAPED / "*.json")):
        for r in json.load(open(f)):
            fn = (r.get("filename") or "").strip()
            if fn:
                m[os.path.basename(fn).lower()] = r
    return m


def first_page_text(doc_ids: set[int], max_chars: int = 2500) -> dict:
    """Stream fulltext once; collect the earliest page's text per needed doc_id."""
    best = {}
    for fp in sorted(FULLTEXT.glob("*.jsonl")):
        with open(fp, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                did = r.get("doc_id")
                if did in doc_ids and (did not in best or r.get("page", 1) < best[did][0]):
                    best[did] = (r.get("page", 1), (r.get("text") or "")[:max_chars])
    return {d: t for d, (_, t) in best.items()}


def claude_titles(pending, text_by_doc, limit):
    """Title the pending PDF rows with Claude. pending: list of (id, filename, doc_id).
    Returns dict id->{title,document_type,date,people}. Resumable via cache."""
    import anthropic  # lazy: only needed for --claude
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        env = ROOT / ".search.env"
        if env.exists():
            for ln in env.read_text().splitlines():
                if ln.startswith("ANTHROPIC_API_KEY="):
                    key = ln.split("=", 1)[1].strip()
    if not key:
        raise SystemExit("ANTHROPIC_API_KEY not set (env or .search.env) — needed for --claude")
    client = anthropic.Anthropic(api_key=key)
    cache = json.loads(CLAUDE_CACHE.read_text()) if CLAUDE_CACHE.exists() else {}

    todo = [p for p in pending if str(p[0]) not in cache][: (limit or len(pending))]
    print(f"claude: {len(todo)} to title ({len(cache)} cached)")
    for n, (rid, filename, did) in enumerate(todo, 1):
        content = text_by_doc.get(did, "")
        prompt = (
            "Give a concise document title (3-7 words), a document type (one or two "
            "words), and a date (YYYY-MM-DD / YYYY-MM / YYYY, else \"undated\") for this "
            "FDA COVID-19 vaccine FOIA document. Return ONLY JSON: "
            '{"title":"...","document_type":"...","date":"..."}\n\n'
            f"Filename: {filename}\nContent excerpt:\n{content}"
        )
        try:
            resp = client.messages.create(model=MODEL, max_tokens=200,
                                          messages=[{"role": "user", "content": prompt}])
            txt = resp.content[0].text.strip()
            txt = txt[txt.index("{"): txt.rindex("}") + 1]
            d = json.loads(txt)
            cache[str(rid)] = {"title": d.get("title") or filename_title(filename),
                               "document_type": d.get("document_type"),
                               "date": d.get("date")}
        except Exception as e:
            print(f"  !{rid} {filename[:40]}: {e}")
            cache[str(rid)] = {"title": filename_title(filename)}
        if n % 50 == 0:
            CLAUDE_CACHE.write_text(json.dumps(cache))
            print(f"  …{n}/{len(todo)}")
        time.sleep(0.2)
    CLAUDE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    CLAUDE_CACHE.write_text(json.dumps(cache))
    return cache


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--claude", action="store_true", help="Claude-title untitled PDFs")
    ap.add_argument("--limit", type=int, default=0, help="cap Claude calls (smoke test)")
    args = ap.parse_args()

    index = rows_of(json.load(open(INDEX)))
    scraped = load_scraped()
    print(f"index {len(index):,} | scraped titles {len(scraped):,}")

    out = {}
    pending_pdfs = []           # untitled PDFs eligible for Claude
    n_scraped = n_file = 0
    for r in index:
        rid = r["id"]
        key = os.path.basename(r.get("filename") or "").lower()
        s = scraped.get(key)
        if s and (s.get("title") or "").strip():
            out[str(rid)] = {"title": s["title"].strip(), "src": "scraped"}
            for k in ("document_type", "date"):
                if s.get(k):
                    out[str(rid)][k] = s[k]
            if s.get("people_mentioned"):
                out[str(rid)]["people"] = s["people_mentioned"]
            n_scraped += 1
        else:
            out[str(rid)] = {"title": filename_title(r.get("filename")), "src": "filename"}
            n_file += 1
            if (r.get("filename") or "").lower().endswith(".pdf"):
                pending_pdfs.append((rid, r.get("filename"), r.get("doc_id")))

    print(f"  scraped: {n_scraped:,} | filename-fallback: {n_file:,} "
          f"(of which untitled PDFs eligible for Claude: {len(pending_pdfs):,})")

    if args.claude and pending_pdfs:
        ids = {d for _, _, d in pending_pdfs if d is not None}
        print(f"loading first-page text for {len(ids):,} docs…")
        text_by_doc = first_page_text(ids)
        cache = claude_titles(pending_pdfs, text_by_doc, args.limit)
        upgraded = 0
        for rid, *_ in pending_pdfs:
            c = cache.get(str(rid))
            if c and c.get("title"):
                out[str(rid)] = {"title": c["title"], "src": "claude"}
                for k in ("document_type", "date"):
                    if c.get(k) and c[k] != "undated":
                        out[str(rid)][k] = c[k]
                upgraded += 1
        print(f"  claude-titled: {upgraded:,}")

    OUT.write_text(json.dumps(out, ensure_ascii=False))
    print(f"wrote {OUT.relative_to(ROOT)} ({len(out):,} entries, {OUT.stat().st_size//1024} KB)")


if __name__ == "__main__":
    main()
