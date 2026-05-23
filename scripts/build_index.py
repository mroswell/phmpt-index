"""Join the TOC into the final web index.

Reads data/toc.json (raw per-zip member listing) and, when present,
data/individual_urls.json (filename -> per-file URL on phmpt.org).
Writes docs/data/index.json — the single artifact the front-end loads.

Member metadata (company, license, age_group) is derived from the
zip's batch_code, since member filenames inside each zip don't carry
the prefix.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# `_M1` through `_M5` followed by anything non-alphanumeric (covers
# _M5_, _M5 _, _M4.2.3.2_, etc.). Sub-section like M4.2 still maps to M4.
MODULE_RE = re.compile(r"_M([1-5])(?:[^0-9A-Za-z]|$)")
# FDA-CBER bates prefix: FDA-CBER-YYYY-NNNN-XXXXXXX-YYYYYYY
BATES_RE = re.compile(r"^FDA-CBER-\d+-\d+-(\d+)-(\d+)")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TOC = DATA / "toc.json"
INDIVIDUAL = DATA / "individual_urls.json"
ORPHANS = DATA / "orphans.json"
VERIFICATION = DATA / "complete_orphan_verification.json"
ID_REGISTRY = DATA / "id_registry.json"
OUT = ROOT / "docs" / "data" / "index.json"

# batch_code -> (company, license, age_group)
BATCH_META: dict[str, tuple[str, str, str]] = {
    "md":         ("Moderna", "BLA", "adult"),
    "md-eua":     ("Moderna", "EUA", "adult"),
    "pd":         ("Pfizer",  "BLA", "16+"),
    "pd-eua":     ("Pfizer",  "EUA", "16+"),
    "p1215d":     ("Pfizer",  "BLA", "12-15"),
    "p1215d-eua": ("Pfizer",  "EUA", "12-15"),
}

# product_page -> (company, age_group). License is BLA-or-EUA-mixed on these
# pages, so we leave it null for orphans.
PRODUCT_META: dict[str, tuple[str | None, str | None]] = {
    "/pfizer-16-plus-documents/":                  ("Pfizer",  "16+"),
    "/moderna-documents/":                         ("Moderna", "adult"),
    "/pfizer-12-15-documents/":                    ("Pfizer",  "12-15"),
    "/pfizer-court-documents/":                    ("Pfizer",  None),
    "/pfizer-12-15-and-moderna-court-documents/":  (None,      None),
}

URL_DATE_RE = re.compile(r"/wp-content/uploads/(\d{4})/(\d{2})/")


def extension_of(name: str) -> str:
    base = name.rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[-1].lower() if "." in base else ""


def basename(name: str) -> str:
    return name.rsplit("/", 1)[-1]


def module_of(name: str) -> str | None:
    m = MODULE_RE.search(name)
    return f"M{m.group(1)}" if m else None


def bates_of(name: str) -> tuple[int | None, int | None]:
    m = BATES_RE.match(name)
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def load_registry() -> dict[str, int]:
    """Persistent (zip_source||member_name) -> int ID map.

    Once a row gets an ID, the ID is permanent: it survives data rebuilds,
    re-orderings, and new files being added. Removed files keep their slot
    (so the same number never gets reassigned). New files get max+1.
    """
    if not ID_REGISTRY.exists():
        return {}
    return json.loads(ID_REGISTRY.read_text())


def save_registry(reg: dict[str, int]) -> None:
    ID_REGISTRY.write_text(json.dumps(reg, separators=(",", ":")))


def main() -> None:
    if not TOC.exists():
        raise SystemExit(f"missing {TOC} — run extract_toc.py first")
    toc = json.loads(TOC.read_text())
    individual: dict[str, str] = {}
    if INDIVIDUAL.exists():
        individual = json.loads(INDIVIDUAL.read_text())

    registry = load_registry()
    next_id = (max(registry.values()) + 1) if registry else 1
    new_ids = 0

    out_rows: list[dict] = []
    unknown_codes: set[str] = set()
    for row in toc:
        code = row.get("batch_code")
        meta = BATCH_META.get(code or "")
        if meta is None:
            unknown_codes.add(code or "(none)")
            company = license_ = age_group = None
        else:
            company, license_, age_group = meta

        fname = basename(row["member_name"])
        bates_start, bates_end = bates_of(fname)

        # Stable per-document ID. Key on (zip_source, full member path) so
        # two different zips containing identically-named members each get
        # their own row.
        reg_key = f"{row.get('zip_source','')}||{row['member_name']}"
        if reg_key not in registry:
            registry[reg_key] = next_id
            next_id += 1
            new_ids += 1
        row_id = registry[reg_key]

        out_rows.append(
            {
                "id":             row_id,
                "filename":       fname,
                "extension":      extension_of(fname),
                "size":           row.get("uncompressed_size"),
                "page_count":     row.get("page_count"),
                "modified":       row.get("modified"),
                "company":        company,
                "license":        license_,
                "age_group":      age_group,
                "module":         module_of(fname),
                "bates_start":    bates_start,
                "bates_end":      bates_end,
                "batch_code":     code,
                "zip_source":     row.get("zip_source"),
                "zip_url":        row.get("zip_url"),
                "individual_url": individual.get(fname),
            }
        )

    # Load allowed orphan URLs (only the 4 unique files, not the 304 duplicates)
    allowed_orphan_urls = set()
    if VERIFICATION.exists():
        verification = json.loads(VERIFICATION.read_text())
        for new_file in verification.get("new_files", []):
            allowed_orphan_urls.add(new_file["orphan_url"])

    # Append phmpt-only files (files on phmpt.org not present in any zip).
    # Only include files that are NOT duplicates of multiple-file-downloads content.
    orphan_count = 0
    if ORPHANS.exists():
        orphans = json.loads(ORPHANS.read_text())
        for o in orphans:
            # Skip duplicate files - only include the 4 unique ones
            if o["url"] not in allowed_orphan_urls:
                continue
            fname = (o.get("filename") or "").strip()
            if not fname:
                continue  # blank-filename row on at least one product page
            url = o["url"]
            page = o.get("product_page", "")
            company, age_group = PRODUCT_META.get(page, (None, None))
            bates_start, bates_end = bates_of(fname)
            # Try to recover a date from /wp-content/uploads/YYYY/MM/...
            m = URL_DATE_RE.search(url)
            modified = f"{m.group(1)}-{m.group(2)}-01T00:00:00" if m else None

            reg_key = f"orphan||{url}"
            if reg_key not in registry:
                registry[reg_key] = next_id
                next_id += 1
                new_ids += 1
            row_id = registry[reg_key]

            out_rows.append(
                {
                    "id":             row_id,
                    "filename":       fname,
                    "extension":      extension_of(fname),
                    "size":           None,
                    "page_count":     None,
                    "modified":       modified,
                    "company":        company,
                    "license":        None,           # BLA vs EUA unknown for orphans
                    "age_group":      age_group,
                    "module":         module_of(fname),
                    "bates_start":    bates_start,
                    "bates_end":      bates_end,
                    "batch_code":     None,
                    "zip_source":     None,
                    "zip_url":        None,
                    "individual_url": url,
                }
            )
            orphan_count += 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out_rows, separators=(",", ":")))
    save_registry(registry)

    matched_indiv = sum(1 for r in out_rows if r["individual_url"])
    by_company: dict[str, int] = {}
    by_license: dict[str, int] = {}
    by_age: dict[str, int] = {}
    by_module: dict[str, int] = {}
    bates_count = 0
    for r in out_rows:
        by_company[r["company"] or "?"] = by_company.get(r["company"] or "?", 0) + 1
        by_license[r["license"] or "?"] = by_license.get(r["license"] or "?", 0) + 1
        by_age[r["age_group"] or "?"] = by_age.get(r["age_group"] or "?", 0) + 1
        by_module[r["module"] or "(none)"] = by_module.get(r["module"] or "(none)", 0) + 1
        if r["bates_start"] is not None:
            bates_count += 1

    zip_rows = len(out_rows) - orphan_count
    both = sum(1 for r in out_rows[:zip_rows] if r["individual_url"])
    zip_only = zip_rows - both
    indiv_only = orphan_count

    size_kb = OUT.stat().st_size / 1024
    print(f"wrote {len(out_rows):,} rows  ({size_kb:.0f} KB) -> {OUT}")
    print(f"  zip-member rows:        {zip_rows:,}")
    print(f"  individual-only rows:   {orphan_count:,}")
    print(f"id registry: {len(registry):,} total, {new_ids:,} newly assigned this run")
    print(f"breakdown by source:")
    print(f"  in zip + individual:    {both:,}")
    print(f"  zip only:               {zip_only:,}")
    print(f"  individual only:        {indiv_only:,}")
    print(f"company: {by_company}")
    print(f"license: {by_license}")
    print(f"age:     {by_age}")
    print(f"module:  {by_module}")
    print(f"bates ranges extracted: {bates_count:,} / {len(out_rows):,}")
    if unknown_codes:
        print(f"WARNING: unknown batch codes: {sorted(unknown_codes)}")


if __name__ == "__main__":
    main()
