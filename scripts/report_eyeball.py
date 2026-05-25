"""Generate two human-readable "eyeball" reports.

Output 1 — data/rare_exemptions_report.md
  Every occurrence of the 5 rarely-used exemption types:
  (b)(1), (b)(2), (b)(5), (b)(1)(C), (b)(7)(A).
  For each occurrence: file + page numbers + PHMPT/ICAN links.

Output 2 — data/per_file_report_non_m5.md
  Sorted per-file table for all non-M5 modules. The cross-tab report
  showed surprisingly high markers/file rates dominated by M5; this
  view lets you eyeball M1–M4 file-by-file.

Reads:
- data/M1..M4_exemptions.json  (page-level detail, gitignored, regenerable)
- data/exemptions.json          (slim per-file rollups, committed)
- docs/data/index.json          (for PHMPT URLs)
- data/ican_comparison.json     (for ICAN URLs)

Run:
    uv run python scripts/report_eyeball.py
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
INDEX = ROOT / "docs" / "data" / "index.json"
ICAN = DATA / "ican_comparison.json"
SLIM = DATA / "exemptions.json"

RARE = {"(b)(1)", "(b)(2)", "(b)(5)", "(b)(1)(C)", "(b)(7)(A)"}

# Human-friendly description of each exemption type, used in section headers.
EXEMPTION_DESC = {
    "(b)(1)": "Classified information",
    "(b)(2)": "Internal personnel rules and practices",
    "(b)(5)": "Deliberative process / attorney privilege",
    "(b)(1)(C)": "Classified information (subpart C)",
    "(b)(7)(A)": "Law enforcement records — ongoing investigations",
}

OUT_RARE = DATA / "rare_exemptions_report.md"
OUT_PERFILE = DATA / "per_file_report_non_m5.md"


def load_url_map() -> tuple[dict[str, dict], dict[str, str]]:
    """Return (phmpt_url_by_filename, ican_url_by_filename).

    phmpt entries are dicts with keys: individual_url, zip_url, zip_source.
    """
    idx = json.loads(INDEX.read_text())
    phmpt: dict[str, dict] = {}
    for row in idx:
        fname = row.get("filename")
        if not fname:
            continue
        # Prefer the first record with an individual_url; otherwise keep whatever
        # we have (some filenames appear in multiple records — picking by
        # availability of individual_url makes the link more useful).
        existing = phmpt.get(fname)
        if existing is None or (not existing.get("individual_url") and row.get("individual_url")):
            phmpt[fname] = {
                "individual_url": row.get("individual_url"),
                "zip_url": row.get("zip_url"),
                "zip_source": row.get("zip_source"),
            }

    ican_doc = json.loads(ICAN.read_text())
    ican: dict[str, str] = {}
    for d in ican_doc.get("ican_documents", []):
        fname = d.get("filename")
        if fname and fname not in ican:
            ican[fname] = d.get("ican_url", "")

    return phmpt, ican


def link_cell(filename: str, phmpt_url_map: dict, ican_url_map: dict) -> str:
    """Build a markdown-cell of clickable links for a filename."""
    parts = []
    p = phmpt_url_map.get(filename, {})
    if p.get("individual_url"):
        parts.append(f"[PHMPT]({p['individual_url']})")
    elif p.get("zip_url"):
        parts.append(f"[PHMPT zip]({p['zip_url']})")
    ican_url = ican_url_map.get(filename)
    if ican_url:
        parts.append(f"[ICAN]({ican_url})")
    return " · ".join(parts) if parts else "—"


def fmt_pages(pages: list[int]) -> str:
    """Compact list of page numbers. E.g., [3,4,5,8] -> '3-5, 8'."""
    if not pages:
        return ""
    pages = sorted(set(pages))
    out: list[str] = []
    start = prev = pages[0]
    for p in pages[1:]:
        if p == prev + 1:
            prev = p
            continue
        out.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = p
    out.append(str(start) if start == prev else f"{start}-{prev}")
    return ", ".join(out)


# --- Report 1: rare exemptions ---------------------------------------------


def build_rare_report(phmpt_url_map: dict, ican_url_map: dict) -> None:
    # Collect (exemption -> list of (filename, module, company, license, pages))
    by_exemption: dict[str, list[dict]] = defaultdict(list)

    for mod in ("M1", "M2", "M3", "M4", "M5"):
        path = DATA / f"{mod}_exemptions.json"
        if not path.exists():
            continue
        doc = json.loads(path.read_text())
        for f in doc.get("files", []):
            ex_pages = f.get("exemption_pages") or []
            # Group this file's hits by marker type
            per_marker: dict[str, list[int]] = defaultdict(list)
            for entry in ex_pages:
                m = entry.get("marker")
                if m in RARE:
                    # Each entry already represents a unique page; preserve the
                    # count so a page with multiple hits shows e.g. "5 (x3)".
                    per_marker[m].append((entry.get("page"), entry.get("count", 1)))
            for marker, page_hits in per_marker.items():
                by_exemption[marker].append({
                    "filename": f["filename"],
                    "module": f.get("module") or mod,
                    "company": f.get("company"),
                    "license": f.get("license"),
                    "batch_code": f.get("batch_code"),
                    "page_hits": sorted(page_hits, key=lambda ph: (ph[0] or 0)),
                })

    total_occurrences = sum(
        sum(c for _, c in r["page_hits"])
        for rows in by_exemption.values()
        for r in rows
    )
    total_files = sum(len(rows) for rows in by_exemption.values())

    lines: list[str] = [
        "# Rare FOIA Exemptions — Eyeball Report",
        "",
        f"Every occurrence of the 5 rarely-used exemption types across all "
        f"five modules. **{total_occurrences} occurrences across "
        f"{total_files} file–marker combinations** (a single file may appear "
        f"in multiple sections if it uses more than one rare type).",
        "",
        "The dominant `(b)(4)` (trade secrets) and `(b)(6)` (personal privacy) "
        "markers are excluded — see `data/exemptions_report.md` for those.",
        "",
        "Each row links to the file on PHMPT (the original source; clicking opens "
        "the PDF in your browser — phmpt.org may show a one-time Cloudflare "
        "challenge) and on ICAN (re-host, usually loads directly).",
        "",
        "---",
        "",
    ]

    # Order sections by total count desc, but list (b)(5) explicitly since it's
    # the one many people care about (deliberative process / attorney privilege).
    section_order = sorted(
        by_exemption.keys(),
        key=lambda m: -sum(c for _, c in sum((r["page_hits"] for r in by_exemption[m]), []))
    )

    for marker in section_order:
        rows = by_exemption[marker]
        total = sum(c for r in rows for _, c in r["page_hits"])
        desc = EXEMPTION_DESC.get(marker, "")
        lines.append(f"## `{marker}` — {desc}")
        lines.append("")
        lines.append(f"**{total} occurrence(s) across {len(rows)} file(s).**")
        lines.append("")
        lines.append("| File | Module | Company | License | Pages | Links |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        # Sort by module then by total hits in this file desc
        rows = sorted(
            rows,
            key=lambda r: (r["module"], -sum(c for _, c in r["page_hits"]), r["filename"]),
        )
        for r in rows:
            pages_only = [p for p, _ in r["page_hits"]]
            total_in_file = sum(c for _, c in r["page_hits"])
            page_str = fmt_pages(pages_only)
            if total_in_file > len(pages_only):
                page_str += f" *(×{total_in_file} hits)*"
            links = link_cell(r["filename"], phmpt_url_map, ican_url_map)
            lines.append(
                f"| `{r['filename']}` | {r['module']} | "
                f"{r.get('company') or '—'} | {r.get('license') or '—'} | "
                f"{page_str} | {links} |"
            )
        lines.append("")

    OUT_RARE.write_text("\n".join(lines))
    print(f"Wrote: {OUT_RARE}  ({total_files} rows, {total_occurrences} occurrences)")


# --- Report 2: per-file non-M5 ----------------------------------------------


def build_perfile_non_m5(phmpt_url_map: dict, ican_url_map: dict) -> None:
    # We need page_count for "markers/page" — that's in docs/data/index.json
    idx = json.loads(INDEX.read_text())
    pages_by_id = {row["id"]: row.get("page_count") for row in idx}

    slim = json.loads(SLIM.read_text())
    files = [f for f in slim["files"] if f.get("module") and f["module"] != "M5"]

    # Augment with page_count and rate
    for f in files:
        f["page_count"] = pages_by_id.get(f.get("id"))
        pc = f["page_count"]
        f["markers_per_page"] = (
            (f.get("total_markers", 0) / pc) if pc else None
        )

    # Sort by total_markers desc, then markers/page desc
    files.sort(key=lambda f: (-f.get("total_markers", 0), -(f.get("markers_per_page") or 0)))

    total_markers = sum(f.get("total_markers", 0) for f in files)

    # Module breakdown for the intro
    by_mod = Counter(f["module"] for f in files)
    with_markers = sum(1 for f in files if f.get("total_markers", 0) > 0)

    lines: list[str] = [
        "# Per-File Exemption Report — M1 through M4 (M5 excluded)",
        "",
        f"**{len(files):,} files** across modules "
        + ", ".join(f"{m}: {by_mod[m]:,}" for m in sorted(by_mod))
        + ".",
        "",
        f"**{with_markers:,} files contain at least one redaction marker**; "
        f"{total_markers:,} total marker occurrences (Σ across all non-M5 files).",
        "",
        "Sorted by total markers desc. The `Markers/page` column normalizes "
        "for document size so you can compare a 5-page admin doc against a "
        "200-page protocol.",
        "",
        "Top exemption in each row is whichever has the highest count; ties "
        "broken alphabetically.",
        "",
        "---",
        "",
        "| File | Module | Company | License | Pages | Markers | Markers / page | Top | Links |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- |",
    ]

    for f in files:
        bym = f.get("by_marker") or {}
        if bym:
            top_marker = max(bym.items(), key=lambda kv: (kv[1], -ord(kv[0][3])))[0]
        else:
            top_marker = "—"

        rate = f.get("markers_per_page")
        rate_str = f"{rate:,.1f}" if rate else "—"
        pages = f.get("page_count")
        pages_str = f"{pages:,}" if pages else "—"
        markers = f.get("total_markers", 0)
        markers_str = f"{markers:,}" if markers else "0"
        links = link_cell(f["filename"], phmpt_url_map, ican_url_map)

        lines.append(
            f"| `{f['filename']}` | {f['module']} | "
            f"{f.get('company') or '—'} | {f.get('license') or '—'} | "
            f"{pages_str} | {markers_str} | {rate_str} | "
            f"`{top_marker}` | {links} |"
        )

    OUT_PERFILE.write_text("\n".join(lines))
    print(f"Wrote: {OUT_PERFILE}  ({len(files)} rows, {total_markers:,} total markers)")


def main() -> None:
    phmpt, ican = load_url_map()
    print(f"Loaded {len(phmpt):,} PHMPT URLs, {len(ican):,} ICAN URLs")
    print()
    build_rare_report(phmpt, ican)
    build_perfile_non_m5(phmpt, ican)


if __name__ == "__main__":
    main()
