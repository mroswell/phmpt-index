# phmpt-index

A filterable browser of every document inside the FDA Pfizer / Moderna
COVID-19 vaccine FOIA productions, as published by
[Public Health and Medical Professionals for Transparency](https://phmpt.org/multiple-file-downloads/)
(PHMPT).

PHMPT hosts the documents as a few dozen large per-month zip bundles on
S3. This project crawls the listing, downloads the archives, reads each
zip's table of contents *without extracting* anything (page counts for
PDFs come from streaming the bytes into PyMuPDF in-memory), joins it
all into one JSON index, and serves a static HTML/JS site for filtering.

## What's in the repo

| Path | What it is |
| --- | --- |
| `scripts/bootstrap.py` | One-time interactive Playwright session — opens the listing in a real Chromium so you can solve the Cloudflare challenge once. The browser profile persists to `.profile/`. |
| `scripts/crawl_listing.py` | Scrapes the listing table. Writes `data/zips.json` (73 zips, ~18 GB total at last run). |
| `scripts/download_zips.py` | Downloads each zip from S3. Serial, resume-aware. |
| `scripts/extract_toc.py` | Reads each zip's central directory + counts PDF pages with PyMuPDF, all in-memory. Writes `data/toc.json`. |
| `scripts/build_index.py` | Joins everything, derives metadata (company / license / age group from the zip prefix, eCTD module from the filename, Bates ranges where present), assigns persistent IDs. Writes `docs/data/index.json`. |
| `docs/` | Vanilla HTML/CSS/JS static site. Loads `data/index.json` and provides filters: filename, date range, company, license, age, page count, file type, eCTD module, Bates number. Shareable links + named saved searches in localStorage. |
| `data/zips.json`, `data/toc.json`, `data/id_registry.json` | Committed metadata — re-clone and serve immediately without re-downloading the 18 GB. |

## Running it

```bash
uv sync
uv run playwright install chromium

# Only needed if you want to regenerate the underlying data:
uv run python scripts/bootstrap.py        # solve CF once, close window
uv run python scripts/crawl_listing.py    # rewrites data/zips.json
uv run python scripts/download_zips.py    # fetches all zips → data/zips/
uv run python scripts/extract_toc.py      # rewrites data/toc.json
uv run python scripts/build_index.py      # rewrites docs/data/index.json

# Always: serve the site.
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
