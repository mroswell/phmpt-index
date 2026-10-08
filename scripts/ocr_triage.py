"""Score the OCR/text quality of the indexed full-text, to triage re-OCR.

The search text in data/fulltext/*.jsonl is mostly the PDFs' own embedded text
layer (see extract_fulltext.py). Much of it is the FDA's own poor OCR
("Injec ion site", "Sub ect unab e to comply", "DIARR HEA") — present, so it
was never re-OCR'd. This script scores each page and reports the worst files so
a re-OCR pilot can target them.

Heuristics per page (on alphabetic tokens):
  - short_ratio : share of 1-2 char tokens (spaced-out / broken words)
  - dict_ratio  : share of >=3 char tokens found in the system word list
A page is "bad" if dict_ratio < DICT_MIN (when a dict is available) or
short_ratio > SHORT_MAX. Pages with very few tokens are "sparse" (image-only /
near-empty) and scored separately.

    uv run python scripts/ocr_triage.py --sample 10   # 1-in-10 pages (fast)
    uv run python scripts/ocr_triage.py               # every page (slow)

Outputs: data/ocr_triage_report.md  and  data/ocr_triage.json
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FULLTEXT = ROOT / "data" / "fulltext"
INDEX = ROOT / "docs" / "data" / "index.json"
REPORT_MD = ROOT / "data" / "ocr_triage_report.md"
REPORT_JSON = ROOT / "data" / "ocr_triage.json"

TOKEN_RE = re.compile(r"[A-Za-z]+")
MIN_TOKENS = 25        # fewer => "sparse" (image-only / near-empty), scored apart
GARBLE_MIN = 0.03      # > this share of words that are OCR-split => bad


def load_dict() -> set[str]:
    for p in ("/usr/share/dict/words", "/usr/dict/words"):
        fp = Path(p)
        if fp.exists():
            return {w.strip().lower() for w in fp.read_text(errors="ignore").splitlines()
                    if len(w.strip()) >= 3}
    return set()


def load_filenames() -> dict[int, str]:
    rows = json.load(open(INDEX))
    rows = rows if isinstance(rows, list) else next(v for v in rows.values() if isinstance(v, list))
    return {r["id"]: r.get("filename", "") for r in rows}


def score_page(text: str, DICT: set[str]):
    """Detect OCR word-splitting: adjacent fragments that merge into a real
    word (Injec+ion -> injection, Sub+ect -> subject), where at least one
    fragment is not itself a word. Ignores clean code/ID-dense pages."""
    toks = TOKEN_RE.findall(text or "")
    n = len(toks)
    if n < MIN_TOKENS:
        return "sparse", 0.0
    if not DICT:
        return "ok", 0.0
    lc = [t.lower() for t in toks]
    words = sum(1 for t in lc if len(t) >= 3)
    merges = 0
    for i in range(len(lc) - 1):
        a, b = lc[i], lc[i + 1]
        if len(a) >= 2 and len(b) <= 4 and len(a) + len(b) >= 5:
            if (a + b) in DICT and not (a in DICT and b in DICT):
                merges += 1
    garble = merges / words if words else 0.0
    return ("bad" if garble > GARBLE_MIN else "ok"), garble


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=10, help="score 1 in N pages (default 10)")
    args = ap.parse_args()

    DICT = load_dict()
    names = load_filenames()
    print(f"dictionary words: {len(DICT):,}  |  sampling 1 in {args.sample} pages")

    per_doc = defaultdict(lambda: {"ok": 0, "bad": 0, "sparse": 0})
    tot = {"ok": 0, "bad": 0, "sparse": 0}
    worst_snips = []

    for fp in sorted(FULLTEXT.glob("*.jsonl")):
        i = 0
        with open(fp, encoding="utf-8") as fh:
            for line in fh:
                i += 1
                if args.sample > 1 and (i % args.sample):
                    continue
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                cls, garble = score_page(r.get("text", ""), DICT)
                per_doc[r["doc_id"]][cls] += 1
                tot[cls] += 1
                if cls == "bad" and len(worst_snips) < 15 and garble > 0.06:
                    worst_snips.append((names.get(r["doc_id"], str(r["doc_id"])), r.get("page"),
                                        round(garble, 3), (r.get("text", "") or "")[:160].replace("\n", " ")))
        print(f"  scanned {fp.name}")

    scored = tot["ok"] + tot["bad"]
    bad_pct = 100 * tot["bad"] / scored if scored else 0
    # worst documents: among those with enough scored text pages, by bad ratio
    docs = []
    for did, c in per_doc.items():
        s = c["ok"] + c["bad"]
        if s >= 5:
            docs.append((c["bad"] / s, s, did, names.get(did, str(did))))
    docs.sort(reverse=True)
    worst = [{"doc_id": d, "filename": fn, "bad_ratio": round(br, 3), "scored_pages": s}
             for br, s, d, fn in docs[:40]]

    REPORT_JSON.write_text(json.dumps({
        "sample": args.sample,
        "pages_scored": scored, "pages_sparse": tot["sparse"],
        "bad_pages": tot["bad"], "bad_pct": round(bad_pct, 1),
        "worst_docs": worst,
    }, indent=1))

    lines = [
        "# OCR triage report", "",
        f"- Sampling: 1 in {args.sample} pages",
        f"- Pages scored (has real text): **{scored:,}**",
        f"- Sparse pages (image-only / near-empty, handled by OCR fallback): {tot['sparse']:,}",
        f"- **Bad pages: {tot['bad']:,} ({bad_pct:.1f}% of scored)**", "",
        "## 40 worst documents (by share of bad pages, ≥5 scored pages)", "",
        "| bad% | pages | filename |", "|---:|---:|:--|",
    ]
    for w in worst:
        lines.append(f"| {w['bad_ratio']*100:.0f}% | {w['scored_pages']} | {w['filename'][:80]} |")
    lines += ["", "## Sample bad-page snippets", ""]
    for fn, pg, g, snip in worst_snips:
        lines.append(f"- `{fn[:70]}` p{pg} (garble {g}): {snip}")
    REPORT_MD.write_text("\n".join(lines) + "\n")

    print(f"\nscored {scored:,} pages; {tot['bad']:,} bad ({bad_pct:.1f}%); {tot['sparse']:,} sparse")
    print(f"wrote {REPORT_MD.relative_to(ROOT)} and {REPORT_JSON.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
