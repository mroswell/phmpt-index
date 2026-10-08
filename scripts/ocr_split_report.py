"""Report OCR split-word rejoin candidates across the full-text.

Finds adjacent fragment pairs that merge into a real word where at least one
fragment is NOT itself a word (so legitimate two-word phrases are excluded):
  Injec + ion  -> injection      TREATME + NT -> treatment
  DIARR + HEA  -> diarrhea        Sub + ect    -> subject
Ranks candidates by how often they occur, so we can decide which joins to
apply as an index-time normalization (no re-OCR).

    uv run python scripts/ocr_split_report.py --sample 5

Outputs: data/ocr_split_report.md  and  data/ocr_split_candidates.json
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FULLTEXT = ROOT / "data" / "fulltext"
REPORT_MD = ROOT / "data" / "ocr_split_report.md"
CAND_JSON = ROOT / "data" / "ocr_split_candidates.json"
TOKEN_RE = re.compile(r"[A-Za-z]+")


def load_dict() -> set[str]:
    for p in ("/usr/share/dict/words", "/usr/dict/words"):
        fp = Path(p)
        if fp.exists():
            return {w.strip().lower() for w in fp.read_text(errors="ignore").splitlines()
                    if len(w.strip()) >= 2}
    return set()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=5, help="scan 1 in N pages")
    ap.add_argument("--min-count", type=int, default=5, help="min occurrences to report")
    args = ap.parse_args()
    DICT = load_dict()
    if not DICT:
        raise SystemExit("no system word list found")
    print(f"dict words: {len(DICT):,} | sampling 1 in {args.sample}")

    joined = Counter()                         # canonical joined word -> occurrences
    pairs = defaultdict(Counter)               # joined -> Counter of raw "A+B" forms
    total = 0

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
                toks = TOKEN_RE.findall(json.loads(line).get("text", "") or "")
                lc = [t.lower() for t in toks]
                for j in range(len(lc) - 1):
                    a, b = lc[j], lc[j + 1]
                    if len(a) >= 2 and len(b) <= 4 and len(a) + len(b) >= 5:
                        w = a + b
                        if w in DICT and not (a in DICT and b in DICT):
                            joined[w] += 1
                            pairs[w][f"{toks[j]}+{toks[j+1]}"] += 1
                            total += 1
        print(f"  scanned {fp.name}")

    ranked = joined.most_common()
    keep = [(w, n) for w, n in ranked if n >= args.min_count]
    CAND_JSON.write_text(json.dumps({
        "sample": args.sample, "total_merge_occurrences": total,
        "distinct_joined_words": len(joined),
        "candidates": [{"word": w, "count": n, "example": pairs[w].most_common(1)[0][0]}
                       for w, n in keep],
    }, indent=1))

    lines = [
        "# OCR split-word rejoin candidates", "",
        f"- Sampling: 1 in {args.sample} pages",
        f"- Total split occurrences found: **{total:,}**",
        f"- Distinct joined words: **{len(joined):,}**  (showing those ≥{args.min_count})", "",
        "| joined word | count | example split |", "|:--|---:|:--|",
    ]
    for w, n in keep[:300]:
        lines.append(f"| {w} | {n:,} | `{pairs[w].most_common(1)[0][0]}` |")
    REPORT_MD.write_text("\n".join(lines) + "\n")
    print(f"\n{total:,} occurrences; {len(joined):,} distinct joins; {len(keep):,} ≥{args.min_count}")
    print(f"wrote {REPORT_MD.relative_to(ROOT)} and {CAND_JSON.relative_to(ROOT)}")
    print("top 25:", [w for w, _ in ranked[:25]])


if __name__ == "__main__":
    main()
