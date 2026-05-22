# PHMPT Document Inventory + Filterable Site

## Context

You want a complete inventory and browsable index of every document released on
[phmpt.org](https://phmpt.org/) (the Pfizer/Moderna FDA FOIA productions). The
site exposes documents two ways: as large **per-batch ZIP bundles** on
`/multiple-file-downloads/`, and as **individual file** download pages
elsewhere on the site. The same document may appear in multiple zip batches
and also exist as a standalone download — we want one cross-referenced TOC.

Two practical constraints shape the plan:

1. **The site is gated by Cloudflare's managed-challenge plus a Kasada layer.**
   Plain `curl` / `WebFetch` returns 403. We must drive a real browser
   (Playwright with persistent profile) at least once interactively.
2. **No unzipping is needed.** A ZIP file's "central directory" sits at the
   tail of the archive and contains every member's filename, uncompressed
   size, and modification date. Python's `zipfile.ZipFile.infolist()` reads
   only that directory — no decompression, no temp files. We get the full TOC
   by opening each downloaded `.zip` in place. (We could even avoid full
   download via HTTP range requests, but since you want the zip bytes anyway,
   we download once and read locally.)

End state: a generated `docs/data/index.json` and a static, filterable HTML/JS
site that lets you slice the corpus by any part of the filename, date range,
company (Moderna/Pfizer), license (EUA vs. BLA), and age group (adult / 12–15).

## Filename → metadata key

From the legend at the top of `/multiple-file-downloads/`:

| Prefix       | Company | License | Age group |
| ------------ | ------- | ------- | --------- |
| `md`         | Moderna | BLA     | adult     |
| `pd`         | Pfizer  | BLA     | 16+       |
| `p1215d`     | Pfizer  | BLA     | 12–15     |
| `pd-eua`     | Pfizer  | EUA     | 16+       |
| `p1215d-eua` | Pfizer  | EUA     | 12–15     |
| `md-eua`     | Moderna | EUA     | adult     |

`build_index.py` parses these prefixes off each member filename to populate
metadata fields. (Longest-match first: check `p1215d-eua` before `pd-eua`
before `pd`.)

## Repository layout

```
foia-toc/
├── pyproject.toml              # uv/pip project — playwright, httpx, tqdm, pymupdf
├── .gitignore                  # ignores .profile/, data/zips/, .venv/
├── .profile/                   # Playwright persistent context (gitignored)
├── data/
│   ├── zips.json               # one row per zip batch (URL, size, date, code)
│   ├── toc.json                # one row per member file (raw, pre-join)
│   ├── individual_urls.json    # filename → individual phmpt.org URL
│   └── zips/                   # downloaded archives (gitignored)
├── scripts/
│   ├── bootstrap.py            # headed Playwright; user solves CF once
│   ├── crawl_listing.py        # headless; parse zip table at 100/page
│   ├── download_zips.py        # download archives with resume + verify
│   ├── extract_toc.py          # zipfile.infolist() inventory
│   ├── crawl_files.py          # individual per-file page crawl
│   └── build_index.py          # join + emit docs/data/index.json
└── docs/
    ├── index.html              # table + filter controls
    ├── app.js                  # client-side filtering
    ├── styles.css
    └── data/index.json         # the generated index
```

## Pipeline

### 1. `scripts/bootstrap.py` — one-time interactive session

- Launch Playwright Chromium **headed** with persistent context at `.profile/`.
- Navigate to `https://phmpt.org/multiple-file-downloads/`.
- Pause for you to solve the Cloudflare challenge and confirm the page loads.
- Set the select2 "items per page" dropdown to **100** (so subsequent
  headless runs land on the same view).
- Save `storage_state.json` into `.profile/` for headless reuse.

### 2. `scripts/crawl_listing.py` — enumerate zip batches

- Launch headless Chromium with saved storage state.
- For each page of the listing, parse rows into:
  `{ batch_code, zip_filename, zip_url, size_bytes, posted_date }`.
- Paginate until last page; write `data/zips.json`.
- Verification: row count matches the "Showing N of M" label on the page.

### 3. `scripts/download_zips.py` — fetch archives

- Read `data/zips.json`.
- Use Playwright's `request` context (so the browser session cookies travel)
  to GET each zip with `Range` header support for resume.
- Save to `data/zips/<batch_code>/<zip_filename>`.
- Concurrency: 2–3 parallel downloads, polite rate.
- Verify: on-disk size == listing size; HEAD `Content-Length` matches.
- Skip files already present and intact.

### 4. `scripts/extract_toc.py` — inventory without unzipping

For each `.zip` under `data/zips/`:

```python
import zipfile, io
import fitz  # PyMuPDF

with zipfile.ZipFile(path) as zf:
    for info in zf.infolist():  # reads central directory only
        page_count = None
        if info.filename.lower().endswith(".pdf"):
            with zf.open(info) as f:
                page_count = fitz.open(stream=f.read(), filetype="pdf").page_count
        rows.append({
            "member_name": info.filename,
            "uncompressed_size": info.file_size,
            "compressed_size": info.compress_size,
            "modified": datetime(*info.date_time).isoformat(),
            "page_count": page_count,
            "zip_source": path.name,
            "zip_url": zip_url_lookup[path.name],
        })
```

Writes `data/toc.json`. PDF page counts come from PyMuPDF reading the stream
in-memory — no extraction to disk. Parallelize across zips with
`ProcessPoolExecutor` (one worker per CPU). Make the run resumable: cache
per-zip output so a rerun skips zips already inventoried unless the zip's
mtime changed. Skip non-PDF members for page count (set `null`).

Spot-check with `unzip -l` on three random archives to confirm row counts
agree, and with `pdfinfo` on three random PDFs to confirm page counts match.

### 5. `scripts/crawl_files.py` — individual file URLs

- Discover the single-file index page on phmpt.org during the first
  bootstrap run (likely under the main "Documents" nav). Record its URL
  pattern in the script.
- Crawl pages with the same persistent profile, parsing
  `{ filename → individual_url }`.
- Write `data/individual_urls.json`.

### 6. `scripts/build_index.py` — join + derive metadata

- Load `data/toc.json` + `data/individual_urls.json`.
- For each member file, parse the longest-matching prefix from the table
  above to populate `company`, `license`, `age_group`.
- Derive `extension` from the lowercased suffix after the final `.` in
  `filename` (e.g. `pdf`, `xlsx`, `xpt`, `docx`). Empty string if none.
- Emit `docs/data/index.json` as an array of:

```json
{
  "filename": "pd-eua-12345-some-doc.pdf",
  "extension": "pdf",
  "size": 482194,
  "page_count": 47,
  "modified": "2022-03-01T00:00:00",
  "company": "Pfizer",
  "license": "EUA",
  "age_group": "16+",
  "zip_source": "pd-eua-batch-07.zip",
  "zip_url": "https://phmpt.org/...",
  "individual_url": "https://phmpt.org/..."
}
```

`page_count` is `null` for non-PDF members. This shape is the API contract
for the front-end — change with care once the site ships against it.

### 7. `docs/` — static filterable site

- `index.html` + `app.js` + `styles.css`, no framework.
- Loads `data/index.json` once; renders a table with controls:
  - Free-text filter on any part of the filename
  - Date range (uses `modified`)
  - Company (Moderna / Pfizer / All)
  - License (EUA / BLA / All)
  - Age group (adult / 16+ / 12–15 / All)
  - Page count range (min / max numeric inputs; rows with
    `page_count == null` excluded only when either bound is set)
  - File extension (multi-select; options derived from distinct
    `extension` values in the index, sorted by frequency descending)
- Row virtualization for large tables (Tabulator's `virtualDom`, or a
  simple `IntersectionObserver` pager) so tens of thousands of rows stay
  responsive.
- Each row exposes both the **zip source URL** and the **individual file URL**
  when present.
- Deployable as-is to GitHub Pages / Netlify or opened over a local
  `python -m http.server`.

## Why no unzip

Your direct question: we do **not** need to unzip. `zipfile.ZipFile` reads
only the central directory at the tail of the archive when you call
`infolist()` / `namelist()`. That gives us filename, uncompressed size, and
embedded modification date for every member — exactly what the TOC needs —
without writing a single extracted byte. Unzipping would only matter later
if you decide to add full-text search (step 5+ in your message), at which
point you'd extract PDFs and run OCR/text-extraction.

## Verification

- `bootstrap.py`: `.profile/storage_state.json` exists; headless reload of the
  listing returns HTTP 200 with non-empty zip rows.
- `crawl_listing.py`: `data/zips.json` row count matches the "Showing N of M"
  total on the listing page; every row has a non-empty `zip_url` ending in
  `.zip`.
- `download_zips.py`: every file in `data/zips/` matches its `size_bytes` from
  the listing; re-running is a no-op.
- `extract_toc.py`: spot-check 3 random zips: `python -c "import zipfile;
  print(len(zipfile.ZipFile('x.zip').namelist()))"` agrees with the row count
  for that zip in `toc.json`.
- `crawl_files.py`: at least one match found per batch code; report unmatched
  count.
- `build_index.py`: every row classified into a known prefix; report any
  unclassified filenames (likely indicate a new batch code we should add);
  every row has a non-null `extension` (or empty string) and PDFs have
  non-null `page_count`.
- Site: open `docs/index.html` (or the live Pages URL); each filter (text, date, company, license,
  age, pages, extension) independently narrows the row count and the
  cleared state restores the full count.

## Critical files

- `scripts/bootstrap.py` — the one-way door. Decides cookie/storage location.
- `scripts/build_index.py` — defines `index.json` schema, the API the
  front-end depends on.
- `docs/data/index.json` — generated artifact, but its shape is load-bearing.
