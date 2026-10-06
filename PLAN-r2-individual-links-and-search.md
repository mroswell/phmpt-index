# Plan: Self-hosted individual document links on Cloudflare R2 (+ later: full-text search)

> Saved 2026-10-05. Status: approved-in-principle, not yet started. Resume when ready.
> Backup copy of this plan also lives at
> `~/.claude/plans/the-new-content-doesn-t-quizzical-ember.md`.

## Context

The FOIA index site (`docs/`, served at foia.coviddocuments.com via GitHub Pages) links each
document to the best available URL: a phmpt.org individual file, an S3 zip bundle, or an ICAN
fallback. The older coviddocuments.com hosted every document individually on Google Drive, but
only through ~early 2024. Newer documents — especially the recent Moderna EUA and Pfizer 12‑15
productions — have **no individual link**; the only access is the multi‑GB source zip.

Measured gap (from `docs/data/index.json`, 8,791 rows):

- **7,467 non‑`.xpt` docs / 27 GB** total. Of these, **4,851 already link to phmpt**, **2,616 have no link at all**.
- **833 `.xpt` SAS datasets / 124 GB** — not viewable in a browser.

**Decision (user):** **phmpt stays the primary link wherever it exists** (authoritative public source,
best for citations). Self‑host on **Cloudflare R2** only the **~2,616 non‑`.xpt` files that have no link
today** (the gap, ~10 GB) — `fill‑gap`, not rehost‑all. Bundle the `.xpt` files into
**per‑company/per‑study zips** (one link per study group — no individual download, since they can't be
viewed). Keep an **upload ledger** so re‑runs skip anything already uploaded (idempotent, resumable, crash‑safe).

Intended outcome: every non‑xpt row is clickable (phmpt where available, R2 for the gap); every xpt row
links to its study dataset bundle; monthly new bundles flow through the same scripts incrementally.

## Locked decisions (one‑way doors)

1. **R2 key scheme** (permalink commitment):
   - Individual docs: `files/{batch_code}/{sha256_12}-{safe_basename}`
     (sha256‑12 prefix makes keys content‑addressed → structurally resolves the one real cross‑zip
     basename collision and dedupes identical bytes; `batch_code` folder keeps it browsable.)
   - xpt bundles: `datasets/{company_slug}/{study_slug}.zip` (e.g. `datasets/pfizer/c4591001.zip`).
2. **Public base URL:** a custom R2 domain (e.g. `files.coviddocuments.com`), baked into every stored
   URL. `r2.dev` only for the smoke test. **User must connect the domain + provide credentials before the full run.**
3. **Dataset versioning:** stable key, overwrite in place — `datasets/pfizer/c4591001.zip` always
   points at the newest complete bundle; link never changes, content grows.
4. **Scope & precedence:** `fill‑gap` (default) — upload only the ~2,616 non‑xpt files lacking any link
   (~10 GB). User‑facing precedence: **phmpt `individual_url` → R2 `hosted_url` → `ican_url` → `dataset_zip_url`**.
   phmpt remains canonical; R2 only covers what phmpt doesn't. (A `--rehost-all` toggle exists for optional
   full mirroring later, but is off.)

## Prerequisites (user actions — needed before the full upload run)

- Create an R2 bucket; enable public access via a **custom domain** (recommended `files.coviddocuments.com`).
- Create an **Object Read & Write** API token scoped to that bucket.
- Provide creds via a gitignored `.r2.env` at repo root:
  `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `R2_PUBLIC_BASE`.
- Note: upload is ~134 GB one‑time (~10 GB non‑xpt gap + 124 GB xpt). Egress is free; storage ≈ $2/mo.
  Upload time is connection‑bound and resumable — expect the xpt pass to run overnight.

## Implementation

### New shared module — `scripts/_r2.py`
- boto3 S3 client against R2 (`endpoint_url=https://{ACCT}.r2.cloudflarestorage.com`, `region_name="auto"`,
  path addressing, adaptive retries). **Add `boto3>=1.35` to `pyproject.toml`** (rclone is installed but
  can't stream single zip members with custom keys; keep rclone as the audit tool only).
- Ledger load/save with atomic `os.replace` of a `.tmp` file, flushing every N uploads.
- Helpers: `safe_name()` (reuse the `re.sub(r"[^A-Za-z0-9._-]+","_", name)` logic from
  `FDA-FOIA-2026-6007/host_new_files.py`), streamed `sha256()`, `public_url(key)`.
- Do **not** set per‑object ACLs (R2 rejects them); public read is a bucket‑level setting.
- Raise a clear error if any `R2_*` env var is missing. Never log/commit secrets.

### `scripts/host_individual_files.py` (non‑xpt uploader)
- Walk `data/toc.json`; for each non‑`.xpt` member **whose basename is absent from
  `data/individual_urls.json`** (fill‑gap; `--rehost-all` processes every non‑xpt instead):
  open `data/zips/{batch_code}/{zip_source}` with `import zipfile_deflate64 as zipfile`
  (same pattern as `extract_toc.py`), stream the member once to compute sha256, build the key,
  check ledger → skip if size matches; else `upload_fileobj` straight from `zf.open(member)` with
  `TransferConfig(multipart_threshold=64MB, chunk=64MB, concurrency=4)`.
- Append to ledger `data/r2_uploads.json` (`individual` section), keyed by `"{zip_source}||{member_name}"`
  (identical to build_index's registry key, so the later join is exact and collision‑proof).
- Flags: `--batch <code>`, `--limit N`, `--verify` (re‑`head_object`/re‑hash), `--fill-gap`.

### `scripts/bundle_xpt_datasets.py` (xpt grouping)
- Group all `.xpt` by `(company, study)`. Company from `batch_code` (reuse build_index BATCH_META).
  `study_slug` via ordered regex on `member_name`, most‑specific first:
  `c4591\d{3}` → `bnt162-?\d+` → `mrna-1273-p\d{3}(?:-add\d+)?` → `\bp\d{3}\b` → `bimo` → else `misc`.
- Build one zip per group in `tmp/xpt_bundles/` (gitignored), streaming members from source zips;
  upload (multipart, 256 MB chunks); then delete the local temp zip (disk headroom). One group at a time.
- Resumable at group granularity via a `members_fingerprint` (sha256 of sorted member identities) in
  the ledger `datasets` section: unchanged fingerprint → skip; changed → rebuild+reupload that group only.
- Emit `data/xpt_dataset_map.json`: `{ "{zip_source}||{member_name}": {dataset_zip_url, study, company} }`.

### `scripts/build_index.py` (additive, follows the ICAN‑fallback template at lines ~102‑112 / ~159‑161)
- Load ledger `individual` → `hosted_by_key`, and `xpt_dataset_map.json` → `dataset_by_key`
  (both keyed by the `reg_key` already computed at line ~135).
- Add two row fields: `"hosted_url": hosted_by_key.get(reg_key)`, `"dataset_zip_url": dataset_by_key.get(reg_key)`.
  Add `None` defaults to orphan rows too (schema uniformity).
- **Permalink stability:** no change to `id_registry.json` keys/IDs — fields are added to existing rows.
  Keep ICAN populated only when no self‑hosted/phmpt link exists.

### `docs/app.js`
- Link precedence at line ~175: `const fileUrl = r.individual_url || r.hosted_url || r.ican_url || r.dataset_zip_url || null;`
  (phmpt wins when present; R2 fills the gap).
- `.xpt` rows: link the filename to `dataset_zip_url`, render a small chip via the existing `tag()` helper
  (e.g. `study: c4591001`) and a `title` explaining it's the grouped, non‑viewable dataset bundle
  (mirror the existing ICAN `title` affordance at lines ~176‑178).
- Treat `hosted_url` like `individual_url` in the `f-individual` filter (lines ~91‑93) so "has a per‑file link" stays meaningful.

### `scripts/verify_r2_links.py` (read‑only)
- HEAD every URL in the ledger; report non‑200s and content‑length mismatches.

### Config / housekeeping
- `.gitignore`: add `tmp/xpt_bundles/`, `.r2.env`, `*.env`.
- Commit `data/r2_uploads.json` and `data/xpt_dataset_map.json` (small, like `id_registry.json`) so
  build_index and monthly re‑runs read permalinks without contacting R2.
- Update `CLAUDE.md` pipeline table with the new steps 4.5 (host non‑xpt) and 4.6 (bundle xpt),
  and the incremental monthly flow.

## Monthly incremental flow (new bundles)
`download_zips.py` → `extract_toc.py` → `host_individual_files.py` (ledger skips done, uploads only new)
→ `bundle_xpt_datasets.py` (fingerprint rebuilds only changed study groups) → `build_index.py`.

## Verification (small subset first)
1. Smoke test one small batch to a `test/` key prefix: `host_individual_files.py --batch p1215d-eua --limit 20`.
2. `curl -sI {public_url}` on a sample from the ledger → `HTTP/2 200` + correct content‑length;
   `rclone ls r2:bucket/files/p1215d-eua/` to audit.
3. One small xpt group end‑to‑end (`--study bimo`): download the `.zip`, confirm members open.
4. `uv run python scripts/build_index.py`, serve `docs/`, confirm gap rows now link to R2, xpt rows show
   the dataset chip+link, phmpt links intact, build summary sane.
5. Re‑run uploader → ~all skipped, ledger unchanged (idempotency proof).
6. Then full non‑xpt pass, then full xpt pass (overnight, resumable).

## Phase 2 (later): full‑text search
Facts: no document‑body text is published today; `data/cache/ocr_text/` has 8,232 page OCR files
(server‑only) and `hidden_substance/` has redaction snippets. Corpus is **8,791 documents / ~4.28M PDF
pages** and growing — far beyond a client‑side index.

**Pagefind is NOT suitable at this scale** (a client-side index can't hold 4.3M pages). **Decision: use
OpenSearch (engine family: OpenSearch/Elasticsearch), hosted on Bonsai.io** (managed, engine-agnostic,
low-ops, good for a non-expert; AWS OpenSearch Service or Elastic Cloud are alternatives).

**Granularity — go straight to page-level** (the cost gap vanished once measured):
- Measured: 4.28M pages, 96.7% M5, ~644 text-bytes/M5 page → **~2.7 GB raw text total** → **~3–6 GB index**
  (≈6–12 GB with 1 replica). Sample covered 99.5% of pages (only 0.5% sit in >150 MB files).
- **Cluster: 1 primary shard, 1 replica (or 0 — index is rebuildable), ~2–4 GB RAM, ≥15–20 GB storage.**
  A small/standard Bonsai tier (~tens of $/mo), not a large cluster. Doc-level stays a fallback if cost ever matters.
- **CHOSEN (2026-10): Bonsai "Standard Birch" $90/mo** — OpenSearch 3.8.0, AWS us-east-1 (Virginia),
  Production. 15M-doc cap / 25 GB storage / 1.83 GB memory — comfortable headroom. Run **1 replica**
  (room to spare). Cluster name `coviddocuments-search`. (Engine OpenSearch 3.8.0 is newer than the "2.x"
  placeholder in the checklist — fine; `opensearch-py` supports it.)
- Sizing is therefore **pre-solved — no expert needed for it.** Jump to the hit page using existing per-page OCR (`data/cache/ocr_text/`).

**Engine note:** OpenSearch = Apache-2.0, open, cheaper; Elasticsearch = richer tooling, pricier. Core
full-text behaviour is equivalent. Scripts use `opensearch-py` (works against Bonsai's OpenSearch).

**Where to hire an OpenSearch/Elasticsearch expert (flagged in the setup checklist too):**
- ✅ Index **mapping & analyzers** (tokenization, Bates/hyphen handling, highlighting, whether to store
  `_source`) — set before load, painful to change after; the one place expert review (~1–2 h) earns its keep.
- ~~Cluster/plan sizing~~ — **solved by measurement** (above); it's a small cluster. No expert needed.
- ✅ Security/backups — only if self-hosted (Bonsai handles this).
- 🟡 Bulk-ingest tuning, relevance tuning — Claude scripts; expert review optional.
- ❌ Provider provisioning (checklist covers it) and front-end wiring (Claude does it).

**Work:** full PDF-text extraction pass (`PyMuPDF get_text`, OCR fallback for image-only pages) → index
mapping (expert-reviewed) → bulk ingest → search box in `docs/app.js` querying the cluster. Connection via
gitignored `.search.env` (`SEARCH_ENGINE`, `SEARCH_URL`). Scope as its own session once Phase 1 ships.

## Model recommendation
- **Opus 4.8** (current) for design and the gnarly bits: filename→study parsing, key‑scheme/permalink
  decisions, build_index/app.js edits. Use **/fast** for snappier iteration without downgrading the model.
- **Sonnet 4.6** for the bulk mechanical runs once scripts are written and validated (extraction/upload
  batches, verification sweeps) — cheaper and quick for well‑specified work.
- Haiku 4.5 isn't needed here.

## Files
**New:** `scripts/_r2.py`, `scripts/host_individual_files.py`, `scripts/bundle_xpt_datasets.py`,
`scripts/verify_r2_links.py`, `data/r2_uploads.json`, `data/xpt_dataset_map.json`.
**Modified:** `scripts/build_index.py`, `docs/app.js`, `pyproject.toml`, `.gitignore`, `CLAUDE.md`.

## State at save time (today's session, already done — not part of this plan)
- Refreshed the Cloudflare browser profile; re-crawled phmpt.org (73 → 77 bundles).
- Downloaded + ingested the 4 new Moderna EUA bundles (Jun–Sep 2026, 231 docs); index rebuilt to 8,791 rows.
- `scripts/crawl_listing_headful.py` added (headful crawler that works around the Cloudflare headless re-challenge).
- Backups left: `data/zips.prev.json`, `data/id_registry.prev.json` (safe to delete).
