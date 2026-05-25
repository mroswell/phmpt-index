"""Search every accessible PDF for six pharmacovigilance terms.

In-scope:
- M1 + M2 + M4 + M5 PDFs sourced from ZIPs in data/zips/
- 232 individual PDFs already downloaded by
  scripts/scan_individual_via_ican.py (cached at data/cache/individual_pdfs/)

Out of scope:
- M3 (CMC/manufacturing — pharmacovigilance vocabulary doesn't overlap)
- The 168 PDFs unreachable on both PHMPT and ICAN

Six terms (and their regexes):
- Pharmacovigilance plan          (?i)pharmacovigilance\\s+plan
- Disproportionality analysis     (?i)disproportionality\\s+analysis
- Risk management plan            (?i)risk\\s+management\\s+plan
- PRR                             \\bPRR\\b   (case-sensitive)
- Proportional Reporting Ratio    (?i)proportional\\s+reporting\\s+ratio
- Bayesian                        (?i)\\bbayesian\\b

For each hit we capture page number + ~80 chars of surrounding context.

Per-file cache: data/cache/pharmacovigilance/{filename}.json
Aggregate:      data/pharmacovigilance.json (committable)
Report:         data/pharmacovigilance_report.md (committable)

Run:
    uv run python scripts/scan_pharmacovigilance_terms.py
    uv run python scripts/scan_pharmacovigilance_terms.py --include-m3
    uv run python scripts/scan_pharmacovigilance_terms.py --rebuild
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
CACHE = DATA / "cache" / "pharmacovigilance"
INDIVIDUAL_PDF_CACHE = DATA / "cache" / "individual_pdfs"
# Web-facing outputs live in docs/data/ so the pharmacovigilance.html
# browse page can fetch them with a relative URL.
OUT_JSON = ROOT / "docs" / "data" / "pharmacovigilance.json"
OUT_MD = ROOT / "docs" / "data" / "pharmacovigilance_report.md"

# Term name -> compiled regex.
# Order in the dict drives section order in the report.
TERMS: dict[str, re.Pattern] = {
    "Pharmacovigilance plan": re.compile(r"(?i)pharmacovigilance\s+plan"),
    "Disproportionality analysis": re.compile(r"(?i)disproportionality\s+analysis"),
    "Risk management plan": re.compile(r"(?i)risk\s+management\s+plan"),
    "PRR": re.compile(r"\bPRR\b"),  # case-sensitive
    "Proportional Reporting Ratio": re.compile(r"(?i)proportional\s+reporting\s+ratio"),
    "Bayesian": re.compile(r"(?i)\bbayesian\b"),
}

CONTEXT_WIDTH = 80  # chars on each side of the match


def find_matches(text: str) -> list[dict]:
    """Find all term matches in one page's text. Returns [{term, context}, ...]."""
    hits: list[dict] = []
    for term_name, pat in TERMS.items():
        for m in pat.finditer(text):
            start = max(0, m.start() - CONTEXT_WIDTH)
            end = min(len(text), m.end() + CONTEXT_WIDTH)
            ctx = text[start:end]
            # collapse whitespace so the snippet fits one markdown table cell
            ctx = " ".join(ctx.split())
            hits.append({"term": term_name, "context": ctx})
    return hits


def scan_pdf_bytes(pdf_bytes: bytes, filename: str) -> dict:
    """Open PDF; return {total_pages, matches: [{page, term, context}, ...]}."""
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
            for hit in find_matches(text):
                matches.append({"page": page_num + 1, **hit})
    finally:
        doc.close()
    return {"total_pages": total_pages, "matches": matches}


def load_worklist(include_m3: bool) -> list[dict]:
    """Build the list of PDFs to scan."""
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
        mod = r.get("module")
        if not include_m3 and mod == "M3":
            continue
        zip_src = r.get("zip_source")
        if zip_src:
            # In-zip; include if it has a recognized module
            if mod and mod.startswith("M"):
                out.append({
                    "kind": "zip",
                    "filename": r["filename"],
                    "id": r["id"],
                    "module": mod,
                    "batch_code": r.get("batch_code"),
                    "company": r.get("company"),
                    "license": r.get("license"),
                    "zip_path": ZIPS_DIR / r["batch_code"] / zip_src,
                    "individual_url": r.get("individual_url"),
                    "ican_url": ican_url_by_fname.get(r["filename"]),
                })
        else:
            # Individual PDF; include if it has a cached download from Task A
            cache_pdf = INDIVIDUAL_PDF_CACHE / r["filename"]
            if cache_pdf.exists():
                out.append({
                    "kind": "individual",
                    "filename": r["filename"],
                    "id": r["id"],
                    "module": mod,
                    "batch_code": r.get("batch_code"),
                    "company": r.get("company"),
                    "license": r.get("license"),
                    "pdf_path": cache_pdf,
                    "individual_url": r.get("individual_url"),
                    "ican_url": ican_url_by_fname.get(r["filename"]),
                })
    return out


def main(include_m3: bool = False, rebuild: bool = False) -> None:
    if not INDEX.exists():
        sys.exit(f"{INDEX} missing — run scripts/build_index.py first")

    CACHE.mkdir(parents=True, exist_ok=True)

    work = load_worklist(include_m3)
    kinds = Counter(w["kind"] for w in work)
    print(f"In-scope PDFs: {len(work):,} ({kinds['zip']:,} zip-sourced, {kinds['individual']:,} individual)")
    print(f"M3 included: {include_m3}")
    print()

    # Group zip-sourced files by their containing ZIP so each archive opens once
    by_zip: dict[Path, list[dict]] = defaultdict(list)
    individuals: list[dict] = []
    for w in work:
        if w["kind"] == "zip":
            by_zip[w["zip_path"]].append(w)
        else:
            individuals.append(w)

    results: list[dict] = []
    by_term: Counter[str] = Counter()
    files_with_hits = 0
    files_errored: list[str] = []

    def process_one(item: dict, pdf_bytes: bytes | None) -> None:
        nonlocal files_with_hits
        fname = item["filename"]
        cache_path = CACHE / f"{fname}.json"
        if cache_path.exists() and not rebuild:
            scan = json.loads(cache_path.read_text())
        elif pdf_bytes is None:
            return  # nothing to scan and no cache
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
                    by_term[m["term"]] += 1
        results.append(record)

    # Process zip-sourced files (open each zip once)
    pbar = tqdm(total=sum(len(rs) for rs in by_zip.values()) + len(individuals),
                desc="scanning", unit="pdf")

    for zip_path, items in by_zip.items():
        if not zip_path.exists():
            print(f"⚠️  ZIP missing, skipping {len(items)} files: {zip_path}")
            for it in items:
                pbar.update(1)
            continue

        # If everything in this zip is already cached, don't open it
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
                        process_one(it, pdf_bytes=b"")  # will be handled as scan error
                    else:
                        with zf.open(info) as f:
                            process_one(it, pdf_bytes=f.read())
                pbar.update(1)

    # Process individual PDFs (read each from local cache)
    for it in individuals:
        cache_path = CACHE / f"{it['filename']}.json"
        if cache_path.exists() and not rebuild:
            process_one(it, pdf_bytes=None)
        else:
            try:
                pdf_bytes = it["pdf_path"].read_bytes()
            except Exception as e:
                process_one(it, pdf_bytes=None)
                files_errored.append(it["filename"])
                pbar.update(1)
                continue
            process_one(it, pdf_bytes=pdf_bytes)
        pbar.update(1)

    pbar.close()

    summary = {
        "in_scope_files": len(work),
        "files_processed": len(results) - len(files_errored),
        "files_with_hits": files_with_hits,
        "files_errored": files_errored,
        "by_term": dict(sorted(by_term.items(), key=lambda kv: -kv[1])),
        "total_term_occurrences": sum(by_term.values()),
        "m3_included": include_m3,
    }

    # Write JSON
    OUT_JSON.write_text(json.dumps({"summary": summary, "files": results}, indent=2))

    # Write markdown report
    _write_report(summary, results)

    # Console summary
    print()
    print("=" * 60)
    print("PHARMACOVIGILANCE TERM SCAN COMPLETE")
    print("=" * 60)
    print(f"In-scope files:        {len(work):,}")
    print(f"Files with hits:       {files_with_hits:,}")
    print(f"Total term occurrences: {sum(by_term.values()):,}")
    print(f"Errored:               {len(files_errored)}")
    print()
    print("Hits per term:")
    for term, count in summary["by_term"].items():
        print(f"  {term:>32}  {count:,}")
    print()
    print(f"Wrote: {OUT_JSON}")
    print(f"Wrote: {OUT_MD}")


def _link_cell(item: dict) -> str:
    parts = []
    if item.get("individual_url"):
        parts.append(f"[PHMPT]({item['individual_url']})")
    if item.get("ican_url"):
        parts.append(f"[ICAN]({item['ican_url']})")
    return " · ".join(parts) if parts else "—"


def _write_report(summary: dict, results: list[dict]) -> None:
    # Group hits per term
    hits_by_term: dict[str, list[tuple[dict, int, str]]] = defaultdict(list)
    for f in results:
        for m in f.get("matches", []) or []:
            hits_by_term[m["term"]].append((f, m["page"], m["context"]))

    lines: list[str] = [
        "# Pharmacovigilance Term Scan",
        "",
        f"Scanned **{summary['files_processed']:,} PDFs** "
        f"(M3 {'included' if summary['m3_included'] else 'excluded'}; "
        f"{summary['in_scope_files']:,} in scope total). "
        f"**{summary['files_with_hits']:,} files contain at least one matching term**; "
        f"{summary['total_term_occurrences']:,} total term occurrences.",
        "",
        "Each section lists files containing that term, sorted by hit count. "
        "Context snippets are ~80 characters around each match. Click PHMPT "
        "(may show one-time Cloudflare challenge) or ICAN (no challenge) to open the PDF.",
        "",
        "**Term hit summary:**",
        "",
        "| Term | Files | Total occurrences |",
        "| --- | ---: | ---: |",
    ]
    files_per_term: Counter[str] = Counter()
    for term, hits in hits_by_term.items():
        files_per_term[term] = len({f["filename"] for f, _, _ in hits})
    for term in TERMS.keys():
        lines.append(
            f"| {term} | {files_per_term.get(term, 0):,} | {summary['by_term'].get(term, 0):,} |"
        )
    lines.append("")
    lines.append("---")
    lines.append("")

    for term in TERMS.keys():
        hits = hits_by_term.get(term, [])
        lines.append(f"## {term}")
        lines.append("")
        if not hits:
            lines.append("*No hits.*")
            lines.append("")
            continue

        # Group by file, count hits per file, get earliest pages
        per_file: dict[str, dict] = {}
        for item, page, ctx in hits:
            fname = item["filename"]
            if fname not in per_file:
                per_file[fname] = {
                    "item": item,
                    "pages": [],
                    "first_contexts": [],
                }
            per_file[fname]["pages"].append(page)
            if len(per_file[fname]["first_contexts"]) < 2:
                per_file[fname]["first_contexts"].append((page, ctx))

        # Sort files by hit count desc
        ordered = sorted(per_file.values(), key=lambda f: -len(f["pages"]))
        lines.append(f"**{len(ordered):,} file(s), {sum(len(f['pages']) for f in ordered):,} hit(s).**")
        lines.append("")
        lines.append("| File | Module | Company | License | Hits | Pages | Links |")
        lines.append("| --- | --- | --- | --- | ---: | --- | --- |")
        for f in ordered:
            it = f["item"]
            pages = sorted(set(f["pages"]))
            pages_str = ", ".join(str(p) for p in pages[:8])
            if len(pages) > 8:
                pages_str += f", … (+{len(pages) - 8})"
            lines.append(
                f"| `{it['filename']}` | {it.get('module') or '—'} | "
                f"{it.get('company') or '—'} | {it.get('license') or '—'} | "
                f"{len(f['pages'])} | {pages_str} | {_link_cell(it)} |"
            )
        # Top-3 context snippets across this term to give readers a feel
        lines.append("")
        lines.append("<details><summary>Example context snippets (first 3)</summary>")
        lines.append("")
        flat = []
        for f in ordered:
            for page, ctx in f["first_contexts"]:
                flat.append((f["item"]["filename"], page, ctx))
        for fname, page, ctx in flat[:3]:
            lines.append(f"- **`{fname}` p{page}** — *…{ctx}…*")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    OUT_MD.write_text("\n".join(lines))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--include-m3", action="store_true", help="also scan M3 (default: skip)")
    p.add_argument("--rebuild", action="store_true", help="ignore per-file cache")
    args = p.parse_args()
    main(include_m3=args.include_m3, rebuild=args.rebuild)
