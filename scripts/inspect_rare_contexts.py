"""Classify every rare-exemption hit by extracting surrounding text
context and bucketing each match into one of three categories:

  foia_redaction        — actual redaction overlay (e.g. "(b)(5)"
                          printed on top of a redacted region)
  foia_legal_reference  — body-text mention of a FOIA exemption,
                          e.g. "5 U.S.C. § 552(b)(1)–(b)(9)"
  not_foia              — false positive: the (b)(N) pattern is part
                          of a citation to a DIFFERENT statute (most
                          commonly FD&C Act subsections like
                          505(b)(1), Section 564(b)(1)(C))

Writes two outputs into docs/data/:

  rare_exemptions_contexts.json
      Flat per-hit record with file, page, marker, context, signals,
      category. Useful for debugging / spot-checking.

  rare_exemptions.json (overwrites the basic one from
                        scripts/report_eyeball.py)
      Restructured to bucket each file under "redaction" or
      "mention", filter out not_foia hits entirely, and adjust the
      page counts to reflect only kept hits.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import fitz
import zipfile_deflate64 as zipfile

from _pdf_text import get_page_text  # OCR-aware text extraction

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
WEB = ROOT / "docs" / "data"
INDEX = WEB / "index.json"
INDIVIDUAL_PDF_CACHE = DATA / "cache" / "individual_pdfs"
ZIPS_DIR = DATA / "zips"
RARE = WEB / "rare_exemptions.json"
OUT = WEB / "rare_exemptions_contexts.json"
OUT_RARE = WEB / "rare_exemptions.json"  # overwrites the basic version

# Same regex the scanners use, kept identical so we find the same matches
MARKER_RE = re.compile(r"\(\s*b\s*\)\s*\(\s*(\d+)\s*\)(?:\s*\(\s*([A-F])\s*\))?")

CONTEXT_WIDTH = 150


def normalize(num: str, sub: str | None) -> str:
    return f"(b)({num})" + (f"({sub})" if sub else "")


def detect_signals(ctx: str, left: str) -> dict:
    """Signals used to bucket each marker into one of three categories.

    `ctx` is the full ~300-char window around the marker.
    `left` is just the text immediately before the "(b)" — used to
    detect "NNN(b)(N)" or "Section NNN (b)(N)" subsection references.
    """
    lc = ctx.lower()
    return {
        # ---- FOIA legal-reference signals ----
        "has_section_sign": "§" in ctx,
        "has_usc_552": ("u.s.c. § 552" in lc) or ("u.s.c. 552" in lc)
                       or ("5 u.s.c." in lc),
        "has_foia": "foia" in lc or "freedom of information" in lc,
        # ---- Non-FOIA "other statute" signals ----
        # FD&C Act references (the FDA's enabling statute, where most
        # of our false-positive (b)(1)/(b)(2) hits come from)
        "has_fdc_act": ("fd&c" in lc) or ("food, drug" in lc)
                       or ("federal food" in lc) or ("ffdca" in lc),
        "has_21_usc": ("21 u.s.c." in lc) or ("21 usc" in lc)
                      or ("21 cfr" in lc),
        # Direct "Section NNN" preceding the marker
        "preceded_by_section_n": bool(re.search(r"[Ss]ection\s+\d+\s*$", left)),
        # "NNN(b)" — digits directly attached to the (b) opening paren
        # (e.g. 505(b)(1), 351(a), 506(b)(2)). Means it's part of some
        # other section number, not a standalone FOIA exemption label.
        "preceded_by_digits_no_gap": bool(re.search(r"\d+\s*$", left))
                                     and not bool(re.search(r"\d+(?:-\d+){2,}\s*$", left)),
    }


def categorize(signals: dict) -> str:
    """Return one of: 'not_foia', 'foia_legal_reference', 'foia_redaction'."""
    # Strong signals that this isn't a FOIA marker at all
    if signals["preceded_by_section_n"]:
        return "not_foia"
    if signals["has_fdc_act"] or signals["has_21_usc"]:
        return "not_foia"
    # NNN(b)(N) drug-app section number pattern, but only if no FOIA
    # signal in the wider context (otherwise it might be a real
    # exemption near a Bates-numbered footer)
    if signals["preceded_by_digits_no_gap"] and not (
        signals["has_section_sign"] or signals["has_usc_552"] or signals["has_foia"]
    ):
        return "not_foia"

    # FOIA-specific legal references
    if signals["has_section_sign"] or signals["has_usc_552"] or signals["has_foia"]:
        return "foia_legal_reference"

    # Default: real redaction overlay
    return "foia_redaction"


def open_pdf_for(filename: str, idx_record: dict) -> bytes | None:
    """Return PDF bytes from either the cached individual download or
    the source ZIP."""
    zip_src = idx_record.get("zip_source")
    if zip_src:
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
    # Individual file
    p = INDIVIDUAL_PDF_CACHE / filename
    if p.exists():
        return p.read_bytes()
    return None


def main() -> None:
    if not RARE.exists():
        sys.exit(f"{RARE} missing — run scripts/report_eyeball.py first")
    if not INDEX.exists():
        sys.exit(f"{INDEX} missing — run scripts/build_index.py first")

    rare = json.loads(RARE.read_text())
    idx = json.loads(INDEX.read_text())
    # filename -> first index record we see for it
    by_fname: dict[str, dict] = {}
    for r in idx:
        if r["filename"] not in by_fname:
            by_fname[r["filename"]] = r

    # Group rare hits by filename so each PDF opens once
    files: dict[str, list[dict]] = defaultdict(list)
    for marker, rows in rare.get("by_marker", {}).items():
        for r in rows:
            files[r["filename"]].append({"marker": marker, **r})

    print(f"{len(files)} files with rare-exemption hits to inspect")
    print()

    hits_out: list[dict] = []
    summary = {
        "files_inspected": 0,
        "files_missing_pdf": [],
        "total_hits": 0,
        "hits_classified_not_foia": 0,
        "hits_classified_legal_reference": 0,
        "hits_classified_redaction": 0,
        "by_marker": defaultdict(lambda: {"total": 0, "not_foia": 0, "legal_ref": 0, "redaction": 0}),
    }

    for filename, rare_rows in files.items():
        idx_rec = by_fname.get(filename)
        if not idx_rec:
            summary["files_missing_pdf"].append(filename)
            continue
        body = open_pdf_for(filename, idx_rec)
        if body is None:
            summary["files_missing_pdf"].append(filename)
            continue

        try:
            doc = fitz.open(stream=body, filetype="pdf")
        except Exception as e:
            summary["files_missing_pdf"].append(f"{filename} ({e})")
            continue
        summary["files_inspected"] += 1

        # Set of (page, marker) we need to extract context for
        needed = defaultdict(set)
        for r in rare_rows:
            for pg in r["pages"]:
                needed[pg["page"]].add(r["marker"])
        # Track totals separately so context-less hits still count
        per_marker_totals: Counter = Counter()
        for r in rare_rows:
            for pg in r["pages"]:
                per_marker_totals[r["marker"]] += pg["count"]

        try:
            for page_num, page in enumerate(doc, start=1):
                if page_num not in needed:
                    continue
                # Use OCR-aware text so we don't undercount on image-only pages
                text = get_page_text(filename, page, page_num)
                wanted_markers = needed[page_num]
                # Find each match position so we can grab surrounding text
                for m in MARKER_RE.finditer(text):
                    marker = normalize(m.group(1), m.group(2))
                    if marker not in wanted_markers:
                        continue
                    start = max(0, m.start() - CONTEXT_WIDTH)
                    end = min(len(text), m.end() + CONTEXT_WIDTH)
                    ctx = " ".join(text[start:end].split())
                    # Just the text immediately before "(b)" (used for the
                    # "preceded_by_*" signals that need un-collapsed text)
                    left = text[max(0, m.start() - 30):m.start()]
                    signals = detect_signals(ctx, left)
                    category = categorize(signals)
                    hits_out.append({
                        "filename": filename,
                        "page": page_num,
                        "marker": marker,
                        "context": ctx,
                        "signals": signals,
                        "category": category,
                        "module": idx_rec.get("module"),
                        "company": idx_rec.get("company"),
                        "license": idx_rec.get("license"),
                    })
                    summary["total_hits"] += 1
                    bucket = summary["by_marker"][marker]
                    bucket["total"] += 1
                    if category == "not_foia":
                        bucket["not_foia"] += 1
                        summary["hits_classified_not_foia"] += 1
                    elif category == "foia_legal_reference":
                        bucket["legal_ref"] += 1
                        summary["hits_classified_legal_reference"] += 1
                    else:
                        bucket["redaction"] += 1
                        summary["hits_classified_redaction"] += 1
        finally:
            doc.close()

    # Convert defaultdict for JSON
    summary["by_marker"] = {k: v for k, v in summary["by_marker"].items()}

    OUT.write_text(json.dumps({"summary": summary, "hits": hits_out}, indent=2))

    # Rewrite docs/data/rare_exemptions.json with category-aware buckets.
    # Each marker now has TWO sub-lists: "redaction" (real overlays) and
    # "mention" (legal references). not_foia hits are dropped entirely
    # (they'll surface on a future "Other statutes mentioned" page).
    rare_basic = json.loads(RARE.read_text())
    descriptions = rare_basic["summary"].get("descriptions", {})

    # Index per-hit categories by (filename, marker, page)
    hits_by_key: dict[tuple[str, str, int], list[str]] = defaultdict(list)
    contexts_by_key: dict[tuple[str, str, int], list[str]] = defaultdict(list)
    for h in hits_out:
        key = (h["filename"], h["marker"], h["page"])
        hits_by_key[key].append(h["category"])
        if h["category"] == "foia_legal_reference":
            contexts_by_key[key].append(h["context"])

    enriched: dict[str, dict] = {}
    for marker, files in rare_basic["by_marker"].items():
        kept = {"redaction": [], "mention": []}
        for f in files:
            new_pages: list[dict] = []
            file_categories: list[str] = []
            for pg in f["pages"]:
                key = (f["filename"], marker, pg["page"])
                cats = hits_by_key.get(key, ["foia_redaction"])
                # Drop hits we classified as not_foia
                kept_cats = [c for c in cats if c != "not_foia"]
                if not kept_cats:
                    continue
                # Dominant kept category on this page
                page_cat = "foia_legal_reference" if (
                    "foia_legal_reference" in kept_cats
                    and kept_cats.count("foia_legal_reference") >= kept_cats.count("foia_redaction")
                ) else "foia_redaction"
                entry = {
                    "page": pg["page"],
                    "count": len(kept_cats),  # only valid hits
                    "category": page_cat,
                }
                # Attach context snippets for legal references so the UI
                # can show what the file is referring to
                if page_cat == "foia_legal_reference" and contexts_by_key.get(key):
                    entry["contexts"] = contexts_by_key[key][:2]
                new_pages.append(entry)
                file_categories.append(page_cat)

            if not new_pages:
                continue  # all hits in this file were not_foia

            # File-level bucket: if any page is a legal reference, the file
            # goes in "mention"; otherwise "redaction"
            file_bucket = "mention" if "foia_legal_reference" in file_categories else "redaction"
            new_record = {
                "filename": f["filename"],
                "module": f["module"],
                "company": f.get("company"),
                "license": f.get("license"),
                "batch_code": f.get("batch_code"),
                "pages": new_pages,
                "total_hits": sum(p["count"] for p in new_pages),
                "phmpt_url": f.get("phmpt_url"),
                "ican_url": f.get("ican_url"),
            }
            kept[file_bucket].append(new_record)

        if kept["redaction"] or kept["mention"]:
            enriched[marker] = kept

    # Build a slim summary
    by_marker_summary: dict[str, dict] = {}
    grand_total = 0
    grand_combos = 0
    for marker, bucket in enriched.items():
        total_hits = sum(f["total_hits"] for f in bucket["redaction"] + bucket["mention"])
        by_marker_summary[marker] = {
            "total": total_hits,
            "redaction_files": len(bucket["redaction"]),
            "redaction_hits": sum(f["total_hits"] for f in bucket["redaction"]),
            "mention_files": len(bucket["mention"]),
            "mention_hits": sum(f["total_hits"] for f in bucket["mention"]),
        }
        grand_total += total_hits
        grand_combos += len(bucket["redaction"]) + len(bucket["mention"])

    enriched_doc = {
        "summary": {
            "total_occurrences": grand_total,
            "file_marker_combos": grand_combos,
            "by_marker": by_marker_summary,
            "descriptions": descriptions,
            "note": (
                "Hits where the (b)(N) pattern referred to a different statute "
                "(FD&C Act, 21 CFR, etc.) — not FOIA — have been excluded. They "
                "will surface on a future 'Other statutes mentioned' page. See "
                "rare_exemptions_contexts.json for the full classification."
            ),
        },
        "by_marker": enriched,
    }
    OUT_RARE.write_text(json.dumps(enriched_doc, indent=2))
    print(f"Wrote: {OUT_RARE} (enriched with category buckets)")

    # Print a digest
    print("=" * 60)
    print("RARE EXEMPTION CONTEXT INSPECTION")
    print("=" * 60)
    print(f"Files inspected:        {summary['files_inspected']}")
    print(f"Files missing PDF:      {len(summary['files_missing_pdf'])}")
    print(f"Total hits with context:  {summary['total_hits']}")
    print(f"  not_foia (other statute): {summary['hits_classified_not_foia']}")
    print(f"  foia legal reference:     {summary['hits_classified_legal_reference']}")
    print(f"  actual FOIA redaction:    {summary['hits_classified_redaction']}")
    print()
    print("By marker:")
    print(f"  {'marker':<14} {'total':>6} {'redact':>7} {'legal':>7} {'other':>7}")
    for marker, b in sorted(summary["by_marker"].items(),
                            key=lambda kv: -kv[1]["total"]):
        print(f"  {marker:<14} {b['total']:>6} {b['redaction']:>7} {b['legal_ref']:>7} {b['not_foia']:>7}")
    print()
    print(f"Wrote: {OUT}")


if __name__ == "__main__":
    main()
