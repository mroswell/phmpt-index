"""Score every kept hit in docs/data/hidden_substance.json for
"follow-up FOIA value" and select the top 100 via stratified sampling
so the report covers multiple substance categories and marker types
— not just the dominant (b)(4)-in-manufacturing cluster.

Scoring (per hit):
  +30  substance_category is Manufacturing / Pharmacology / Clinical safety
  +25  marker is rare  (anything except (b)(4) — (b)(4) is 99% of the
       corpus, so rare markers are inherently more interesting)
  +20  likely_table  (one redaction probably hides many values)
  +15  (b)(4) marker in a CMC document  (trade-secret claim on ingredients)
  +15  (b)(5) marker in any context (deliberative-process exemption)
  +10  module is M3 or M4  (quality / nonclinical — most substance-rich)
  +10  Bates range captured  (enables precise citation in a follow-up letter)
  +10  section_heading present  (gives the reader an anchor)
  -20  context contains "court" / "v." / "Plaintiff"  (likely a court
       document citing the FOIA statute, not a substantive redaction)

Selection: stratified across substance categories + a "rare markers"
bucket, with a max-per-file cap so coverage spreads across documents.

Output:
  docs/data/foia_followup_targets.json
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "docs" / "data"
IN_PATH = ROOT / "data" / "hidden_substance_full.json"
OUT_PATH = WEB / "foia_followup_targets.json"

TOP_N = 100
MAX_PER_FILE = 5

SUBSTANCE_HIGH_VALUE = {
    "Manufacturing / CMC",
    "Pharmacology / toxicology",
    "Clinical safety / adverse events",
}

# Per-bucket allocations summing to TOP_N. Buckets are evaluated in
# order; if a bucket runs out of candidates its remaining slots roll
# over into "Manufacturing / CMC" (the dominant bucket).
STRATA = [
    # (label, predicate, slots)
    ("Rare markers (anything not (b)(4))", lambda h: h["marker_canonical"] != "(b)(4)", 15),
    ("Manufacturing / CMC", lambda h: h["substance_category"] == "Manufacturing / CMC", 25),
    ("Pharmacology / toxicology", lambda h: h["substance_category"] == "Pharmacology / toxicology", 15),
    ("Clinical safety / adverse events", lambda h: h["substance_category"] == "Clinical safety / adverse events", 15),
    ("Trial design / efficacy", lambda h: h["substance_category"] == "Trial design / efficacy", 10),
    ("Inspection / enforcement", lambda h: h["substance_category"] == "Inspection / enforcement", 10),
    ("Trade-secret claim", lambda h: h["substance_category"] == "Trade-secret claim", 5),
    ("Internal FDA deliberation", lambda h: h["substance_category"] == "Internal FDA deliberation", 5),
]

COURT_DOC_RE = re.compile(r"\b(court|plaintiff|defendant|magistrate|civil action|v\.\s+[A-Z])\b",
                          re.IGNORECASE)


def score_hit(h: dict) -> tuple[int, list[str]]:
    """Return (score, list of reason strings)."""
    score = 0
    reasons: list[str] = []

    cat = h.get("substance_category")
    if cat in SUBSTANCE_HIGH_VALUE:
        score += 30
        reasons.append(f"high-value category: {cat}")

    if h.get("likely_table"):
        score += 20
        reasons.append(f"likely table cluster ({h.get('nearby_marker_count', 0)} markers nearby)")

    marker = h.get("marker_canonical")
    if marker != "(b)(4)":
        score += 25
        reasons.append(f"rare marker ({marker})")
    if marker == "(b)(4)" and cat == "Manufacturing / CMC":
        score += 15
        reasons.append("(b)(4) trade-secret claim in manufacturing context")
    if marker == "(b)(5)":
        score += 15
        reasons.append("(b)(5) internal deliberation")

    if h.get("module") in {"M3", "M4"}:
        score += 10
        reasons.append(f"high-substance module ({h['module']})")

    if h.get("bates"):
        score += 10
        reasons.append(f"Bates citable ({h['bates']})")

    if h.get("section_heading"):
        score += 10
        reasons.append("section heading anchor present")

    ctx = h.get("context", "")
    if COURT_DOC_RE.search(ctx):
        score -= 20
        reasons.append("context looks like court-document FOIA citation (-20)")

    return score, reasons


def main() -> None:
    if not IN_PATH.exists():
        raise SystemExit(f"{IN_PATH} missing — run extract_hidden_substance.py first")

    doc = json.loads(IN_PATH.read_text())
    hits = doc["hits"]
    print(f"Scoring {len(hits):,} kept hits...")

    scored = []
    for i, h in enumerate(hits):
        score, reasons = score_hit(h)
        scored.append({"_score": score, "_score_reasons": reasons, "_idx": i, **h})

    scored.sort(key=lambda x: -x["_score"])

    # Stratified selection: fill each bucket's quota, then roll over
    # remaining slots into the dominant bucket.
    selected: list[dict] = []
    selected_idxs: set[int] = set()
    per_file: Counter = Counter()
    rolled_over_slots = 0

    for label, pred, quota in STRATA:
        added_for_bucket = 0
        for h in scored:
            if added_for_bucket >= quota:
                break
            if h["_idx"] in selected_idxs:
                continue
            if not pred(h):
                continue
            if per_file[h["filename"]] >= MAX_PER_FILE:
                continue
            selected.append({**h, "_bucket": label})
            selected_idxs.add(h["_idx"])
            per_file[h["filename"]] += 1
            added_for_bucket += 1
        if added_for_bucket < quota:
            rolled_over_slots += (quota - added_for_bucket)

    # Roll over: fill remaining from any high-value category, sorted by score
    if rolled_over_slots > 0:
        for h in scored:
            if rolled_over_slots <= 0:
                break
            if h["_idx"] in selected_idxs:
                continue
            if h["substance_category"] not in SUBSTANCE_HIGH_VALUE:
                continue
            if per_file[h["filename"]] >= MAX_PER_FILE:
                continue
            selected.append({**h, "_bucket": "Roll-over (high-value)"})
            selected_idxs.add(h["_idx"])
            per_file[h["filename"]] += 1
            rolled_over_slots -= 1

    # Final sort: by score desc within each bucket, buckets in STRATA order
    bucket_order = {label: i for i, (label, _, _) in enumerate(STRATA)}
    bucket_order["Roll-over (high-value)"] = len(STRATA)
    selected.sort(key=lambda h: (bucket_order.get(h["_bucket"], 99), -h["_score"]))

    # Score distribution of the selected set
    score_dist = Counter(h["_score"] for h in selected)
    cat_dist = Counter(h["substance_category"] for h in selected)
    marker_dist = Counter(h["marker_canonical"] for h in selected)
    mod_dist = Counter(h.get("module") or "?" for h in selected)
    bucket_dist = Counter(h["_bucket"] for h in selected)

    out_doc = {
        "summary": {
            "scoring_rubric": {
                "high_value_category": 30,
                "rare_marker": 25,
                "likely_table": 20,
                "b4_in_manufacturing": 15,
                "b5_deliberation": 15,
                "module_M3_or_M4": 10,
                "bates_present": 10,
                "section_heading_present": 10,
                "court_doc_penalty": -20,
            },
            "strata_quotas": {label: quota for label, _, quota in STRATA},
            "total_scored": len(hits),
            "top_n": len(selected),
            "max_per_file": MAX_PER_FILE,
            "bucket_distribution": dict(bucket_dist.most_common()),
            "score_distribution_top_n": dict(sorted(score_dist.items(), reverse=True)),
            "category_distribution_top_n": dict(cat_dist.most_common()),
            "marker_distribution_top_n": dict(marker_dist.most_common()),
            "module_distribution_top_n": dict(mod_dist.most_common()),
        },
        "targets": selected,
    }
    OUT_PATH.write_text(json.dumps(out_doc, indent=2))

    print(f"Wrote: {OUT_PATH}")
    print()
    print(f"Top {len(selected)} targets")
    scores = sorted([h["_score"] for h in selected])
    print(f"  Score range: {scores[0]} - {scores[-1]} (median {scores[len(scores)//2]})")
    print(f"  Unique files: {len(per_file)}")
    print()
    print("By stratum bucket:")
    for b, n in bucket_dist.most_common():
        print(f"  {b:<45} {n}")
    print()
    print("By category:")
    for c, n in cat_dist.most_common():
        print(f"  {c:<40} {n}")
    print()
    print("By marker:")
    for m, n in marker_dist.most_common():
        print(f"  {m:<14} {n}")
    print()
    print("By module:")
    for mod, n in mod_dist.most_common():
        print(f"  {mod:<6} {n}")


if __name__ == "__main__":
    main()
