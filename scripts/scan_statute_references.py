"""Scan the corpus for citations to statutes, regulations, court
cases, and international rules.

Pattern families:
  - USC          5 U.S.C. § 552, 21 USC 355
  - CFR          21 CFR 314.50
  - Section-of-Act  Section 564(b)(1) of the FD&C Act
  - Named act    FD&C Act / FDCA / FOIA / PHS Act / PREP Act / CARES Act
  - Public Law   Public Law 116-127, Pub. L. 116-127
  - Court case   Smith v. Jones, 123 F.3d 456
  - EU reg       Regulation (EU) No 536/2014
  - EU directive Directive 2001/83/EC
  - ICH          ICH E6(R2), ICH Q1A
  - WHO TRS      WHO TRS 800
  - ISO          ISO 14155:2020

For each match we record family, normalized canonical name, raw match,
page number, and ~80-char context.

Same scanning architecture as scripts/scan_pharmacovigilance_terms.py
(stream from ZIPs, fall back to data/cache/individual_pdfs/).

Run:
    uv run python scripts/scan_statute_references.py
    uv run python scripts/scan_statute_references.py --rebuild
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

from _pdf_text import get_page_text  # OCR-aware text extraction

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ZIPS_DIR = DATA / "zips"
INDEX = ROOT / "docs" / "data" / "index.json"
ICAN_COMPARISON = DATA / "ican_comparison.json"
CACHE = DATA / "cache" / "statutes"
INDIVIDUAL_PDF_CACHE = DATA / "cache" / "individual_pdfs"
OUT_JSON = ROOT / "docs" / "data" / "statutes.json"

CONTEXT_WIDTH = 80


# --- Patterns ----------------------------------------------------------------

# USC: "21 U.S.C. § 355" or "5 USC 552" or "21 U.S.C. 355(b)(1)"
USC_RE = re.compile(
    r"\b(\d+)\s+U\.?\s*S\.?\s*C\.?\s+§?\s*(\d+[a-z]?(?:[-–]\d+[a-z]?)?)",
)

# CFR: "21 CFR 314.50" or "21 C.F.R. § 314.50" or "21 CFR Part 314"
CFR_RE = re.compile(
    r"\b(\d+)\s+C\.?\s*F\.?\s*R\.?\s+(?:§\s*|Part\s+)?(\d+(?:\.\d+)*)",
)

# "Section NNN(...) of the X Act"
SECT_OF_ACT_RE = re.compile(
    r"\bSection\s+(\d+[a-z]?(?:\([a-zA-Z0-9]+\))*)\s+of\s+(?:the\s+)?"
    r"([A-Z][\w&\.]*(?:\s+[A-Z][\w&\.]*){0,5}\s+Act)\b",
)

# Standalone named acts (only when not part of "Section NNN of the X Act"
# — the SECT_OF_ACT_RE will catch those; we run SECT first).
NAMED_ACT_RE = re.compile(
    r"\b(FD&C\s+Act|FDCA|FFDCA|FOIA|PHS\s+Act|PREP\s+Act|CARES\s+Act|"
    r"Public\s+Health\s+Service\s+Act|"
    r"Federal\s+Food,?\s+Drug,?\s+and\s+Cosmetic\s+Act)\b",
)

# Public Law: "Public Law 116-127" or "Pub. L. 116-127"
PUBLIC_LAW_RE = re.compile(
    r"\bPub(?:lic)?\.?\s*L(?:aw)?\.?\s+(?:No\.?\s*)?(\d+)[-–](\d+)",
)

# Court case with reporter: "Smith v. Jones, 123 F.3d 456"
# Captures: party_a, party_b, vol, reporter, page
COURT_CASE_RE = re.compile(
    r"\b([A-Z][\w&'.,-]+(?:\s+[\w&'.,-]+){0,5})\s+v\.\s+"
    r"([A-Z][\w&'.,-]+(?:\s+[\w&'.,-]+){0,5}),\s+"
    r"(\d+)\s+([A-Z][\w. ]{1,15}?)\s+(\d+)\b",
)

# EU Regulation: "Regulation (EU) No 536/2014"
EU_REG_RE = re.compile(
    r"\b(?:Regulation|Reg\.)\s+(?:\((?:EC|EU)\)\s+)?No\.?\s*(\d+)/(\d{4})",
)

# EU Directive: "Directive 2001/83/EC"
EU_DIR_RE = re.compile(r"\bDirective\s+(\d+/\d+/(?:EC|EU))\b")

# ICH guideline: "ICH E6(R2)" "ICH Q1A"
ICH_RE = re.compile(r"\bICH\s+([A-Z]\d+[A-Z]?(?:\(R\d+\))?)\b")

# WHO Technical Report Series: "WHO TRS 800"
WHO_TRS_RE = re.compile(r"\bWHO\s+TRS\s+(\d+)\b")

# ISO standard: "ISO 14155:2020"
ISO_RE = re.compile(r"\bISO\s+(\d+(?::\d{4})?)\b")


# --- Normalization helpers ---------------------------------------------------

# All these named-act forms map to "FD&C Act"
FDCA_FORMS = {
    "FD&C ACT", "FDCA", "FFDCA",
    "FEDERAL FOOD, DRUG, AND COSMETIC ACT",
    "FEDERAL FOOD DRUG AND COSMETIC ACT",
    "FEDERAL FOOD, DRUG AND COSMETIC ACT",
}
PHSA_FORMS = {"PHS ACT", "PUBLIC HEALTH SERVICE ACT"}


def normalize_named_act(raw: str) -> str:
    n = re.sub(r"\s+", " ", raw.strip()).upper()
    if n in FDCA_FORMS:
        return "FD&C Act"
    if n in PHSA_FORMS:
        return "PHS Act"
    return raw.strip()


def normalize_usc(title: str, section: str) -> str:
    return f"{title} U.S.C. § {section}"


def normalize_cfr(title: str, section: str) -> str:
    return f"{title} CFR {section}"


def normalize_section_of_act(sect: str, act: str) -> str:
    act_canonical = normalize_named_act(act)
    return f"Section {sect} of the {act_canonical}"


def normalize_public_law(congress: str, num: str) -> str:
    return f"Pub. L. {congress}-{num}"


def normalize_court_case(party_a: str, party_b: str, vol: str, reporter: str, page: str) -> str:
    return f"{party_a} v. {party_b}, {vol} {reporter.strip()} {page}"


# --- Finder ------------------------------------------------------------------


def find_all_in_page(text: str) -> list[dict]:
    """Find every statute citation on one page; return hits with family,
    normalized name, raw match, and surrounding context."""
    hits: list[dict] = []
    # Track positions we've already attributed to a richer pattern so we
    # don't double-count (e.g., "Section 564 of the FD&C Act" shouldn't
    # ALSO be counted as a bare "FD&C Act" mention).
    consumed: list[tuple[int, int]] = []

    def overlaps(start: int, end: int) -> bool:
        for s, e in consumed:
            if not (end <= s or start >= e):
                return True
        return False

    def add(family: str, normalized: str, raw: str, m: re.Match):
        start, end = m.start(), m.end()
        if overlaps(start, end):
            return
        consumed.append((start, end))
        ctx_start = max(0, start - CONTEXT_WIDTH)
        ctx_end = min(len(text), end + CONTEXT_WIDTH)
        ctx = " ".join(text[ctx_start:ctx_end].split())
        hits.append({
            "family": family,
            "normalized": normalized,
            "raw": raw,
            "context": ctx,
        })

    # Run richer patterns first to win precedence
    for m in SECT_OF_ACT_RE.finditer(text):
        sect, act = m.group(1), m.group(2)
        add("Section-of-Act", normalize_section_of_act(sect, act), m.group(0), m)

    for m in COURT_CASE_RE.finditer(text):
        a, b, vol, rep, pg = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        add("Court case", normalize_court_case(a, b, vol, rep, pg), m.group(0), m)

    for m in PUBLIC_LAW_RE.finditer(text):
        add("Public Law", normalize_public_law(m.group(1), m.group(2)), m.group(0), m)

    for m in EU_REG_RE.finditer(text):
        normalized = f"Regulation (EU) No {m.group(1)}/{m.group(2)}"
        add("International", normalized, m.group(0), m)

    for m in EU_DIR_RE.finditer(text):
        add("International", f"Directive {m.group(1)}", m.group(0), m)

    for m in ICH_RE.finditer(text):
        add("International", f"ICH {m.group(1)}", m.group(0), m)

    for m in WHO_TRS_RE.finditer(text):
        add("International", f"WHO TRS {m.group(1)}", m.group(0), m)

    for m in ISO_RE.finditer(text):
        add("International", f"ISO {m.group(1)}", m.group(0), m)

    for m in USC_RE.finditer(text):
        title, section = m.group(1), m.group(2)
        add("USC", normalize_usc(title, section), m.group(0), m)

    for m in CFR_RE.finditer(text):
        title, section = m.group(1), m.group(2)
        add("CFR", normalize_cfr(title, section), m.group(0), m)

    # Named acts go LAST — section-of-act has already claimed the
    # interesting ones; standalone matches here are the bare references
    for m in NAMED_ACT_RE.finditer(text):
        add("Named act", normalize_named_act(m.group(1)), m.group(0), m)

    return hits


# --- Scanning machinery (mirrors scan_pharmacovigilance_terms.py) -----------


def scan_pdf_bytes(pdf_bytes: bytes, filename: str) -> dict:
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    total_pages = doc.page_count
    matches: list[dict] = []
    try:
        for page_num in range(total_pages):
            page = doc.load_page(page_num)
            text = get_page_text(filename, page, page_num + 1)
            for hit in find_all_in_page(text):
                matches.append({"page": page_num + 1, **hit})
    finally:
        doc.close()
    return {"total_pages": total_pages, "matches": matches}


def load_worklist() -> list[dict]:
    idx = json.loads(INDEX.read_text())
    ican_doc = json.loads(ICAN_COMPARISON.read_text())
    ican_url_by_fname: dict[str, str] = {
        d["filename"]: d.get("ican_url", "")
        for d in ican_doc.get("ican_documents", [])
        if d.get("filename")
    }

    out: list[dict] = []
    for r in idx:
        if r.get("extension") != "pdf":
            continue
        zip_src = r.get("zip_source")
        if zip_src:
            out.append({
                "kind": "zip",
                "filename": r["filename"],
                "id": r["id"],
                "module": r.get("module"),
                "batch_code": r.get("batch_code"),
                "company": r.get("company"),
                "license": r.get("license"),
                "zip_path": ZIPS_DIR / r["batch_code"] / zip_src,
                "individual_url": r.get("individual_url"),
                "ican_url": ican_url_by_fname.get(r["filename"]),
            })
        else:
            cache_pdf = INDIVIDUAL_PDF_CACHE / r["filename"]
            if cache_pdf.exists():
                out.append({
                    "kind": "individual",
                    "filename": r["filename"],
                    "id": r["id"],
                    "module": r.get("module"),
                    "batch_code": r.get("batch_code"),
                    "company": r.get("company"),
                    "license": r.get("license"),
                    "pdf_path": cache_pdf,
                    "individual_url": r.get("individual_url"),
                    "ican_url": ican_url_by_fname.get(r["filename"]),
                })
    return out


def main(rebuild: bool = False) -> None:
    if not INDEX.exists():
        sys.exit(f"{INDEX} missing — run scripts/build_index.py first")

    CACHE.mkdir(parents=True, exist_ok=True)

    work = load_worklist()
    kinds = Counter(w["kind"] for w in work)
    print(f"In-scope PDFs: {len(work):,} ({kinds['zip']:,} zip-sourced, {kinds['individual']:,} individual)")
    print()

    by_zip: dict[Path, list[dict]] = defaultdict(list)
    individuals: list[dict] = []
    for w in work:
        if w["kind"] == "zip":
            by_zip[w["zip_path"]].append(w)
        else:
            individuals.append(w)

    results: list[dict] = []
    by_family: Counter[str] = Counter()
    by_normalized: Counter[str] = Counter()
    files_with_hits = 0
    files_errored: list[str] = []

    def process_one(item: dict, pdf_bytes: bytes | None) -> None:
        nonlocal files_with_hits
        fname = item["filename"]
        cache_path = CACHE / f"{fname}.json"
        if cache_path.exists() and not rebuild:
            scan = json.loads(cache_path.read_text())
        elif pdf_bytes is None:
            return
        else:
            scan = scan_pdf_bytes(pdf_bytes, fname)
            cache_path.write_text(json.dumps(scan, indent=2))

        record = {
            "filename": fname,
            "id": item["id"],
            "module": item.get("module"),
            "batch_code": item.get("batch_code"),
            "company": item.get("company"),
            "license": item.get("license"),
            "individual_url": item.get("individual_url"),
            "ican_url": item.get("ican_url"),
        }
        if "error" in scan:
            record["error"] = scan["error"]
            files_errored.append(fname)
        else:
            record["total_pages"] = scan["total_pages"]
            record["matches"] = scan["matches"]
            if scan["matches"]:
                files_with_hits += 1
                for m in scan["matches"]:
                    by_family[m["family"]] += 1
                    by_normalized[m["normalized"]] += 1
        results.append(record)

    pbar = tqdm(total=sum(len(rs) for rs in by_zip.values()) + len(individuals),
                desc="scanning", unit="pdf")

    for zip_path, items in by_zip.items():
        if not zip_path.exists():
            print(f"⚠️  ZIP missing, skipping {len(items)} files: {zip_path}")
            for it in items:
                pbar.update(1)
            continue

        all_cached = all(
            (CACHE / f"{it['filename']}.json").exists() for it in items
        )
        if all_cached and not rebuild:
            for it in items:
                process_one(it, pdf_bytes=None)
                pbar.update(1)
            continue

        with zipfile.ZipFile(zip_path) as zf:
            by_basename: dict[str, zipfile.ZipInfo] = {}
            for info in zf.infolist():
                if info.is_dir():
                    continue
                by_basename[info.filename.rsplit("/", 1)[-1]] = info

            for it in items:
                cache_path = CACHE / f"{it['filename']}.json"
                if cache_path.exists() and not rebuild:
                    process_one(it, pdf_bytes=None)
                else:
                    info = by_basename.get(it["filename"])
                    if info is None:
                        process_one(it, pdf_bytes=b"")
                    else:
                        with zf.open(info) as f:
                            process_one(it, pdf_bytes=f.read())
                pbar.update(1)

    for it in individuals:
        cache_path = CACHE / f"{it['filename']}.json"
        if cache_path.exists() and not rebuild:
            process_one(it, pdf_bytes=None)
        else:
            try:
                pdf_bytes = it["pdf_path"].read_bytes()
            except Exception:
                process_one(it, pdf_bytes=None)
                files_errored.append(it["filename"])
                pbar.update(1)
                continue
            process_one(it, pdf_bytes=pdf_bytes)
        pbar.update(1)

    pbar.close()

    total_citations = sum(by_family.values())
    top_statutes = dict(by_normalized.most_common(30))

    summary = {
        "in_scope_files": len(work),
        "files_processed": len(results) - len(files_errored),
        "files_with_hits": files_with_hits,
        "files_errored": files_errored,
        "total_citations": total_citations,
        "by_family": dict(sorted(by_family.items(), key=lambda kv: -kv[1])),
        "top_30_statutes": top_statutes,
    }

    OUT_JSON.write_text(json.dumps({"summary": summary, "files": results}, indent=2))

    print()
    print("=" * 60)
    print("STATUTE REFERENCE SCAN COMPLETE")
    print("=" * 60)
    print(f"In-scope files:        {len(work):,}")
    print(f"Files with citations:  {files_with_hits:,}")
    print(f"Total citations:       {total_citations:,}")
    print(f"Errored:               {len(files_errored)}")
    print()
    print("Hits per family:")
    for family, count in summary["by_family"].items():
        print(f"  {family:>16}  {count:,}")
    print()
    print("Top 10 statutes by citation count:")
    for stat, count in list(top_statutes.items())[:10]:
        print(f"  {count:>6}  {stat}")
    print()
    print(f"Wrote: {OUT_JSON}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--rebuild", action="store_true", help="ignore per-file cache")
    args = p.parse_args()
    main(rebuild=args.rebuild)
