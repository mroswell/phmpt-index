"""Generate cross-tab FOIA exemption reports from data/exemptions.json.

Outputs:
- data/exemptions_report.md   — human-readable markdown
- data/exemptions_report.json — structured cross-tab data

Pivots: each exemption type (b)(N) crossed against module, company,
license, and age_group; plus module-vs-company / license / age_group
2-D breakdowns showing file count, total markers, and markers per file.

Run:
    uv run python scripts/report_exemptions.py
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
IN = DATA / "exemptions.json"
OUT_MD = DATA / "exemptions_report.md"
OUT_JSON = DATA / "exemptions_report.json"

# batch_code → age_group (mirrors BATCH_META in scripts/build_index.py)
BATCH_TO_AGE = {
    "md": "adult",  "md-eua": "adult",
    "pd": "16+",    "pd-eua": "16+",
    "p1215d": "12-15", "p1215d-eua": "12-15",
}

UNKNOWN = "Unknown"


def fmt(n: float | int | None) -> str:
    if n is None:
        return "—"
    if isinstance(n, float):
        return f"{n:,.1f}" if abs(n) < 1000 else f"{n:,.0f}"
    return f"{n:,}"


def collect(files: list[dict], dim_fn) -> tuple[dict, dict]:
    """Return (file_counts_by_dim, marker_totals_by_dim_and_exemption)."""
    file_counts = Counter()
    markers = defaultdict(Counter)
    for f in files:
        v = dim_fn(f) or UNKNOWN
        file_counts[v] += 1
        for marker, count in f.get("by_marker", {}).items():
            markers[v][marker] += count
    return dict(file_counts), {k: dict(v) for k, v in markers.items()}


def two_d(files: list[dict], row_fn, col_fn) -> dict:
    """Return {row: {col: {file_count, total_markers}}}."""
    table: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"file_count": 0, "total_markers": 0})
    )
    for f in files:
        r = row_fn(f) or UNKNOWN
        c = col_fn(f) or UNKNOWN
        cell = table[r][c]
        cell["file_count"] += 1
        cell["total_markers"] += f.get("total_markers", 0)
    return {r: dict(d) for r, d in table.items()}


def md_one_d_table(title: str, file_counts: dict, markers: dict) -> list[str]:
    """Build a markdown section: exemption rows × dimension columns."""
    dim_values = sorted(file_counts.keys())
    all_exemptions = sorted(
        {e for d in markers.values() for e in d.keys()},
        key=lambda e: -sum(markers.get(v, {}).get(e, 0) for v in dim_values),
    )
    lines = [f"## {title}", ""]
    header = "| Exemption | " + " | ".join(dim_values) + " | **Total** |"
    sep = "| --- " + "| ---: " * (len(dim_values) + 1) + "|"
    lines.append(header)
    lines.append(sep)

    for e in all_exemptions:
        row = [markers.get(v, {}).get(e, 0) for v in dim_values]
        total = sum(row)
        lines.append(
            f"| `{e}` | " + " | ".join(fmt(v) for v in row) + f" | **{fmt(total)}** |"
        )

    col_totals = [sum(markers.get(v, {}).values()) for v in dim_values]
    grand_total = sum(col_totals)
    lines.append(
        "| **Markers total** | "
        + " | ".join(f"**{fmt(v)}**" for v in col_totals)
        + f" | **{fmt(grand_total)}** |"
    )

    file_row = [file_counts.get(v, 0) for v in dim_values]
    total_files = sum(file_row)
    lines.append(
        "| *Files* | "
        + " | ".join(f"*{fmt(v)}*" for v in file_row)
        + f" | *{fmt(total_files)}* |"
    )

    rates = [
        (col_totals[i] / file_row[i]) if file_row[i] else 0.0
        for i in range(len(dim_values))
    ]
    overall_rate = grand_total / total_files if total_files else 0.0
    lines.append(
        "| *Markers / file* | "
        + " | ".join(f"*{fmt(r)}*" for r in rates)
        + f" | *{fmt(overall_rate)}* |"
    )
    lines.append("")
    return lines


def md_two_d_block(title: str, row_label: str, col_label: str, table: dict) -> list[str]:
    """Build three stacked sub-tables: file counts, marker totals, markers/file."""
    rows = sorted(table.keys())
    cols = sorted({c for r in table.values() for c in r.keys()})

    def cell(r, c, field, *, rate=False):
        if r not in table or c not in table[r]:
            return 0
        v = table[r][c]
        if rate:
            return (v["total_markers"] / v["file_count"]) if v["file_count"] else 0.0
        return v[field]

    lines = [f"## {title}", ""]

    for label, getter in [
        ("File counts", lambda r, c: cell(r, c, "file_count")),
        ("Marker totals", lambda r, c: cell(r, c, "total_markers")),
        ("Markers per file", lambda r, c: cell(r, c, "total_markers", rate=True)),
    ]:
        lines.append(f"### {label}")
        lines.append("")
        header = f"| {row_label} | " + " | ".join(cols) + " | **Total** |"
        sep = "| --- " + "| ---: " * (len(cols) + 1) + "|"
        lines.append(header)
        lines.append(sep)

        col_totals = [0] * len(cols)  # ints when accumulating counts/markers
        col_files = [0] * len(cols)  # for computing column rate
        for r in rows:
            row_vals = [getter(r, c) for c in cols]
            if label == "Markers per file":
                row_files = sum(cell(r, c, "file_count") for c in cols)
                row_markers = sum(cell(r, c, "total_markers") for c in cols)
                row_total = (row_markers / row_files) if row_files else 0.0
            else:
                row_total = sum(row_vals)
            lines.append(
                f"| **{r}** | "
                + " | ".join(fmt(v) for v in row_vals)
                + f" | **{fmt(row_total)}** |"
            )
            for i, c in enumerate(cols):
                col_totals[i] += getter(r, c) if label != "Markers per file" else cell(r, c, "total_markers")
                col_files[i] += cell(r, c, "file_count")

        if label == "Markers per file":
            display_totals = [
                (col_totals[i] / col_files[i]) if col_files[i] else 0.0
                for i in range(len(cols))
            ]
            grand = (
                sum(col_totals) / sum(col_files) if sum(col_files) else 0.0
            )
        else:
            display_totals = col_totals
            grand = sum(col_totals)
        lines.append(
            "| **Total** | "
            + " | ".join(f"**{fmt(v)}**" for v in display_totals)
            + f" | **{fmt(grand)}** |"
        )
        lines.append("")
    return lines


def main() -> None:
    data = json.loads(IN.read_text())
    files = data["files"]

    # Derive age_group
    for f in files:
        f["age_group"] = BATCH_TO_AGE.get(f.get("batch_code")) or UNKNOWN

    mod = lambda f: f.get("module")
    cmp_ = lambda f: f.get("company")
    lic = lambda f: f.get("license")
    age = lambda f: f.get("age_group")

    # 1-D rollups
    counts_mod, mk_mod = collect(files, mod)
    counts_cmp, mk_cmp = collect(files, cmp_)
    counts_lic, mk_lic = collect(files, lic)
    counts_age, mk_age = collect(files, age)

    # 2-D cross-tabs
    tab_mod_cmp = two_d(files, mod, cmp_)
    tab_mod_lic = two_d(files, mod, lic)
    tab_mod_age = two_d(files, mod, age)

    # Structured JSON
    report_json = {
        "source": "data/exemptions.json",
        "file_records": len(files),
        "exemption_by_module": {
            "file_counts": counts_mod,
            "markers": mk_mod,
        },
        "exemption_by_company": {
            "file_counts": counts_cmp,
            "markers": mk_cmp,
        },
        "exemption_by_license": {
            "file_counts": counts_lic,
            "markers": mk_lic,
        },
        "exemption_by_age_group": {
            "file_counts": counts_age,
            "markers": mk_age,
        },
        "module_by_company": tab_mod_cmp,
        "module_by_license": tab_mod_lic,
        "module_by_age_group": tab_mod_age,
    }
    OUT_JSON.write_text(json.dumps(report_json, indent=2))

    # Markdown report
    md = [
        "# FOIA Exemption Cross-Tab Report",
        "",
        f"Source: `data/exemptions.json` ({len(files):,} file records, "
        f"{sum(f.get('total_markers', 0) for f in files):,} total marker occurrences).",
        "",
        "**Dimensions covered:**",
        "- `module` (M1–M5): document-type classification within the FOIA submission",
        "- `company`: Pfizer vs Moderna",
        "- `license`: BLA (full approval) vs EUA (emergency use authorization)",
        "- `age_group`: adult (Moderna), 16+ (Pfizer adult), 12-15 (Pfizer pediatric)",
        "",
        "Each one-dimensional table shows raw marker counts split by exemption type, "
        "plus a *files* row and *markers / file* rate row at the bottom so you can "
        "compare across dimensions of different sizes.",
        "",
        "---",
        "",
    ]

    md += md_one_d_table("Exemption × module", counts_mod, mk_mod)
    md += md_one_d_table("Exemption × company", counts_cmp, mk_cmp)
    md += md_one_d_table("Exemption × license", counts_lic, mk_lic)
    md += md_one_d_table("Exemption × age_group", counts_age, mk_age)

    md += ["---", ""]
    md += md_two_d_block("Module × company", "module", "company", tab_mod_cmp)
    md += md_two_d_block("Module × license", "module", "license", tab_mod_lic)
    md += md_two_d_block("Module × age_group", "module", "age_group", tab_mod_age)

    OUT_MD.write_text("\n".join(md))

    # Console summary
    print(f"Wrote: {OUT_JSON}")
    print(f"Wrote: {OUT_MD}")
    print()
    print("Marker totals reconciliation:")
    total_from_files = sum(f.get("total_markers", 0) for f in files)
    for dim_name, mk_dict in [("module", mk_mod), ("company", mk_cmp), ("license", mk_lic), ("age_group", mk_age)]:
        dim_total = sum(sum(d.values()) for d in mk_dict.values())
        ok = "✓" if dim_total == total_from_files else "✗"
        print(f"  by {dim_name:<10} → {dim_total:>14,}  {ok}")
    print(f"  reference total → {total_from_files:>14,}")


if __name__ == "__main__":
    main()
