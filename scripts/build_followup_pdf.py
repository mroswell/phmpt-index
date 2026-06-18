"""Generate the printable "FOIA Follow-Up Targets" report from the
top-100 ranked targets at docs/data/foia_followup_targets.json.

Outputs three formats so the user can pick whatever fits the audience:
  docs/data/foia_followup_targets.md        — Markdown source
  docs/data/foia_followup_targets.html      — Print-styled HTML
                                              (open in browser; Cmd-P → Save as PDF)
  docs/data/foia_followup_targets.pdf       — Optional: only written if
                                              pandoc + a LaTeX engine or
                                              weasyprint is available.

The HTML is intentionally self-contained (inline CSS, no external assets)
so it can be emailed as an attachment.
"""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
WEB = ROOT / "docs" / "data"

TARGETS_PATH = WEB / "foia_followup_targets.json"
FULL_PATH = DATA / "hidden_substance_full.json"
INDEX_PATH = WEB / "index.json"
OUT_MD = WEB / "foia_followup_targets.md"
OUT_HTML = WEB / "foia_followup_targets.html"
OUT_PDF = WEB / "foia_followup_targets.pdf"

SITE_URL = "https://mroswell.github.io/phmpt-index/"


def fmt_module(t: dict) -> str:
    return t.get("module") or "Memo/Other"


def whyitmatters(t: dict) -> str:
    """Hand-craftable one-paragraph 'why a senator/lawyer should care'."""
    cat = t.get("substance_category") or "Other"
    marker = t.get("marker_canonical") or "?"
    likely_table = t.get("likely_table")
    nearby = t.get("nearby_marker_count", 0)
    mod = fmt_module(t)

    parts: list[str] = []

    if marker == "(b)(4)":
        parts.append(
            "Trade-secret / commercial-confidential redaction — the agency or "
            "applicant claims revealing it would cause competitive harm."
        )
    elif marker == "(b)(5)":
        parts.append(
            "Deliberative-process redaction — FDA reviewer opinions, draft "
            "recommendations, or internal debate. Often the most substantively "
            "interesting topic because it hides the agency's reasoning."
        )
    elif marker.startswith("(b)(7)"):
        sub = marker[-2]
        if sub == "C":
            parts.append(
                "Law-enforcement personal-privacy redaction. Often hides "
                "individual investigators' or interview subjects' identities."
            )
        elif sub == "E":
            parts.append(
                "Law-enforcement technique redaction. Hides investigative "
                "methods, often appearing in inspection reports."
            )
        else:
            parts.append("Law-enforcement exemption — context-dependent.")
    else:
        parts.append(f"Exemption type {marker}.")

    if cat == "Manufacturing / CMC":
        parts.append(
            "Surrounding text references manufacturing / CMC content "
            "(formulation, batch, process, lot release, etc.)."
        )
    elif cat == "Pharmacology / toxicology":
        parts.append(
            "Surrounding text references nonclinical pharmacology / toxicology "
            "(biodistribution, ADME, GLP studies, NOAEL, etc.)."
        )
    elif cat == "Clinical safety / adverse events":
        parts.append(
            "Surrounding text references clinical safety (adverse events, "
            "narratives, AESIs, mortality, post-marketing follow-up)."
        )
    elif cat == "Inspection / enforcement":
        parts.append(
            "Surrounding text references FDA inspections / 483s / corrective "
            "actions — typical site-inspection context."
        )
    elif cat == "Trial design / efficacy":
        parts.append(
            "Surrounding text references clinical-trial design or efficacy "
            "(protocol, endpoint, randomization, statistical analysis)."
        )
    elif cat == "Internal FDA deliberation":
        parts.append(
            "Surrounding text references reviewer discussion, recommendations, "
            "draft language, or internal opinion."
        )
    elif cat == "Trade-secret claim":
        parts.append(
            "Surrounding text explicitly references confidentiality / "
            "proprietary status, suggesting the redactor's rationale is "
            "competitive harm."
        )

    if likely_table:
        parts.append(
            f"Marker sits inside a likely table cluster ({nearby} additional markers within "
            "±200 chars on the same page) — a single redaction here may hide "
            "many rows or columns of data."
        )

    parts.append(
        "A senator's office (under Senate oversight authority) or an attorney "
        "with a vaccine-injury or fraud claim can request the unredacted text "
        "directly from FDA citing the exact Bates number and page below."
    )

    return " ".join(parts)


def md_escape(s: str) -> str:
    return (s or "").replace("|", "\\|")


def short_context(ctx: str, max_chars: int = 350) -> str:
    if not ctx:
        return ""
    ctx = " ".join(ctx.split())
    if len(ctx) <= max_chars:
        return ctx
    return ctx[:max_chars] + " …"


def main() -> None:
    if not TARGETS_PATH.exists():
        raise SystemExit(f"Run rank_followup_targets.py first ({TARGETS_PATH} missing)")
    if not FULL_PATH.exists():
        raise SystemExit(f"Run extract_hidden_substance.py first ({FULL_PATH} missing)")

    targets_doc = json.loads(TARGETS_PATH.read_text())
    full_doc = json.loads(FULL_PATH.read_text())
    index_records = {r["filename"]: r for r in json.loads(INDEX_PATH.read_text())}
    summary = full_doc["summary"]
    targets = targets_doc["targets"]

    # Group full-kept hits by category for the category sections
    full_hits = full_doc["hits"]
    by_category: dict[str, list[dict]] = defaultdict(list)
    for h in full_hits:
        by_category[h["substance_category"]].append(h)

    # ---- Build Markdown ----
    md: list[str] = []
    today = date.today().isoformat()

    md.append("# FOIA Follow-Up Targets")
    md.append("")
    md.append(f"_Generated {today} from {SITE_URL}_")
    md.append("")
    md.append("---")
    md.append("")
    md.append("## What this report is")
    md.append("")
    md.append(
        "FDA released roughly 18 GB of internal documents on its review of the "
        "Pfizer-BioNTech and Moderna COVID-19 vaccines under court order in "
        "*PHMPT v. FDA*. Many pages contain FOIA redactions — black-bar boxes "
        "labeled `(b)(N)` indicating the legal basis for withholding."
    )
    md.append("")
    md.append(
        f"This report catalogs the **top {len(targets)} redactions** in the corpus "
        "that are most worth following up on — selected from a pool of "
        f"**~{summary['total_kept']:,} non-routine markers** (after excluding "
        "Case Report Forms, which hold 99% of all markers, and `(b)(6)` "
        "personal-privacy redactions, which protect the names of individual "
        "trial participants and case writers)."
    )
    md.append("")
    md.append(
        "Each entry below gives a senator's office or an attorney enough "
        "information to ask FDA, in writing, for the specific text behind "
        "that specific redaction — citing the Bates number, page, and "
        "surrounding context."
    )
    md.append("")
    md.append("## How targets were selected")
    md.append("")
    md.append("Stratified across topics so no one topic dominates:")
    md.append("")
    md.append("| Stratum | Slots |")
    md.append("|---|---:|")
    for label, quota in targets_doc["summary"]["strata_quotas"].items():
        md.append(f"| {label} | {quota} |")
    md.append("")
    md.append("Per-target score components:")
    md.append("")
    for k, v in targets_doc["summary"]["scoring_rubric"].items():
        sign = "+" if v > 0 else ""
        md.append(f"- `{sign}{v}` — {k.replace('_', ' ')}")
    md.append("")
    md.append("Maximum 5 entries per source file to ensure coverage across documents.")
    md.append("")

    md.append("## Headline numbers")
    md.append("")
    md.append("**Markers by topic** (across the full ~64K dataset, not just top-100):")
    md.append("")
    md.append("| Topic | Markers |")
    md.append("|---|---:|")
    for cat, n in summary["by_substance_category"].items():
        md.append(f"| {cat} | {n:,} |")
    md.append("")
    md.append("**Markers by FOIA exemption type:**")
    md.append("")
    md.append("| Marker | Count |")
    md.append("|---|---:|")
    for m, n in summary["by_marker"].items():
        md.append(f"| `{m}` | {n:,} |")
    md.append("")
    md.append("**Markers by module (eCTD submission section):**")
    md.append("")
    md.append("| Module | Count |")
    md.append("|---|---:|")
    for mod, n in summary["by_module"].items():
        md.append(f"| {mod} | {n:,} |")
    md.append("")

    # ---- Per-topic sections (top 3 illustrative examples each) ----
    md.append("## Topic overviews")
    md.append("")
    category_order = [
        "Manufacturing / CMC",
        "Pharmacology / toxicology",
        "Clinical safety / adverse events",
        "Trial design / efficacy",
        "Inspection / enforcement",
        "Internal FDA deliberation",
        "Trade-secret claim",
        "Other / uncategorized",
    ]
    for cat in category_order:
        hits = by_category.get(cat, [])
        if not hits:
            continue
        md.append(f"### {cat} — {len(hits):,} redactions")
        md.append("")
        # Top 3 by score (rare marker, has bates, has section heading, has table)
        ranked = sorted(
            hits,
            key=lambda h: (
                -(2 if h["marker_canonical"] != "(b)(4)" else 0),
                -(1 if h["likely_table"] else 0),
                -(1 if h.get("bates") else 0),
                -(1 if h.get("section_heading") else 0),
            ),
        )
        for h in ranked[:3]:
            md.append(
                f"- **{h['filename']}** p{h['page']} `{h['marker_canonical']}`"
                f"{' ⊞' if h['likely_table'] else ''}"
            )
            sect = h.get("section_heading")
            if sect:
                md.append(f"    §  {sect}")
            md.append(f"    > {short_context(h['context'], 240)}")
        md.append("")

    # ---- Top-100 targets ----
    md.append("---")
    md.append("")
    md.append(f"## Top {len(targets)} follow-up targets")
    md.append("")
    md.append(
        "Each entry below is a single redaction. The information in *every* "
        "entry — Bates number, page, marker, surrounding context, file URL — "
        "is sufficient on its own to compose a one-line follow-up FOIA "
        "request or oversight inquiry."
    )
    md.append("")

    # Track by bucket for slight visual grouping
    current_bucket = None
    for i, t in enumerate(targets, 1):
        bucket = t.get("_bucket")
        if bucket != current_bucket:
            md.append("")
            md.append(f"### {bucket}")
            md.append("")
            current_bucket = bucket

        mod = fmt_module(t)
        company = t.get("company") or "?"
        license_ = t.get("license") or "?"
        bates = t.get("bates") or "_(no Bates captured on this page)_"
        heading = t.get("section_heading") or "_(no section heading detected)_"
        url = (
            (index_records.get(t["filename"]) or {}).get("individual_url")
            or (index_records.get(t["filename"]) or {}).get("ican_url")
            or (index_records.get(t["filename"]) or {}).get("zip_url")
            or ""
        )

        md.append(f"#### #{i} — {company} {license_}, {mod}, page {t['page']}")
        md.append("")
        md.append(f"**File:** `{t['filename']}`")
        if url:
            md.append("")
            md.append(f"[Open document]({url})")
        md.append("")
        md.append(f"**Bates:** {bates}")
        md.append("")
        md.append(f"**Marker:** `{t['marker_canonical']}` &nbsp; · &nbsp; **Topic:** {t['substance_category']}"
                  f"{' &nbsp; · &nbsp; **Likely in table** (' + str(t.get('nearby_marker_count', 0)) + ' nearby markers)' if t['likely_table'] else ''}")
        md.append("")
        md.append(f"**Section heading near redaction:** {heading}")
        md.append("")
        md.append(f"**Surrounding context** (redaction is the `{t['marker_canonical']}` marker below):")
        md.append("")
        md.append("> " + short_context(t["context"], 600).replace("\n", " "))
        md.append("")
        md.append(f"**Why a follow-up may be worth it:** {whyitmatters(t)}")
        md.append("")
        md.append("---")
        md.append("")

    # ---- Appendix ----
    md.append("## Appendix: taxonomy keyword list")
    md.append("")
    md.append(
        "Each redaction was tagged with a topic by counting substring matches "
        "in its surrounding ±500-char context. The topic with the most matches "
        "wins. Markers with no keyword match land in *Other / uncategorized*. "
        "Code: `scripts/extract_hidden_substance.py::TAXONOMY`."
    )
    md.append("")
    from extract_hidden_substance import TAXONOMY, DELIBERATION_KEYWORDS
    for cat, kws in TAXONOMY.items():
        md.append(f"### {cat}")
        md.append("")
        md.append(", ".join(f"`{k}`" for k in kws))
        md.append("")
    md.append("### Internal FDA deliberation (additional triggers for `(b)(5)`)")
    md.append("")
    md.append(", ".join(f"`{k}`" for k in DELIBERATION_KEYWORDS))
    md.append("")

    md_text = "\n".join(md)
    OUT_MD.write_text(md_text)
    print(f"Wrote: {OUT_MD}")

    # ---- HTML version with print styles ----
    # Pandoc handles the heavy lifting (TOC, anchors, blockquote styling)
    if not shutil.which("pandoc"):
        print("⚠️  pandoc not found — skipping HTML generation. Markdown still emitted.")
    else:
        css = """
        body { font: 11pt/1.45 Georgia, "Times New Roman", serif;
               max-width: 850px; margin: 32px auto; padding: 0 32px;
               color: #1c1c1c; }
        h1 { font-size: 22pt; margin: 0 0 4px; color: #1e4d8c; }
        h2 { font-size: 16pt; margin: 28px 0 8px; color: #1e4d8c;
             border-bottom: 1px solid #ccc; padding-bottom: 2px; }
        h3 { font-size: 13pt; margin: 22px 0 8px; color: #333; }
        h4 { font-size: 11pt; margin: 18px 0 4px; color: #1e4d8c; }
        code { background: #f3f3f3; padding: 0 4px; border-radius: 2px;
               font-family: ui-monospace, Menlo, monospace; font-size: 90%; }
        blockquote { border-left: 3px solid #1e4d8c; margin: 6px 0;
                     padding: 4px 10px; background: #fafafa;
                     font-family: ui-monospace, Menlo, monospace;
                     font-size: 10pt; line-height: 1.45; }
        table { border-collapse: collapse; width: auto; margin: 8px 0;
                font-size: 10pt; }
        th, td { border: 1px solid #ccc; padding: 4px 10px; text-align: left; }
        th { background: #f3f3f3; }
        td.num, td:has(+ td:empty), td:last-child { text-align: right; }
        hr { border: 0; border-top: 1px solid #ddd; margin: 16px 0; }
        a { color: #1e4d8c; }
        @media print {
            body { margin: 0; padding: 18mm 16mm; max-width: none; }
            h2 { page-break-before: auto; }
            h4 { page-break-after: avoid; }
            blockquote, hr { page-break-inside: avoid; }
        }
        """
        try:
            subprocess.run(
                ["pandoc", str(OUT_MD), "-s", "-o", str(OUT_HTML),
                 # Disable TeX-math interpretation — context text contains
                 # stray $ characters and other punctuation from OCR/PDF that
                 # would otherwise be parsed as LaTeX math
                 "--from=markdown-tex_math_dollars-tex_math_single_backslash-raw_tex",
                 "--metadata=title:FOIA Follow-Up Targets",
                 "-V", "lang=en"],
                check=False,
            )
            # Inject CSS inline so the file is self-contained
            html_text = OUT_HTML.read_text()
            html_text = html_text.replace(
                "</head>",
                f"<style>{css}</style></head>",
                1,
            )
            OUT_HTML.write_text(html_text)
            print(f"Wrote: {OUT_HTML} (open in browser; Cmd-P → Save as PDF)")
        except Exception as e:
            print(f"⚠️  pandoc HTML conversion failed: {e}")

        # Optional PDF if a backend is available
        for engine in ("weasyprint", "wkhtmltopdf"):
            if shutil.which(engine):
                try:
                    subprocess.run(
                        ["pandoc", str(OUT_MD), "-o", str(OUT_PDF),
                         f"--pdf-engine={engine}"],
                        check=True,
                    )
                    print(f"Wrote: {OUT_PDF} (via {engine})")
                    break
                except Exception as e:
                    print(f"⚠️  pandoc PDF via {engine} failed: {e}")
        else:
            print("ℹ️  No PDF backend (weasyprint/wkhtmltopdf) found. Open the HTML "
                  "in a browser and Cmd-P → Save as PDF to produce a static PDF.")


if __name__ == "__main__":
    main()
