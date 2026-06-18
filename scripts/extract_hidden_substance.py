"""Re-scan every non-CRF, non-(b)(6) FOIA redaction marker with rich
context and substance categorization, so we can characterize *what* was
hidden — not just how many markers exist.

Scope: ~68,300 markers across ~928 PDFs.

Per-marker record (kept records):
    filename, page, marker_canonical, marker_raw,
    context (1000 chars centered on the marker),
    section_heading (nearest preceding heading text),
    bates,
    likely_table (bool — 5+ markers within +/- 200 chars on same page),
    foia_classification ("foia_redaction" — the only category kept),
    signals (six-bool dict from inspect_rare_contexts.detect_signals),
    substance_category, substance_secondary,
    module, company, license

Outputs:
    docs/data/hidden_substance.json         (kept records, aggregated)
    docs/data/hidden_substance_dropped.json (audit: invalid/non-redaction)
    data/cache/hidden_substance/{file}.json (per-file cache)

Resumable: per-file cache files mean re-runs only process new/modified
files.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import fitz  # PyMuPDF
import zipfile_deflate64 as zipfile
from tqdm import tqdm

from _pdf_text import get_page_text
from inspect_rare_contexts import (
    MARKER_RE,
    categorize,
    detect_signals,
    is_structurally_valid_foia,
    normalize as normalize_marker,
)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
WEB = ROOT / "docs" / "data"
ZIPS_DIR = DATA / "zips"
INDIVIDUAL_PDF_CACHE = DATA / "cache" / "individual_pdfs"
CACHE_DIR = DATA / "cache" / "hidden_substance"
INDEX = WEB / "index.json"

# Outputs — three tiers:
#   1) Slim per-file summary (browser-friendly, ~1-2 MB)
#   2) Per-file detail JSONs (loaded on-demand by the browse page)
#   3) Full record dump (heavy; used only by the PDF generator, gitignored)
OUT_SUMMARY = WEB / "hidden_substance.json"            # ~1-2 MB
OUT_DETAIL_DIR = WEB / "hidden_substance"              # one file per PDF
OUT_DROPPED = WEB / "hidden_substance_dropped.json"    # slim audit summary
OUT_FULL = DATA / "hidden_substance_full.json"         # 80+ MB, gitignored

EXEMPTION_SOURCES = [
    "M1_exemptions", "M2_exemptions", "M3_exemptions",
    "M4_exemptions", "M5_exemptions",
    "individual_exemptions", "individual_phmpt_exemptions",
]

CONTEXT_WIDTH = 500  # chars on each side -> 1000-char window
TABLE_CLUSTER_THRESHOLD = 5
TABLE_CLUSTER_WINDOW = 200

BATES_RE = re.compile(r"FDA-CBER-\d{4}-\d{4}-\d{6,8}")

# Section-heading patterns. Tried in order; the first match wins.
# All anchored to start-of-line via the surrounding regex flags.
HEADING_PATTERNS = [
    # eCTD-style numbered sections like "5.3.6", "3.2.P.5.3", "2.7.4"
    re.compile(r"^\s*(\d+(?:\.[\dA-Z]+){1,5})(?:\s+[A-Z][^\n]{3,120})?", re.MULTILINE),
    # "Section 14.1" / "Module 2.7" / "Appendix A"
    re.compile(r"^\s*(?:Section|Module|Appendix|Part|Chapter)\s+[\dA-Z][^\n]{0,120}",
               re.MULTILINE | re.IGNORECASE),
    # ALL-CAPS heading line (10+ chars, mostly letters, not just digits)
    re.compile(r"^[A-Z][A-Z0-9 \-,&/.()'\"]{10,120}$", re.MULTILINE),
]

# Substance-taxonomy keyword bundles. Lowercased substring match against
# the 1000-char context window. Highest-scoring category wins. A
# secondary category is recorded when the next-best score is within 20%.
TAXONOMY = {
    "Manufacturing / CMC": [
        "lipid", "peg", "alc-0315", "alc-0159", "sm-102", "ionizable",
        "mrna sequence", "plasmid", "capping", "buffer", "excipient",
        "formulation", "batch", "lot release", "potency", "impurit",
        "yield", "scale-up", "scale up", "gmp", "manufacturing process",
        "manufacturing", "drug substance", "drug product", "process control",
        "specification", "stability", "release test", "active pharmaceutical",
    ],
    "Pharmacology / toxicology": [
        "biodistribution", "adme", "pharmacokinetic", "toxicity", "necrosis",
        "histopath", "glp", "repeat-dose", "repeat dose", "reproductive tox",
        "genotoxicity", "noael", "in vivo", "in vitro", "tissue distribution",
        "nonclinical",
    ],
    "Clinical safety / adverse events": [
        "adverse event", "aesi", "sae ", "serious adverse",
        "myocarditis", "pericarditis", "anaphylaxis", "narrative",
        "safety signal", "meddra", "follow-up", "death", "fatal",
        "post-marketing", "postmarketing", "post-authorization",
    ],
    "Trial design / efficacy": [
        "protocol", "randomization", "randomisation", "primary endpoint",
        "efficacy", "statistical analysis plan", "interim analysis",
        "blinding", "placebo", "study design", "inclusion criteria",
        "exclusion criteria", "endpoint",
    ],
    "Inspection / enforcement": [
        "483", "warning letter", "observation", "inspection", "audit",
        "deviation", "capa", "form fda", "establishment inspection",
    ],
    "Trade-secret claim": [
        "confidential", "proprietary", "trade secret", " cci ",
        "commercial confidential", "business confidential",
    ],
}

# Markers (b)(5) deserve a deliberation-substance bucket when the
# context suggests internal recommendation/draft language.
DELIBERATION_KEYWORDS = [
    "draft", "predecisional", "deliberative", "recommend", "opinion",
    "internal review", "memorandum", "advisory", "discussion",
]


def categorize_substance(ctx: str, marker_canonical: str) -> tuple[str, str | None, dict]:
    """Return (primary_category, secondary_category_or_None, score_dict).

    Scoring: count of distinct trigger keywords found in the lowercased
    context. Highest score wins. Secondary returned when the runner-up
    is within 20% of the winner.
    """
    lc = ctx.lower()
    scores: dict[str, int] = {}
    for category, keywords in TAXONOMY.items():
        score = sum(1 for kw in keywords if kw in lc)
        if score > 0:
            scores[category] = score

    # Special rule: (b)(5) deliberation overrides Trade-secret signals
    # because (b)(5) is fundamentally the deliberative-process exemption.
    # We weight it heavily so a (b)(5) on a memo/review page lands in
    # the deliberation bucket even if "lot release" or similar CMC
    # keywords also appear in the surrounding text.
    if marker_canonical == "(b)(5)":
        delib_score = sum(1 for kw in DELIBERATION_KEYWORDS if kw in lc)
        # Also boost when the context contains reviewer language like
        # "Reviewer's Comments", "recommendation", "concur"
        for review_marker in ["reviewer", "concur", "recommend", "memo"]:
            if review_marker in lc:
                delib_score += 1
        # Always tag (b)(5) as deliberation unless there's strong
        # competing CMC signal; the +5 baseline ensures deliberation
        # dominates ties.
        scores["Internal FDA deliberation"] = delib_score + 5

    if not scores:
        return ("Other / uncategorized", None, {})

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    primary = ranked[0][0]
    secondary = None
    if len(ranked) > 1:
        top_score = ranked[0][1]
        runner = ranked[1]
        if runner[1] >= top_score * 0.8:
            secondary = runner[0]
    return (primary, secondary, scores)


def find_section_heading(text_before: str) -> str | None:
    """Walk backward through `text_before` (text from the start of the
    page up to the marker) looking for the last heading-like line."""
    candidates: list[tuple[int, str]] = []
    for pat in HEADING_PATTERNS:
        for m in pat.finditer(text_before):
            heading = m.group(0).strip()
            if len(heading) < 5:
                continue
            if heading.replace(".", "").isdigit():
                continue
            # Reject Bates-number false positives like FDA-CBER-2021-5683-1150325
            if BATES_RE.search(heading):
                continue
            # Reject lines that are just hyphenated numeric IDs
            if re.fullmatch(r"[A-Z]+-[A-Z]+-[\d-]+", heading):
                continue
            candidates.append((m.start(), heading))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[-1][1][:200]


def find_bates(text: str) -> str | None:
    m = BATES_RE.search(text)
    return m.group(0) if m else None


def build_worklist() -> tuple[dict[str, list[int]], dict[str, dict]]:
    """Return:
        in_scope: {filename: [page_num, ...]}  pages to inspect for each file
        idx_records: {filename: index_record}
    Pages are deduplicated, sorted. The page list includes any page with
    at least one structurally-valid non-(b)(6) marker.
    """
    in_scope: dict[str, set[int]] = defaultdict(set)
    for src in EXEMPTION_SOURCES:
        path = DATA / f"{src}.json"
        if not path.exists():
            continue
        d = json.loads(path.read_text())
        for f in d.get("files", []):
            if f.get("skipped"):
                continue
            filename = f["filename"]
            if "_CRF_" in filename:
                continue
            for pg in f.get("exemption_pages", []):
                marker = pg["marker"]
                if marker == "(b)(6)":
                    continue
                if not is_structurally_valid_foia(marker):
                    continue
                in_scope[filename].add(pg["page"])

    # Index lookup
    idx = json.loads(INDEX.read_text())
    idx_records = {r["filename"]: r for r in idx}

    # Drop files missing index records (shouldn't happen, but safe)
    in_scope = {f: sorted(p) for f, p in in_scope.items() if f in idx_records}
    return in_scope, idx_records


def open_pdf_for(filename: str, idx_record: dict) -> bytes | None:
    """Return PDF bytes from individual cache or zip."""
    p = INDIVIDUAL_PDF_CACHE / filename
    if p.exists():
        try:
            return p.read_bytes()
        except OSError:
            pass
    zip_src = idx_record.get("zip_source")
    if not zip_src:
        return None
    zip_path = ZIPS_DIR / idx_record["batch_code"] / zip_src
    if not zip_path.exists():
        return None
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if info.filename.rsplit("/", 1)[-1] == filename:
                    with zf.open(info) as f:
                        return f.read()
    except Exception:
        return None
    return None


def scan_pdf(pdf_bytes: bytes, filename: str, pages_to_scan: list[int],
             idx_rec: dict) -> dict:
    """Open PDF and extract rich-context records for every marker on
    the requested pages.

    Returns {"kept": [...], "dropped": [...]} — both lists of dicts.
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "kept": [], "dropped": []}

    pages_set = set(pages_to_scan)
    page_texts: dict[int, str] = {}

    # Cache page text once per page we look at (and the prior page if we
    # need to walk back for headings).
    def page_text(n: int) -> str:
        if n in page_texts:
            return page_texts[n]
        if n < 1 or n > doc.page_count:
            return ""
        try:
            page = doc.load_page(n - 1)
            txt = get_page_text(filename, page, n)
        except Exception:
            txt = ""
        page_texts[n] = txt
        return txt

    kept: list[dict] = []
    dropped: list[dict] = []

    try:
        for page_num in pages_to_scan:
            text = page_text(page_num)
            if not text:
                continue

            # Count all candidate-marker positions on this page (for
            # table-cluster detection).
            all_match_positions: list[int] = [m.start() for m in MARKER_RE.finditer(text)]

            for m in MARKER_RE.finditer(text):
                marker_canonical = normalize_marker(m.group(1), m.group(2))
                marker_raw = m.group(0)

                # Structural validation
                structural_valid = is_structurally_valid_foia(marker_canonical)

                # Slice context windows
                start = max(0, m.start() - CONTEXT_WIDTH)
                end = min(len(text), m.end() + CONTEXT_WIDTH)
                ctx = " ".join(text[start:end].split())
                left_raw = text[max(0, m.start() - 30):m.start()]

                # Build dropped record if marker is (b)(6) or structurally invalid
                base = {
                    "filename": filename,
                    "page": page_num,
                    "marker_canonical": marker_canonical,
                    "marker_raw": marker_raw,
                    "module": idx_rec.get("module"),
                    "company": idx_rec.get("company"),
                    "license": idx_rec.get("license"),
                    "context": ctx[:600],  # smaller for dropped audit
                }

                if not structural_valid:
                    dropped.append({**base, "reason": "structurally_invalid"})
                    continue
                if marker_canonical == "(b)(6)":
                    dropped.append({**base, "reason": "(b)(6)_excluded"})
                    continue

                # FOIA-mention classifier
                signals = detect_signals(ctx, left_raw)
                foia_class = categorize(signals, marker_canonical)
                if foia_class != "foia_redaction":
                    dropped.append({
                        **base,
                        "reason": foia_class,  # "foia_legal_reference" or "not_foia"
                        "signals": signals,
                    })
                    continue

                # Table-cluster detection
                marker_pos = m.start()
                near = sum(
                    1 for p in all_match_positions
                    if abs(p - marker_pos) <= TABLE_CLUSTER_WINDOW and p != marker_pos
                )
                likely_table = near >= TABLE_CLUSTER_THRESHOLD

                # Section heading: text from page-start up to marker, plus
                # up to 2 prior pages prepended if no heading found nearby.
                heading_search = text[:m.start()]
                heading = find_section_heading(heading_search)
                if not heading:
                    prior_text = ""
                    for back in (1, 2):
                        prior_text = page_text(page_num - back) + "\n" + prior_text
                        heading = find_section_heading(prior_text)
                        if heading:
                            break

                # Bates: search full page text first, then nearby pages
                bates = find_bates(text) or find_bates(page_text(page_num - 1) or "")

                # Substance taxonomy
                substance, secondary, taxonomy_scores = categorize_substance(ctx, marker_canonical)

                kept.append({
                    "filename": filename,
                    "page": page_num,
                    "marker_canonical": marker_canonical,
                    "marker_raw": marker_raw,
                    "context": ctx,
                    "section_heading": heading,
                    "bates": bates,
                    "likely_table": likely_table,
                    "nearby_marker_count": near,
                    "foia_classification": "foia_redaction",
                    "signals": signals,
                    "substance_category": substance,
                    "substance_secondary": secondary,
                    "taxonomy_scores": taxonomy_scores,
                    "module": idx_rec.get("module"),
                    "company": idx_rec.get("company"),
                    "license": idx_rec.get("license"),
                })
    finally:
        doc.close()

    return {"kept": kept, "dropped": dropped}


def main(rebuild: bool = False) -> None:
    if not INDEX.exists():
        sys.exit(f"{INDEX} missing — run scripts/build_index.py first")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    in_scope, idx_records = build_worklist()
    print(f"{len(in_scope)} PDFs in scope (non-CRF with non-(b)(6) markers)")

    # Group by ZIP for efficient access (cached individuals are direct-read)
    # but iterate per-file (simpler; PDFs are small enough)
    all_kept: list[dict] = []
    all_dropped: list[dict] = []
    files_missing_pdf: list[str] = []
    files_errored: list[str] = []

    pbar = tqdm(total=len(in_scope), desc="hidden-substance", unit="pdf")
    for filename, pages in sorted(in_scope.items()):
        cache_path = CACHE_DIR / f"{filename}.json"
        if cache_path.exists() and not rebuild:
            result = json.loads(cache_path.read_text())
        else:
            idx_rec = idx_records[filename]
            body = open_pdf_for(filename, idx_rec)
            if body is None:
                files_missing_pdf.append(filename)
                pbar.update(1)
                continue
            result = scan_pdf(body, filename, pages, idx_rec)
            cache_path.write_text(json.dumps(result, indent=2))

        if "error" in result:
            files_errored.append(filename)
        all_kept.extend(result.get("kept", []))
        all_dropped.extend(result.get("dropped", []))
        pbar.update(1)
    pbar.close()

    # ---- Aggregates ----
    by_category: Counter = Counter()
    by_marker: Counter = Counter()
    by_module: Counter = Counter()
    by_company: Counter = Counter()
    table_count = 0
    hits_by_file: dict[str, list[dict]] = defaultdict(list)
    for h in all_kept:
        by_category[h["substance_category"]] += 1
        by_marker[h["marker_canonical"]] += 1
        by_module[h.get("module") or "Memo/Other"] += 1
        by_company[h.get("company") or "?"] += 1
        if h["likely_table"]:
            table_count += 1
        hits_by_file[h["filename"]].append(h)

    # ---- (1) Slim per-file summary ----
    summary_rows = []
    for filename, hits in sorted(hits_by_file.items()):
        cats = Counter(h["substance_category"] for h in hits)
        markers = Counter(h["marker_canonical"] for h in hits)
        # Top 3 representative hits — prefer rare markers + table-cluster
        ranked = sorted(
            hits,
            key=lambda h: (
                -(1 if h["marker_canonical"] != "(b)(4)" else 0),
                -(1 if h["likely_table"] else 0),
                -(1 if h.get("section_heading") else 0),
            ),
        )
        first = hits[0]
        summary_rows.append({
            "filename": filename,
            "module": first.get("module"),
            "company": first.get("company"),
            "license": first.get("license"),
            "total_kept": len(hits),
            "table_count": sum(1 for h in hits if h["likely_table"]),
            "by_category": dict(cats.most_common()),
            "by_marker": dict(markers.most_common()),
            "top_examples": [
                {
                    "page": h["page"],
                    "marker": h["marker_canonical"],
                    "category": h["substance_category"],
                    "section_heading": h.get("section_heading"),
                    "bates": h.get("bates"),
                    "context_preview": (h["context"] or "")[:300],
                    "likely_table": h["likely_table"],
                }
                for h in ranked[:3]
            ],
        })

    summary_doc = {
        "summary": {
            "scope": "non-CRF files, excluding (b)(6) personal-privacy markers, kept FOIA-redaction-classified hits only",
            "files_in_scope": len(in_scope),
            "files_with_kept_hits": len(hits_by_file),
            "files_missing_pdf": files_missing_pdf,
            "files_errored": files_errored,
            "total_kept": len(all_kept),
            "total_dropped": len(all_dropped),
            "kept_likely_in_table": table_count,
            "by_substance_category": dict(by_category.most_common()),
            "by_marker": dict(by_marker.most_common()),
            "by_module": dict(by_module.most_common()),
            "by_company": dict(by_company.most_common()),
        },
        "files": summary_rows,
    }
    OUT_SUMMARY.write_text(json.dumps(summary_doc, indent=2))

    # ---- (2) Per-file detail JSONs (loaded on-demand) ----
    OUT_DETAIL_DIR.mkdir(parents=True, exist_ok=True)
    for filename, hits in hits_by_file.items():
        # Trim context to 600 chars to keep file size reasonable while
        # giving readers meaningful surrounding text.
        slim_hits = []
        for h in hits:
            slim_hits.append({
                "page": h["page"],
                "marker_canonical": h["marker_canonical"],
                "marker_raw": h["marker_raw"],
                "context": (h["context"] or "")[:600],
                "section_heading": h.get("section_heading"),
                "bates": h.get("bates"),
                "likely_table": h["likely_table"],
                "nearby_marker_count": h.get("nearby_marker_count", 0),
                "substance_category": h["substance_category"],
                "substance_secondary": h.get("substance_secondary"),
            })
        detail_path = OUT_DETAIL_DIR / f"{filename}.json"
        detail_path.write_text(json.dumps({"filename": filename, "hits": slim_hits}))

    # ---- (3) Full record dump (gitignored, used by PDF generator) ----
    full_doc = {
        "summary": summary_doc["summary"],
        "hits": all_kept,
    }
    OUT_FULL.write_text(json.dumps(full_doc, indent=2))

    # ---- Dropped-audit: emit a slim summary + per-reason samples ----
    by_reason: Counter = Counter(h["reason"] for h in all_dropped)
    samples_per_reason: dict[str, list[dict]] = defaultdict(list)
    for h in all_dropped:
        if len(samples_per_reason[h["reason"]]) < 30:
            samples_per_reason[h["reason"]].append({
                "filename": h["filename"],
                "page": h["page"],
                "marker_raw": h["marker_raw"],
                "marker_canonical": h["marker_canonical"],
                "context": h["context"][:400],
                "module": h.get("module"),
                "company": h.get("company"),
            })
    dropped_doc = {
        "summary": {
            "total_dropped": len(all_dropped),
            "by_reason": dict(by_reason.most_common()),
            "note": "30 sample records per reason are included below for spot-checking the filter logic. The full set is in data/hidden_substance_full_dropped.json (gitignored).",
        },
        "samples_per_reason": dict(samples_per_reason),
    }
    OUT_DROPPED.write_text(json.dumps(dropped_doc, indent=2))

    # Full dropped also kept locally (gitignored)
    (DATA / "hidden_substance_full_dropped.json").write_text(
        json.dumps({"summary": dropped_doc["summary"], "hits": all_dropped}, indent=2)
    )

    # Console digest
    print()
    print("=" * 60)
    print("HIDDEN SUBSTANCE SCAN COMPLETE")
    print("=" * 60)
    print(f"Files in scope:           {len(in_scope)}")
    print(f"Files w/ kept hits:       {len(hits_by_file)}")
    print(f"Files missing PDF:        {len(files_missing_pdf)}")
    print(f"Files errored:            {len(files_errored)}")
    print()
    print(f"Total KEPT hits:          {len(all_kept):,}")
    print(f"  Likely in table:        {table_count:,}")
    print(f"Total DROPPED hits:       {len(all_dropped):,}")
    print()
    print("Kept by marker:")
    for m, c in by_marker.most_common():
        print(f"  {m:<14} {c:>8,}")
    print()
    print("Kept by category:")
    for cat, c in by_category.most_common():
        print(f"  {cat:<35} {c:>8,}")
    print()
    print("Dropped by reason:")
    for r, c in by_reason.most_common():
        print(f"  {r:<25} {c:>8,}")
    print()
    print(f"Wrote: {OUT_SUMMARY}")
    print(f"Wrote: {OUT_DETAIL_DIR}/ ({len(hits_by_file)} per-file detail JSONs)")
    print(f"Wrote: {OUT_FULL}")
    print(f"Wrote: {OUT_DROPPED}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--rebuild", action="store_true", help="ignore per-file cache")
    args = p.parse_args()
    main(rebuild=args.rebuild)
