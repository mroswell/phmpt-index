# PHMPT FOIA Document Index - Development Guide

This project creates a searchable web interface for FDA's COVID-19 vaccine FOIA document releases published by [PHMPT.org](https://phmpt.org/multiple-file-downloads/). It transforms 18GB of ZIP bundles into a static website with 8,000+ searchable documents.

## Data Pipeline Workflow

The pipeline consists of 9 Python scripts that must run **sequentially**. Each step depends on outputs from previous steps, but all scripts are **resumable** and cache-aware.

### Core Pipeline (Normal Workflow)

```bash
# 1. One-time setup - creates persistent browser profile for Cloudflare bypass
uv run python scripts/bootstrap.py

# 2. Scrape ZIP bundle metadata from phmpt.org/multiple-file-downloads/
uv run python scripts/crawl_listing.py

# 3. Download ~18GB of ZIP files from S3 (resumable)
uv run python scripts/download_zips.py

# 4. Extract document index from ZIP central directories (parallel)
uv run python scripts/extract_toc.py

# 5. Scrape individual file URLs from product pages
uv run python scripts/crawl_files.py

# 6. Assemble final searchable index
uv run python scripts/build_index.py

# 7. Serve the website
cd docs && python -m http.server 8765
```

### Script Dependencies

| Script | Input | Output | Purpose |
|--------|-------|--------|---------|
| `bootstrap.py` | — | `.profile/` | Solve Cloudflare challenge; save browser state |
| `crawl_listing.py` | `.profile/` | `data/zips.json` | Scrape 73 ZIP bundle URLs + metadata |
| `download_zips.py` | `zips.json` | `data/zips/*.zip` | Download ZIPs with resume support |
| `extract_toc.py` | `zips/*.zip` | `data/toc.json` | Read ZIP directories + PDF page counts |
| `crawl_files.py` | `.profile/` | `individual_urls.json`, `orphans.json` | Find individual download URLs |
| `build_index.py` | All above | `docs/data/index.json` | Join data into final searchable index |

### Optional/Debug Scripts

- `probe_files_pages.py` - Discovery tool (dumps HTML to `.scratch/`)
- `probe_orphan_zips.py` - Size-check orphan ZIPs  
- `check_orphan_outliers.py` - Verify orphan metadata

### When to Re-run Scripts

| Scenario | Scripts to Run | Reason |
|----------|---------------|---------|
| **Full rebuild** | Delete `data/cache/`, run 2-6 | Fresh start from scratch |
| **New ZIPs added** | 2, 3, 4, 6 | Skip individual file crawling |
| **Metadata changes only** | 6 only | No need to re-download/extract |
| **Cloudflare blocked** | 1, then others as needed | Refresh browser authentication |
| **Add new product pages** | 5, 6 | Crawl new individual URLs |

### Caching and Resumability

- **`data/cache/*.zip.json`** - Per-ZIP processing results; deleted files will be reprocessed
- **ZIP downloads** - Incomplete downloads resume from last byte using HTTP Range requests
- **PDF processing** - Skips files already in cache; delete cache entry to reprocess specific file
- **Browser profile** - `.profile/` persists Cloudflare bypass; valid for weeks/months

## Domain Knowledge

### FOIA Document Classification

**BLA vs EUA:**
- **BLA** = Biologics License Application (full approval)  
- **EUA** = Emergency Use Authorization (temporary approval)

**Age Groups:**
- **adult** = 18+ years
- **16+** = 16+ years (Pfizer)
- **12-15** = pediatric 12-15 years (Pfizer only)

**Companies:**
- **Pfizer** = Pfizer-BioNTech mRNA vaccine
- **Moderna** = Moderna mRNA vaccine

### Batch Code System

ZIP bundles follow a strict naming convention that encodes regulatory metadata:

```
md      → Moderna BLA adult
md-eua  → Moderna EUA adult
pd      → Pfizer BLA 16+
pd-eua  → Pfizer EUA 16+
p1215d  → Pfizer BLA 12-15
p1215d-eua → Pfizer EUA 12-15
```

**Examples:**
- `md-508-5-3-10312023.zip` = Moderna EUA adult, batch 508
- `pd-125126-200-8-01172024.zip` = Pfizer BLA 16+, batches 125-126

**Parsing Logic:**
The code uses longest-match-first regex to derive company/license/age from batch codes. Modify `build_index.py` batch code mappings if new categories appear.

### phmpt.org Technical Constraints

**Cloudflare + Kasada Protection:**
- phmpt.org blocks automated requests with advanced bot detection
- **Solution:** Real browser automation with persistent profile
- **Browser state:** Stored in `.profile/` directory (keep this!)
- **Re-authentication:** Run `bootstrap.py` if requests start failing

**Dual Hosting Architecture:**
- **ZIP files:** Direct S3 URLs (no protection) - use `httpx`
- **Individual files:** phmpt.org wp-content URLs (protected) - use Playwright navigation
- **Rate limiting:** Serial downloads prevent IP blocking

### Document Metadata Parsing

**eCTD Module Classification:**
- Regex: `_M([1-5])(?:[^0-9A-Za-z]|$)` extracts module number
- **M1** = Administrative (cover letters, forms)
- **M2** = Common Technical Document summary  
- **M3** = Quality (manufacturing, stability)
- **M4** = Nonclinical (animal studies)
- **M5** = Clinical (human trials)

**Bates Number Ranges:**
- Format: `FDA-CBER-DDDD-DDDD-START-END`
- Regex: `^FDA-CBER-\\d+-\\d+-(\\d+)-(\\d+)` extracts start/end
- Used for document cross-referencing and citation

**File Type Detection:**
- Extension-based: `.pdf`, `.xlsx`, `.docx`, `.xpt` (SAS datasets), etc.
- Case-insensitive matching
- Empty extension for files without dots

**Page Count Extraction:**
- PDF only via PyMuPDF streaming (no file extraction)
- Skips PDFs >2GB to prevent memory exhaustion
- Non-PDF files report `null` page count

## File and Directory Structure

### Version-Controlled Data

These files are committed to git for fast clone-and-serve:

- **`data/zips.json`** (71KB) - Authoritative ZIP metadata; rebuilt by `crawl_listing.py`
- **`data/toc.json`** (3MB) - Complete document index; rebuilt by `extract_toc.py`
- **`data/id_registry.json`** (775KB) - Stable ID mapping for permalinks; **never delete**

### Generated Data

These files are rebuilt automatically from committed data:

- **`data/individual_urls.json`** (893KB) - Filename → phmpt.org URL mapping
- **`data/orphans.json`** (214KB) - 795 files only available individually
- **`docs/data/index.json`** (4.2MB) - Final searchable index loaded by frontend

### Cache and Temporary Files

- **`data/cache/*.zip.json`** - Per-ZIP processing cache; safe to delete for rebuilds
- **`data/zips/`** - Downloaded ZIP files (~18GB); in `.gitignore`
- **`.profile/`** - Playwright browser state; **critical for Cloudflare bypass**
- **`.scratch/`** - Debug outputs; safe to delete

### Web Interface

- **`docs/index.html`** - Static HTML with filter controls
- **`docs/app.js`** - Frontend logic (filtering, sorting, pagination, URL state)
- **`docs/styles.css`** - Design system (Pfizer blue, Moderna red color scheme)

## Development Guidelines

### Maintaining ID Stability

**Critical:** Document IDs must remain stable across rebuilds for permalinks to work.

- **`data/id_registry.json`** maps `(zip_source||member_name)` → permanent integer ID
- **Never modify** the ID generation logic in `build_index.py`
- **New files** get `max_id + 1`; deleted files keep their ID slot
- **Orphan files** use `orphan||<url>` as the registry key

### Adding New Filters

To add a filter to the web interface:

1. **Backend:** Add field extraction logic in `build_index.py` 
2. **Frontend HTML:** Add filter control in `docs/index.html`
3. **Frontend JS:** Add filtering logic in `docs/app.js` `applyFilters()` function
4. **URL state:** Update `getStateFromUrl()` and `updateUrl()` for permalinks

### Modifying Scrapers

**Preserve resumability:**
- Always check for existing outputs before processing
- Use `if not output_file.exists()` patterns
- Cache intermediate results when possible

**Test with subsets:**
- Use small batch codes for testing: `md` (Moderna) has fewer files than `pd` (Pfizer)
- Test individual product pages before full crawls
- Verify against known good data

### Performance Considerations

**Memory management:**
- ZIP reading uses central directory only; never extracts files
- PDF processing streams into PyMuPDF without disk writes  
- Frontend loads entire 4.2MB index once, filters client-side

**Parallelization:**
- `extract_toc.py` uses multiprocessing for CPU-bound PDF parsing
- Other scripts are I/O bound; serial execution prevents rate limiting

**Large file handling:**
- Skips PDFs >2GB to avoid memory exhaustion
- HTTP Range requests for resumable downloads
- Progress bars via `tqdm` for long operations

### Common Troubleshooting

**"403 Forbidden" from phmpt.org:**
- Re-run `bootstrap.py` to refresh Cloudflare bypass
- Check that `.profile/` directory exists and has browser state

**"File not found" errors:**
- Verify pipeline execution order; each script needs inputs from previous steps
- Check that ZIP downloads completed successfully

**Slow PDF processing:**
- Delete `data/cache/` to see progress; cached files are skipped silently
- Large PDFs (>500 pages) take 30+ seconds each

**Permalink breaks:**
- Never modify `data/id_registry.json` manually
- Document IDs are stable; metadata changes don't affect URLs

### Future Extensions

**Full-text search:**
- Could add OCR pipeline with `pypdf` or `pdfplumber`
- Elasticsearch/Solr for server-side search
- Client-side search limited by 4.2MB index size

**API endpoints:**
- Current system is static files only
- Could add Flask/FastAPI for dynamic queries
- Database would improve filter performance

**Batch processing:**
- Pipeline currently processes all ZIPs
- Could add date-based incremental updates
- Would need change detection in `crawl_listing.py`

## Deployment

**GitHub Pages:**
- Published from `docs/` directory to `https://mroswell.github.io/phmpt-index/`
- Add `.nojekyll` file to prevent Jekyll processing
- Index rebuilds push new `docs/data/index.json`

**Local development:**
- `cd docs && python -m http.server 8765`
- No build step required; pure static files
- Test filter changes by refreshing browser