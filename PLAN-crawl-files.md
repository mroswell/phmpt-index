# Individual-file URL crawl + small UI polish

## Context

The site at https://mroswell.github.io/phmpt-index/ currently indexes
8,069 files, all extracted from the per-month zip bundles on
phmpt.org/multiple-file-downloads/. But phmpt.org also publishes
**individual** download pages for those same documents under per-product
URLs in its top nav. Right now every row in our index has
`individual_url: null` — so the **Individual** column shows nothing,
and we have no way to know whether phmpt.org has files we *don't* have
(e.g., never bundled into a monthly zip).

This phase plugs both holes: populate `individual_url` for every row
that matches, and surface any phmpt-only files as a separate report.
Also a one-line UI tweak.

## What's left in the broader project (for orientation)

- [x] Move Reset Filters button — small CSS tweak (this plan, §0)
- [ ] **Crawl individual file pages on phmpt.org** (this plan, §1–§4)
- [ ] Report orphans: files on phmpt.org not present in any zip
      (this plan, §3)
- [ ] *(deferred — user said "not yet")* Remove the **Individual**
      column from the table once the Filename cell is hyperlinked
      to `individual_url` when present.

## §0. Reset button position

`docs/styles.css` currently has:

```css
.savedbar #reset { margin-left: 150px; }
```

Change to `75px` (halfway back to the rest of the toolbar).

## §1. Discovery probe

The five product pages almost certainly use the same DataTables plugin
as `/multiple-file-downloads/` (selector
`table.posts-data-table tbody tr`, length-select pattern, `dlp_*` IDs).
But we haven't confirmed:

- Number of rows per page
- Whether filenames render as plain text or as `<a>` tags pointing at
  per-file pages, or whether the row links straight to the S3 object
- The exact column ordering

So `scripts/crawl_files.py` begins with a probe pass: load the first
page headless (re-using `.profile/`), dump the rendered HTML to
`.scratch/<slug>.html` for each of the 5 pages, log the `dataTables_info`
total, the `<a>` hrefs in each row, and the first 3 cell texts. We
inspect one of those dumps, confirm or adjust the selectors, then turn
the script into a full crawler.

## §2. `scripts/crawl_files.py`

Pattern: re-use everything from `scripts/crawl_listing.py`:

- `sync_playwright().chromium.launch_persistent_context(.profile/, headless=False)`
  (headless True often fails CF even with valid cookies; same finding as
  crawl_listing.py)
- For each of the 5 page slugs:
  1. `page.goto(url, wait_until="domcontentloaded", timeout=60000)`
  2. best-effort `wait_for_load_state("networkidle", timeout=15000)`
  3. `page.wait_for_selector("table.posts-data-table tbody tr", timeout=20000)`
  4. Read total from `.dataTables_info` (regex `(\d+)`)
  5. `page.evaluate(...)` to set length select to `-1` (All) via jQuery
  6. `page.wait_for_function` until row count ≥ total
  7. `page.eval_on_selector_all("table.posts-data-table tbody tr", ...)`
     to collect `{filename, url}` per row

- Combine all 5 pages into one mapping `{ filename: url }` keyed by the
  filename basename (case-sensitive first; we adjust if matching drops).
- Detect cross-page filename collisions — log them; keep the first.

Output: `data/individual_urls.json`

```json
{
  "125752_S10_M5_CRF_mrna-1273-p301-us3662156.pdf":
    "https://phmpt.org/...",
  ...
}
```

## §3. Orphan report

After the crawl, the script also writes `data/orphans.json` — every
filename present on phmpt.org but **not** in any of our 73 zips:

```json
[
  {"filename": "...", "url": "https://phmpt.org/...", "product_page": "/moderna-documents/"}
]
```

And prints a console summary:

```
phmpt total:      N
zips total:       M
matched:          K   (K / N)
orphans (phmpt only): N - K
zip-only (no phmpt):  M - K
```

The set of "zip members with no individual_url" we already get for free
from `build_index.py`'s existing `matched individual_url: X / Y` log line.

## §4. Wire-through and verify

- Re-run `uv run python scripts/build_index.py`. Watch the
  "matched individual_url" count climb from 0 toward 8,069.
- Open `https://localhost:8765/` (or the live Pages URL) and pick a
  row; the **Individual** column should now show an "open" link that
  opens the phmpt.org page in a new tab.
- Spot-check a Bates-named file (e.g. one starting with `FDA-CBER-…`) —
  click "open" and confirm the destination is the right document.
- Spot-check that filter behavior still works (no regression).

## Critical files

| Path | Why |
| --- | --- |
| `docs/styles.css` | Reset button margin (§0) |
| `scripts/crawl_files.py` | **New**; mirrors `scripts/crawl_listing.py` |
| `scripts/build_index.py` | No change — already consumes `individual_urls.json` |
| `data/individual_urls.json` | **New** output of crawler |
| `data/orphans.json` | **New** output of crawler |

## What this plan deliberately does NOT do

- Does not remove the Individual column. The user explicitly said
  "not yet."
- Does not hyperlink the Filename cell to `individual_url`. Same
  reason — we'll do it in the deferred follow-up alongside dropping
  the Individual column.
- Does not attempt fuzzy filename matching unless the case-sensitive
  pass yields suspiciously few matches.
