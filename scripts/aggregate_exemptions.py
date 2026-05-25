"""Combine per-module exemption JSONs into one cross-module catalog.

Reads data/{M1,M2,M3,M4,M5}_exemptions.json and writes
data/exemptions.json with:
- a roll-up summary (totals + per-module + per-exemption)
- the full union of file records

Usage:
    uv run python scripts/aggregate_exemptions.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
MODULES = ["M1", "M2", "M3", "M4", "M5"]
# Optional extra sources: individual PDFs (no zip_source) scanned via
# ICAN (scan_individual_via_ican.py) and via PHMPT with curl_cffi
# (scan_individual_via_phmpt.py).
INDIVIDUAL_SOURCES = [
    DATA / "individual_exemptions.json",
    DATA / "individual_phmpt_exemptions.json",
]
OUT = DATA / "exemptions.json"


def main() -> None:
    per_module = {}
    all_files: list[dict] = []
    by_marker_overall: Counter[str] = Counter()

    for mod in MODULES:
        path = DATA / f"{mod}_exemptions.json"
        if not path.exists():
            print(f"⚠️  {path.name} missing — skipping {mod}")
            continue
        doc = json.loads(path.read_text())
        s = doc["summary"]
        per_module[mod] = {
            "files_in_index": s.get("files_in_index", 0),
            "files_processed": s.get("files_processed", 0),
            "files_with_markers": s.get("files_with_exemptions", 0),
            "files_ocr_flagged": s.get("files_ocr_flagged", 0),
            "files_errored": len(s.get("files_errored", [])),
            "files_skipped_no_zip": len(s.get("files_skipped_no_zip", [])),
            "total_marker_occurrences": s.get("total_marker_occurrences", 0),
            "by_exemption": s.get("by_exemption", {}),
        }
        # Slim per-file record: roll up page-level markers into per-marker counts
        # to keep the aggregate file small enough to commit (M5 alone has
        # 11M page-level entries → 400+ MB at full detail).
        # For per-page detail, read data/{mod}_exemptions.json or
        # data/cache/exemptions/{mod}/{filename}.json directly.
        for f in doc.get("files", []):
            by_marker_f: Counter[str] = Counter()
            for entry in f.get("exemption_pages", []):
                by_marker_f[entry["marker"]] += entry["count"]
            slim = {
                "id": f.get("id"),
                "filename": f.get("filename"),
                "module": f.get("module") or mod,
                "batch_code": f.get("batch_code"),
                "company": f.get("company"),
                "license": f.get("license"),
                "total_pages": f.get("total_pages"),
                "total_markers": sum(by_marker_f.values()),
                "by_marker": dict(sorted(by_marker_f.items(), key=lambda kv: -kv[1])),
                "ocr_candidate_pages_count": len(f.get("ocr_candidate_pages", [])),
            }
            if f.get("error"):
                slim["error"] = f["error"]
            if f.get("skipped"):
                slim["skipped"] = f["skipped"]
            all_files.append(slim)

        for marker, count in s.get("by_exemption", {}).items():
            by_marker_overall[marker] += count

    # Ingest individual-via-(ICAN|PHMPT) scan results, if present
    individual_summaries: dict[str, dict] = {}
    for path in INDIVIDUAL_SOURCES:
        if not path.exists():
            continue
        source_tag = path.stem  # e.g. "individual_exemptions" or "individual_phmpt_exemptions"
        doc = json.loads(path.read_text())
        s = doc["summary"]
        individual_summaries[source_tag] = {
            "files_in_scope": s.get("files_in_scope", 0),
            "files_processed": s.get("files_processed", 0),
            "files_with_markers": s.get("files_with_exemptions", 0),
            "files_ocr_flagged": s.get("files_ocr_flagged", 0),
            "files_errored": len(s.get("files_errored", [])),
            "total_marker_occurrences": s.get("total_marker_occurrences", 0),
            "by_exemption": s.get("by_exemption", {}),
        }
        for f in doc.get("files", []):
            by_marker_f: Counter[str] = Counter()
            for entry in f.get("exemption_pages", []):
                by_marker_f[entry["marker"]] += entry["count"]
            slim = {
                "id": f.get("id"),
                "filename": f.get("filename"),
                "module": f.get("module"),
                "batch_code": f.get("batch_code"),
                "company": f.get("company"),
                "license": f.get("license"),
                "total_pages": f.get("total_pages"),
                "total_markers": sum(by_marker_f.values()),
                "by_marker": dict(sorted(by_marker_f.items(), key=lambda kv: -kv[1])),
                "ocr_candidate_pages_count": len(f.get("ocr_candidate_pages", [])),
                "source": source_tag,
            }
            if f.get("error"):
                slim["error"] = f["error"]
            all_files.append(slim)
        for marker, count in s.get("by_exemption", {}).items():
            by_marker_overall[marker] += count

    summary = {
        "modules_included": list(per_module.keys()),
        "files_in_index": sum(m["files_in_index"] for m in per_module.values()),
        "files_processed": sum(m["files_processed"] for m in per_module.values()),
        "files_with_markers": sum(m["files_with_markers"] for m in per_module.values()),
        "files_ocr_flagged": sum(m["files_ocr_flagged"] for m in per_module.values()),
        "files_errored": sum(m["files_errored"] for m in per_module.values()),
        "total_marker_occurrences": sum(by_marker_overall.values()),
        "by_module": per_module,
        "individual_sources": individual_summaries,
        "by_exemption_overall": dict(sorted(by_marker_overall.items(), key=lambda kv: -kv[1])),
    }
    for ind in individual_summaries.values():
        summary["files_in_index"] += ind["files_in_scope"]
        summary["files_processed"] += ind["files_processed"]
        summary["files_with_markers"] += ind["files_with_markers"]
        summary["files_ocr_flagged"] += ind["files_ocr_flagged"]
        summary["files_errored"] += ind["files_errored"]
    summary["total_marker_occurrences"] = sum(by_marker_overall.values())

    OUT.write_text(json.dumps({"summary": summary, "files": all_files}, indent=2))

    print(f"Aggregated {len(per_module)} modules, {len(all_files):,} file records")
    print(f"Total marker occurrences: {summary['total_marker_occurrences']:,}")
    print()
    print("Per-module:")
    print(f"  {'mod':<4} {'files':>6} {'w/markers':>10} {'occurrences':>13} {'top':<12}")
    for mod, m in per_module.items():
        top_marker = next(iter(m["by_exemption"]), "")
        print(f"  {mod:<4} {m['files_processed']:>6} {m['files_with_markers']:>10}"
              f" {m['total_marker_occurrences']:>13} {top_marker:<12}")
    print()
    print("Top 10 exemptions overall:")
    for marker, count in list(summary["by_exemption_overall"].items())[:10]:
        print(f"  {marker:>14}  {count:>6}")
    print()
    print(f"Wrote: {OUT}")


if __name__ == "__main__":
    main()
