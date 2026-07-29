# DNAIntegrity Trove FOIA-Exemptions Report — Design

**Date:** 2026-07-29
**Status:** Approved (pending spec review)

## Goal

Produce a FOIA-exemptions report covering **all 7,417 documents** in the
FDA-FOIA-2026-6007 trove ("the DNAIntegrity trove"), published as a new page on
the trial-foia site (https://dnaintegrityproject.github.io/trial-foia/) with a
link in the header. Marker detection is **identical** to the
foia.coviddocuments.com exemptions report so the two are directly comparable.

The trove overlaps the PHMPT corpus heavily (7,179 of 7,417 docs already appear
there); the remaining 238 are NEW documents released only in this FOIA
production.

## Data sources

1. **Trove membership** — `FDA-FOIA-2026-6007/site/documents.json` (7,417
   records; each has `filename`, `status` (NEW/PHMPT), `category`, `module`,
   `bimo`, `ext`, `size_mb`).
2. **Overlapping docs (7,179)** — reuse per-file marker counts from the existing
   `docs/data/exemptions.json` `files[]` records (6,545 entries; each has
   `filename`, `by_marker`, `total_markers`, `total_pages`, ocr flags). Matched
   to trove filenames using the SAME normalization keys `build_site.py` uses:
   exact basename → Bates-prefix-stripped → fully normalized → `.xpt` size
   fallback. Same document ⇒ same redactions, so no re-scan.
3. **NEW docs (238, all present locally)** — fresh OCR scan of the loose PDFs in
   `FDA-FOIA-2026-6007/new-files/` (237 PDFs + 1 txt), reusing the exact
   `MARKER_RE = r"\(\s*b\s*\)\s*\(\s*(\d+)\s*\)(?:\s*\(\s*([A-F])\s*\))?"` and
   `normalize_marker()` from `scripts/extract_module_exemptions.py`.

## Components

### 1. `scripts/scan_trove_new_exemptions.py`
OCRs the 238 NEW files and counts exemption markers per file.
- Input: `documents.json` (to select `status == NEW`), local file paths under
  `new-files/` and `site/files/`.
- Per-page: render at 300 dpi → Tesseract → apply `MARKER_RE`. Text-layer text
  is used first; OCR only where the text layer is thin (`< 30` chars),
  mirroring `OCR_TEXT_THRESHOLD`.
- Per-file cache under `data/cache/trove_new_exemptions/<filename>.json` so
  re-runs are cheap and the scan is resumable.
- Output: `data/trove_new_exemptions.json` — per-file records shaped like the
  existing pipeline: `{filename, total_pages, total_markers, by_marker,
  ocr_candidate_pages, error?}`.
- Non-PDF NEW files (the 1 `.txt`) → `total_markers: 0`, no OCR.

### 2. `scripts/build_trove_exemptions.py`
Joins trove membership to marker data and aggregates.
- For each of the 7,417 trove docs, resolve a marker record:
  - matched PHMPT record (source `"phmpt"`), OR
  - NEW-scan record (source `"new"`), OR
  - unresolved (source `"none"` — flagged, counted as 0, surfaced in coverage).
- Enrich each with trove dimensions: `status`, `category`, `module`, `bimo`.
- Emit `FDA-FOIA-2026-6007/site/exemptions.json` containing:
  - `summary` (files, files_with_markers, total_marker_occurrences, coverage
    counts, per-source breakdown, ocr caveats)
  - cross-tabs: exemption × `module`, exemption × `category`,
    exemption × `status` (NEW vs PHMPT), exemption × `bimo`
  - `rare` — low-frequency exemptions with example filename contexts (reusing
    the approach in `scripts/report_exemptions.py` / `rare_exemptions.json`)
  - `files[]` — per-file rows: `filename, status, category, module, bimo,
    total_markers, by_marker, source, doc_link` (doc_link from documents.json).
- Also emit `FDA-FOIA-2026-6007/exemptions_report.md` (repo-side summary).

### 3. `FDA-FOIA-2026-6007/site/exemptions.html` + `exemptions.js`
The page, styled with the trial-foia site's existing CSS variables.
Full parity with the coviddocuments exemptions page:
- headline stat cards
- the four cross-tab tables
- rare-exemption list with example contexts
- a **searchable + sortable per-file table** with a **status filter** whose
  options include **"NEW only (238)"**, plus **All** and **PHMPT**, and a
  free-text filename search. (This satisfies the explicit request for a filter
  showing just the 238 new PDFs.)

### 4. Header navigation
The trial-foia `site/index.html` currently has no nav bar. Add a small nav
(`Documents · Exemptions`) to the header of both `index.html` and
`exemptions.html`. Keep the existing header title/subtitle.

## Publish

- Parent repo (`foia-toc`): commit the two scripts, `data/trove_new_exemptions.json`,
  `exemptions_report.md`, and this spec. Ignore the OCR page cache.
- trial-foia repo (`site/`): commit + push `exemptions.html`, `exemptions.js`,
  `exemptions.json`, and the `index.html` header change → goes live.

## Coverage & honesty

- Non-PDF data files (`.xpt`, `.txt`, `.xml`, `.xsl`) carry no redaction markers
  → counted 0, labeled as data files, not silently mixed into PDF rates.
- OCR is imperfect on dense scans; the summary notes an OCR caveat and reports
  `ocr_candidate_pages` totals.
- Overlap counts come from the existing PHMPT scan (stated on the page); NEW
  counts come from the fresh OCR scan.
- Any trove doc that matches neither source is flagged in the coverage summary,
  never zero-filled silently.

## Non-goals

- No changes to the coviddocuments.com exemptions report.
- No re-scan of the 7,179 overlapping documents (their PDFs are not held
  locally; reuse is correct because they are the same documents).
