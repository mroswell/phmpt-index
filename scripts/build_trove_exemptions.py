"""Join FDA-FOIA-2026-6007 trove membership to FOIA-exemption marker data and
build the trove exemptions dataset the site page loads.

Marker counts come from two sources, keyed per trove document:
  - PHMPT-overlapping docs  -> reuse per-file counts from docs/data/exemptions.json
                               (matched by the same normalization keys build_site.py
                               uses: exact -> Bates-stripped -> normalized).
  - NEW (non-PHMPT) docs    -> counts freshly OCR-scanned by
                               scripts/scan_trove_new_exemptions.py.

Outputs:
  FDA-FOIA-2026-6007/site/exemptions.json   (loaded by exemptions.html/.js)
  FDA-FOIA-2026-6007/exemptions_report.md   (repo-side summary)

Run:  uv run python scripts/build_trove_exemptions.py
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "FDA-FOIA-2026-6007" / "site"
DOCS_JSON = SITE / "documents.json"
PHMPT_EXEMPTIONS = ROOT / "docs" / "data" / "exemptions.json"
INDIVIDUAL_EXEMPTIONS = [
    ROOT / "data" / "individual_phmpt_exemptions.json",
    ROOT / "data" / "individual_exemptions.json",
]
NEW_EXEMPTIONS = ROOT / "data" / "trove_new_exemptions.json"
NEW_CACHE = ROOT / "data" / "cache" / "trove_new_exemptions"
OUT_JSON = SITE / "exemptions.json"
OUT_MD = ROOT / "FDA-FOIA-2026-6007" / "exemptions_report.md"

# --- filename normalization, identical to build_site.py --------------------
BATES = re.compile(r'^(?:~\$)?(?:bates-)?fda-cber-\d+-\d+-\s*\d+\s*[-–—]?\s*\d*[ _to–-]*', re.I)
SUBM = re.compile(r'^0*\d+(?:-\d+)?_s0*\d+_m[0-9.]+[._-]', re.I)


def base(fn: str) -> str:
    return os.path.basename(fn)


def k_exact(fn: str) -> str:
    return base(fn).lower()


def k_bates(fn: str) -> str:
    return BATES.sub('', base(fn)).lower().strip()


def k_norm(fn: str) -> str:
    s = k_bates(fn)
    s = SUBM.sub('', s)
    s = re.sub(r'\s*\(\d+\)', '', s)
    s = re.sub(r'\b0+(\d)', r'\1', s)
    s = re.sub(r'\.(pdf|xpt|txt|docx?|xlsx?|xml|xsl|jmp|png|jpg)$', '', s)
    s = re.sub(r'[ _\-]', '', s)
    return s


# --- load PHMPT per-file exemption records ---------------------------------
def _rec_from_pages(r: dict) -> dict:
    """Individual-exemption datasets store per-page markers; fold to by_marker."""
    by = Counter()
    for ep in r.get("exemption_pages", []):
        by[ep["marker"]] += ep.get("count", 1)
    return {"total_markers": sum(by.values()), "by_marker": dict(by),
            "total_pages": r.get("total_pages")}


def load_phmpt_lookup() -> tuple[dict, dict, dict]:
    ex, ba, no = {}, {}, {}

    def add(fn, rec):
        if not fn:
            return
        ex.setdefault(k_exact(fn), rec)
        ba.setdefault(k_bates(fn), rec)
        no.setdefault(k_norm(fn), rec)

    for r in json.loads(PHMPT_EXEMPTIONS.read_text())["files"]:
        add(r.get("filename"), {
            "total_markers": r.get("total_markers", 0),
            "by_marker": r.get("by_marker", {}),
            "total_pages": r.get("total_pages"),
        })
    for path in INDIVIDUAL_EXEMPTIONS:
        if path.exists():
            for r in json.loads(path.read_text())["files"]:
                add(r.get("filename"), _rec_from_pages(r))
    return ex, ba, no


def load_new_lookup() -> dict:
    """filename -> new-scan record. Prefer the aggregate; fall back to cache."""
    if NEW_EXEMPTIONS.exists():
        data = json.loads(NEW_EXEMPTIONS.read_text())
        return {r["filename"]: r for r in data["files"]}
    out = {}
    if NEW_CACHE.exists():
        for p in NEW_CACHE.glob("*.json"):
            r = json.loads(p.read_text())
            out[r["filename"]] = r
    return out


def resolve(doc, ex, ba, no, new) -> dict:
    fn = doc["filename"]
    if doc.get("status") == "NEW":
        r = new.get(fn)
        if r is not None:
            return {"source": "new", "total_markers": r.get("total_markers", 0),
                    "by_marker": r.get("by_marker", {}),
                    "total_pages": r.get("total_pages"),
                    "ocr_pages": len(r.get("ocr_candidate_pages", [])),
                    "error": r.get("error")}
        return {"source": "none", "total_markers": 0, "by_marker": {},
                "total_pages": None, "ocr_pages": 0, "error": "new scan missing"}
    for keyfn, d in ((k_exact, ex), (k_bates, ba), (k_norm, no)):
        r = d.get(keyfn(fn))
        if r is not None:
            return {"source": "phmpt", "total_markers": r["total_markers"],
                    "by_marker": r["by_marker"], "total_pages": r.get("total_pages"),
                    "ocr_pages": 0, "error": None}
    # Non-PDF files carry no text redaction markers; not a coverage gap.
    if (doc.get("ext") or "").lower() != "pdf":
        return {"source": "data", "total_markers": 0, "by_marker": {},
                "total_pages": None, "ocr_pages": 0, "error": None}
    return {"source": "none", "total_markers": 0, "by_marker": {},
            "total_pages": None, "ocr_pages": 0, "error": "no phmpt match"}


def crosstab(rows, dim, unknown="Unknown"):
    """exemption x dim value -> counts, plus files and markers-total rows."""
    col_markers = defaultdict(lambda: Counter())   # dimval -> Counter(marker->n)
    col_files = Counter()
    for r in rows:
        v = r.get(dim) or unknown
        col_files[v] += 1
        for m, c in r["by_marker"].items():
            col_markers[v][m] += c
    columns = sorted(col_files, key=lambda v: -sum(col_markers[v].values()))
    markers = sorted({m for v in col_markers for m in col_markers[v]},
                     key=lambda m: -sum(col_markers[v][m] for v in col_markers))
    table = []
    for m in markers:
        counts = {v: col_markers[v].get(m, 0) for v in columns}
        table.append({"exemption": m, "counts": counts,
                      "total": sum(counts.values())})
    return {
        "columns": columns,
        "rows": table,
        "files": {v: col_files[v] for v in columns},
        "markers_total": {v: sum(col_markers[v].values()) for v in columns},
    }


def main() -> None:
    docs = json.loads(DOCS_JSON.read_text())
    ex, ba, no = load_phmpt_lookup()
    new = load_new_lookup()

    rows = []
    for d in docs:
        info = resolve(d, ex, ba, no, new)
        rows.append({
            "filename": d["filename"],
            "status": d.get("status"),
            "category": d.get("category") or "Other",
            "module": d.get("module") or "Unknown",
            "bimo": bool(d.get("bimo")),
            "ext": d.get("ext"),
            "doc_link": d.get("primary") or d.get("doc_link"),
            "source": info["source"],
            "total_markers": info["total_markers"],
            "by_marker": info["by_marker"],
            "total_pages": info["total_pages"],
            "error": info["error"],
        })

    by_marker_total = Counter()
    for r in rows:
        by_marker_total.update(r["by_marker"])

    summary = {
        "files": len(rows),
        "files_with_markers": sum(1 for r in rows if r["total_markers"]),
        "total_marker_occurrences": sum(r["total_markers"] for r in rows),
        "by_marker": dict(by_marker_total.most_common()),
        "by_source": dict(Counter(r["source"] for r in rows)),
        "new_files": sum(1 for r in rows if r["status"] == "NEW"),
        "new_files_with_markers": sum(1 for r in rows
                                      if r["status"] == "NEW" and r["total_markers"]),
        "unresolved": [r["filename"] for r in rows if r["source"] == "none"][:50],
        "unresolved_count": sum(1 for r in rows if r["source"] == "none"),
    }

    crosstabs = {
        "status": crosstab(rows, "status"),
        "module": crosstab(rows, "module"),
        "category": crosstab(rows, "category"),
        "bimo": crosstab([{**r, "bimo": "BIMO" if r["bimo"] else "Non-BIMO"}
                          for r in rows], "bimo"),
    }

    # rare = everything but the two dominant markers
    dominant = {"(b)(4)", "(b)(6)"}
    rare = []
    for m, total in by_marker_total.most_common():
        if m in dominant:
            continue
        examples = sorted((r for r in rows if m in r["by_marker"]),
                          key=lambda r: -r["by_marker"][m])[:12]
        rare.append({
            "exemption": m,
            "total": total,
            "files": sum(1 for r in rows if m in r["by_marker"]),
            "examples": [{"filename": e["filename"], "status": e["status"],
                          "count": e["by_marker"][m], "doc_link": e["doc_link"]}
                         for e in examples],
        })

    OUT_JSON.write_text(json.dumps({
        "summary": summary, "crosstabs": crosstabs, "rare": rare,
        "files": [{k: r[k] for k in ("filename", "status", "category", "module",
                                     "bimo", "ext", "source", "total_markers",
                                     "by_marker", "total_pages", "doc_link")}
                  for r in rows],
    }))

    _write_md(summary, crosstabs, rare)

    print(f"wrote {OUT_JSON}")
    print(f"  files {summary['files']}  with markers {summary['files_with_markers']}"
          f"  total markers {summary['total_marker_occurrences']:,}")
    print(f"  by source: {summary['by_source']}")
    print(f"  NEW with markers: {summary['new_files_with_markers']}/{summary['new_files']}")
    print(f"  unresolved: {summary['unresolved_count']}")


def _write_md(summary, crosstabs, rare):
    L = ["# FDA-FOIA-2026-6007 Trove — FOIA Exemption Report", ""]
    L.append(f"Source: `FDA-FOIA-2026-6007/site/documents.json` "
             f"({summary['files']:,} documents). Marker counts for "
             f"PHMPT-overlapping documents are reused from the coviddocuments.com "
             f"exemption scan; the {summary['new_files']} NEW documents were "
             f"OCR-scanned fresh for this report.")
    L.append("")
    L.append(f"- Total marker occurrences: **{summary['total_marker_occurrences']:,}**")
    L.append(f"- Files with markers: **{summary['files_with_markers']:,}**")
    L.append(f"- By source: {summary['by_source']}")
    L.append(f"- NEW docs with markers: {summary['new_files_with_markers']}"
             f"/{summary['new_files']}")
    L.append("")
    for name, ct in crosstabs.items():
        L.append(f"## Exemption × {name}")
        L.append("")
        head = "| Exemption | " + " | ".join(ct["columns"]) + " | **Total** |"
        L.append(head)
        L.append("| --- | " + " | ".join("---:" for _ in ct["columns"]) + " | ---: |")
        for row in ct["rows"]:
            cells = " | ".join(f"{row['counts'][c]:,}" for c in ct["columns"])
            L.append(f"| `{row['exemption']}` | {cells} | **{row['total']:,}** |")
        fcells = " | ".join(f"*{ct['files'][c]:,}*" for c in ct["columns"])
        L.append(f"| *Files* | {fcells} | *{sum(ct['files'].values()):,}* |")
        L.append("")
    L.append("## Rare exemptions")
    L.append("")
    for r in rare:
        L.append(f"- `{r['exemption']}` — {r['total']:,} occurrences "
                 f"across {r['files']} files")
    OUT_MD.write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
