# phmpt-index

A filterable browser of every document inside the FDA Pfizer / Moderna
COVID-19 vaccine FOIA productions, as published by
[Public Health and Medical Professionals for Transparency](https://phmpt.org/multiple-file-downloads/)
(PHMPT).

PHMPT hosts the documents as a few dozen large per-month zip bundles on
S3. This project crawls the listing, downloads the archives, reads each
zip's table of contents *without extracting* anything (page counts for
PDFs come from streaming the bytes into PyMuPDF in-memory), joins it
all into one JSON index, and serves a static HTML/JS site with several
analyses layered on top:

- a per-file index with filters
- a content-overlap analysis vs. PHMPT's company-documents pages
- a redaction (FOIA exemption) catalog
- a pharmacovigilance-term scan
- a statute / regulation / court-case citation catalog

## Site pages

Live at **https://mroswell.github.io/phmpt-index/**:

| Page | What it shows |
| --- | --- |
| `index.html` | The original per-file index. Filterable, sortable, shareable links, named saved searches in localStorage. |
| `duplicates.html` | Content-overlap analysis: maps the 308 zips on PHMPT's company-documents pages against the 73 multiple-file-downloads zips. 304 overlap, 4 are unique. |
| `pharmacovigilance.html` | Files that mention any of six pharmacovigilance / signal-detection terms (Pharmacovigilance plan, disproportionality analysis, risk management plan, PRR, Proportional Reporting Ratio, Bayesian). Filterable by file metadata; show-context per row. |
| `exemptions.html` | Every `(b)(N)` FOIA redaction marker the scanner found. Includes cross-tabs by module × company × license × age, a rare-exemption-types panel (everything other than the dominant `(b)(4)` and `(b)(6)`), a separate "Mentions of FOIA exemptions" panel for body-text statute references, and a filterable per-file table with category filtering and a CRF-exclusion toggle. |
| `statutes.html` | Citations of statutes, regulations, court cases, and international rules — USC, CFR, named acts (FD&C / FOIA / PHS / PREP / CARES), "Section NNN of the X Act", public laws, court cases (`X v. Y, NNN Reporter NNN`), EU regulations and directives, ICH guidelines, WHO TRS, ISO standards. Filterable by family and by individual statute. |

## What's in the repo

### Data pipeline

| Path | What it is |
| --- | --- |
| `scripts/bootstrap.py` | One-time interactive Playwright session — opens the listing in a real Chromium so you can solve the Cloudflare challenge once. The browser profile persists to `.profile/`. |
| `scripts/crawl_listing.py` | Scrapes the listing table. Writes `data/zips.json` (73 zips, ~18 GB total at last run). |
| `scripts/download_zips.py` | Downloads each zip from S3. Serial, resume-aware. |
| `scripts/extract_toc.py` | Reads each zip's central directory + counts PDF pages with PyMuPDF, all in-memory. Writes `data/toc.json`. |
| `scripts/build_index.py` | Joins everything, derives metadata (company / license / age group from the zip prefix, eCTD module from the filename, Bates ranges where present), joins individual / ICAN URLs, assigns persistent IDs. Writes `docs/data/index.json`. |
| `scripts/crawl_files.py` | Scrapes individual PHMPT product pages to discover per-file URLs. Writes `data/individual_urls.json` and `data/orphans.json`. |
| `scripts/verify_all_orphan_zips.py` | Downloads and verifies the 308 zips on company-documents pages against the multiple-file-downloads zips. Writes `data/complete_orphan_verification.json`. |
| `data/zips.json`, `data/toc.json`, `data/id_registry.json`, `data/ican_comparison.json` | Committed metadata — re-clone and serve immediately without re-downloading the 18 GB. |

### Analyses

| Path | What it is |
| --- | --- |
| `scripts/_pdf_text.py` | Shared text-extraction helper used by every scanner. Tries `page.get_text("text")` first; falls back to the OCR text cache for image-only pages. |
| `scripts/extract_ocr_text.py` | One-time OCR pass over every page flagged as `ocr_candidate` by other scanners (low text density). Renders pages at 300 DPI via PyMuPDF, runs Tesseract via pytesseract, caches per-page text at `data/cache/ocr_text/{filename}__p{NNNN}.txt`. ~8,200 pages across ~1,050 PDFs. |
| `scripts/extract_module_files.py` | Extracts PDFs for a given eCTD module from the existing zips into `data/files/{batch_code}/`. Parameterizable: `python scripts/extract_module_files.py M5`. |
| `scripts/extract_module_exemptions.py` | Scans every PDF in one eCTD module for `(b)(N)` redaction markers. Streams from zips; per-file cache; writes `data/M{1..5}_exemptions.json`. |
| `scripts/scan_individual_via_ican.py` | Downloads the ~232 individual-only PDFs that ICAN mirrors (Cloudflare-free) and scans them for redaction markers. Cached at `data/cache/individual_pdfs/`. |
| `scripts/scan_individual_via_phmpt.py` | Downloads the ~168 individual-only PDFs that only exist on phmpt.org (Cloudflare-protected) using curl_cffi with Chrome TLS impersonation. Rate-limited, circuit-broken. Same cache. |
| `scripts/aggregate_exemptions.py` | Combines per-module + individual scan outputs into a slim cross-source catalog at `docs/data/exemptions.json`. |
| `scripts/report_exemptions.py` | Cross-tab pivot tables (exemption × module / company / license / age). Writes `docs/data/exemptions_report.{md,json}`. |
| `scripts/report_eyeball.py` | Per-file detail reports for human eyeballing. Writes `docs/data/rare_exemptions_report.md`, `docs/data/per_file_report_non_m5.md`, `docs/data/rare_exemptions.json`. |
| `scripts/inspect_rare_contexts.py` | Classifies every rare-exemption hit by extracting the surrounding text context. Bucketed into `foia_redaction`, `foia_legal_reference` ("Mentions exemption"), or `not_foia` (false positives — other-statute subsection references). Writes `docs/data/rare_exemptions_contexts.json` and overwrites `rare_exemptions.json` with the enriched version. |
| `scripts/scan_pharmacovigilance_terms.py` | Searches the corpus for six pharmacovigilance terms. Writes `docs/data/pharmacovigilance.json` + a markdown report. |
| `scripts/scan_statute_references.py` | Searches the corpus for statute / regulation / court-case / international citations across seven families. Writes `docs/data/statutes.json`. |

### Reference content

| Path | What it is |
| --- | --- |
| `context/foia_exemptions.md` | The nine FOIA exemptions reference document (linked from the Exemptions page). |

## Running it

### One-time setup

```bash
uv sync
uv run playwright install chromium
brew install tesseract        # only needed for the OCR pass
```

### Regenerate the data pipeline (if you want to redownload)

```bash
uv run python scripts/bootstrap.py        # solve CF once, close window
uv run python scripts/crawl_listing.py    # rewrites data/zips.json
uv run python scripts/download_zips.py    # fetches all zips → data/zips/
uv run python scripts/extract_toc.py      # rewrites data/toc.json
uv run python scripts/build_index.py      # rewrites docs/data/index.json
```

### Run the analyses

```bash
# OCR (one-time, ~6 hours on a laptop, populates data/cache/ocr_text/)
uv run python scripts/extract_ocr_text.py

# Exemption scan per module
for mod in M1 M2 M3 M4 M5; do
  uv run python scripts/extract_module_exemptions.py $mod
done

# Individual-only PDFs (download + scan)
uv run python scripts/scan_individual_via_ican.py
uv run python scripts/scan_individual_via_phmpt.py

# Roll up + reports
uv run python scripts/aggregate_exemptions.py
uv run python scripts/report_exemptions.py
uv run python scripts/report_eyeball.py
uv run python scripts/inspect_rare_contexts.py

# Other catalogs
uv run python scripts/scan_pharmacovigilance_terms.py
uv run python scripts/scan_statute_references.py
```

### Serve the site

```bash
cd docs && uv run python -m http.server 8765
# open http://localhost:8765/
```

## Hosted version

The static site under `docs/` is published via GitHub Pages at
**https://mroswell.github.io/phmpt-index/**. Permalinks and saved
searches work the same way as locally.

## Filename → metadata key

Zip filenames carry the batch code as a prefix:

| Prefix       | Company | License | Age   |
| ------------ | ------- | ------- | ----- |
| `md`         | Moderna | BLA     | adult |
| `md-eua`     | Moderna | EUA     | adult |
| `pd`         | Pfizer  | BLA     | 16+   |
| `pd-eua`     | Pfizer  | EUA     | 16+   |
| `p1215d`     | Pfizer  | BLA     | 12–15 |
| `p1215d-eua` | Pfizer  | EUA     | 12–15 |

## Source-link priority

Across the analysis pages, every filename links to the best available
source in this order:

1. **PHMPT individual URL** (the canonical source; may show a one-time
   Cloudflare challenge in the browser)
2. **ICAN re-host** (`icandecide.org/wp-content/...` — no Cloudflare)
3. **ZIP download** (last resort, downloads the whole archive)

A small color-coded tag on each filename indicates which source the
link points to.

## How it scales

| | Files | Notes |
| --- | ---: | --- |
| Total PHMPT corpus | 8,560 records | ZIP members + individual files |
| PDFs (scanned for redactions / terms / statutes) | 6,505 | After de-dup |
| OCR'd image-only pages | 8,232 across 1,051 files | One-time pass |
| FOIA exemption markers found | 11.65 M | `(b)(4)` 8.18 M + `(b)(6)` 3.47 M dominates |
| Pharmacovigilance term occurrences | 2,318 across 177 files | Risk management plan + Pharmacovigilance plan most common |
| Statute / regulation / court / intl citations | 4,174 across 7 families | FOIA, FD&C Act, USC, CFR, ICH, etc. |
