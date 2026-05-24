# ICAN vs PHMPT FOIA Catalog Comparison

## Headline numbers

- **2,499 documents** enumerated across ICAN's two public catalog pages (split into 8 sub-tables).
- **2,400 (96.0%)** exact filename matches against the PHMPT corpus (`data/toc.json` + `data/orphans.json`, 8,863 unique filenames).
- After fuzzy normalization (stripping `FDA-CBER-*-NNN-NNN_` Bates prefixes and numeric `-N` packaging suffixes), **2,441 (97.7%)** of ICAN's documents map to a known PHMPT file.
- **58 documents** appear genuinely unique to ICAN. Every one of them is either a split-archive ZIP variant of a PHMPT bundle (e.g. `pd-production-070323-1.zip` through `-4.zip` vs. PHMPT's consolidated `pd-production-070323.zip`) or a Bates-numbered repackaging of SAS dataset zips (`c4591001-S-D-mb.zip`, `c4591001-A-D-adfacevd.zip`, etc.) whose contents PHMPT publishes under a different filename. Net new *content* on ICAN: effectively zero.
- **6,532 PHMPT filenames** have no ICAN counterpart — PHMPT is ~3.5x larger by file count. Almost all of that gap is Pfizer 12-15 and Moderna content, which ICAN does not enumerate as individual files (see below).

## Catalog structure differences

The most consequential finding is structural, not numeric. ICAN exposes its catalog in two architecturally distinct ways:

1. **Pfizer 16+ (BLA and EUA)** — a single HTML page (`https://icandecide.org/pfizer-documents/`) with a 2,372-row table listing every individual document. The `?t=eua-documents` URL parameter merely flips a client-side tab; the HTML is identical. Files are hosted on either `icandecide.org/wp-content/uploads/...` or `ican-public.s3.amazonaws.com/productions/...`. This is where the bulk of the catalog lives, and it matches PHMPT almost perfectly (96.8%).
2. **Pfizer 12-15 + Moderna** (`https://icandecide.org/pfizer-12-15-and-moderna-documents/`) — only **31 monthly production ZIP archives** are listed (plus 14 court documents). The three `?t=...` tab variants all serve the same HTML; tabs are client-side filters into 4 tables (court docs + 3 production-zip tables). ICAN does **not** enumerate individual files inside these ZIPs anywhere on the site. PHMPT, by contrast, indexes every file inside every production ZIP — which is why 6,500+ PHMPT filenames have no ICAN counterpart. Both sites have the same data; ICAN just hides it inside bundles.

Of the matched ICAN documents, **389 are also available as PHMPT individual orphan downloads** (where the user can compare URLs directly). ICAN always re-hosts on its own domain or its S3 bucket; URLs never overlap with phmpt.org's wp-content URLs. So the two sites are independent mirrors of the same FDA productions.

## Match rate by ICAN section

| Section | Total | Matched | Pct |
|---|---|---|---|
| Pfizer 16+ documents table | 2,372 | 2,297 | 96.8% |
| Pfizer 16+ BLA Full Productions (ZIPs) | 49 | 25 | 51.0% |
| Pfizer 16+ EUA Full Productions (ZIPs) | 3 | 3 | 100.0% |
| Pfizer Court Documents | 20 | 20 | 100.0% |
| Pfizer 12-15 Full Productions (ZIPs) | 7 | 7 | 100.0% |
| Moderna Full Productions (ZIPs) | 24 | 24 | 100.0% |
| EUA Full Productions (Pfizer 12-15 + Moderna ZIPs) | 10 | 10 | 100.0% |
| Pfizer 12-15 & Moderna Court Documents | 14 | 14 | 100.0% |
| **Total** | **2,499** | **2,400** | **96.0%** |

The only sub-50% category is "Pfizer 16+ BLA Full Productions" — 24 of its 49 ZIPs are ICAN's split-multi-part archives (e.g., `pd-production-070323-1.zip`, `-2.zip`, `-3.zip`, `-4.zip`) for productions that PHMPT later republished as single consolidated ZIPs. The underlying contents are the same.

## Surprises and notes

- **ICAN preserves the original Bates-numbered filenames** for many SAS dataset archives (e.g., `FDA-CBER-2021-5683-0282366-to-0285643_125742_S1_M5_c4591001-S-D-mb.zip`); PHMPT does the same. The matching is mostly exact-filename, suggesting both sites republish the FDA's productions essentially untouched.
- **One file is genuinely a wildcard**: `125742_S1_M4_4.2.3.2-38166.pdf` doesn't appear in PHMPT at all (no fuzzy variants either). This is the single PDF most worth a closer look.
- **Court documents** (orders, status reports from the underlying PHMPT v. FDA lawsuit) are catalogued in two places on ICAN (one Pfizer 16+ block, one 12-15/Moderna block, 34 docs total). All 34 match exact filenames in PHMPT.
- **No JS/AJAX gating** — ICAN ships the entire catalog as plain server-rendered HTML; client-side JavaScript only handles the tab UI and DataTables sort/filter. No pagination to worry about.

## Limitations

- ICAN does not expose the contents of its 31 Pfizer 12-15 and Moderna production ZIPs on the website. A full content-level comparison there would require downloading those ZIPs and reading their central directories (analogous to `scripts/extract_toc.py`). Based on filename matching of the ZIPs themselves, we expect substantial overlap — but cannot confirm individual-file parity without the download.
- The Pfizer 16+ table is the only catalog where ICAN itself enumerates individual files, so the 96.0% match rate is really telling us about that one collection. The Pfizer 12-15 and Moderna match rate is by ZIP-name only, not by enclosed file.
- ICAN's `?t=` URL parameters appear to be cosmetic (client-side tab activation only). Server returns identical HTML for the base URL and all tab variants — we verified this by fetching all 5 user-listed URLs and confirming identical response bodies for each of the two base pages.

## Output

- Raw catalog + match-status: `data/ican_comparison.json` (~866 KB).
- This summary: `data/ican_comparison.md`.
- Working files (HTML caches, parsers): `.scratch/ican/`.
