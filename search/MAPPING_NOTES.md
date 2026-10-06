# OpenSearch mapping — notes for expert review

**Artifact:** `search/opensearch_mapping.json` (index settings + mappings for the page-level FOIA search index).
**Engine:** OpenSearch 3.8.0 on Bonsai "Standard Birch" (1.83 GB RAM, 25 GB storage).
**Scale:** ~4.28M page documents, ~2.7 GB text (measured). One OpenSearch document = one PDF page.
**Document `_id` convention:** `"{doc_id}:{page}"` (doc_id = the stable integer id from `docs/data/index.json`).

Please review the decisions below and the **Open questions** at the end. These settings are set at index
creation and are painful to change after a 4.28M-doc bulk load, so this ~1–2 h review is the high-value step.

## Decisions made (and why)

- **1 primary shard, 1 replica.** Index is only ~3–6 GB; shards should be 10–50 GB, so splitting would hurt.
  Replica gives availability headroom on Birch (25 GB). During bulk load the ingest script temporarily sets
  `number_of_replicas: 0` and `refresh_interval: -1`, then restores (`1` / `30s`) and force-merges.
- **`_source` kept.** Only ~2.7 GB total; keeping it makes highlighting simple and lets us reindex/debug.
  (If storage ever tightens, excluding `text` from `_source` + term vectors is the alternative — see Q3.)
- **`text` field:** `foia_english` analyzer (standard tokenizer → possessive stemmer → lowercase →
  English stop words → English stemmer). Sub-field `text.exact` (lowercase only, no stemming/stopwords) for
  precise phrase/quoted search. Query plan: match on `text` for recall, boost `text.exact` for phrases.
- **`bates_text`:** `bates_analyzer` (whitespace tokenizer → `word_delimiter_graph` with
  `preserve_original` + `split_on_numerics`). Lets `FDA-CBER-2021-5683-0001234` match on the whole string,
  on `5683`, or on `0001234`. `bates_start`/`bates_end` are exact `keyword`s for range/exact lookups.
- **Metadata as `keyword`** (company, license, age_group, module, study, batch_code, zip_source) for
  faceting/filtering that mirrors the existing site filters. `doc_id`/`page`/`total_pages` as `integer`.
- **`url` is `index: false`** — stored for display/click-through only, never searched.
- **`dynamic: "strict"`** — reject unexpected fields at ingest so schema drift fails loudly.

## Resolved — expert review, tested live on the cluster (2026-10-06)

All decisions below were verified empirically against the live OpenSearch 3.8.0 cluster via the `_analyze`
API and by creating the index from this exact mapping (accepted). A second human review is optional, not
required, for an index this size.

1. **Redaction markers → BOTH.** (a) char_filter `redaction_markers` maps `(b)(1)…(b)(7)(F)` to single
   tokens (`(b)(4)→exemptionb4`) on the `text`/`text.exact` analyzers, so free-text search of a literal
   marker works; (b) a dedicated `markers` keyword field, regex-extracted at ingest
   (`\(b\)\(\d+\)(?:\([A-F]\))?`), for clean faceting/counts. Rationale: standard tokenizer alone turned
   `(b)(4)` into noisy `b`,`4`. Verified live: `(b)(4)→exemptionb4`, `(b)(7)(C)→exemptionb7c`.
2. **Stemming → `kstem`, not Porter.** Porter over-stemmed medical terms (`immunogenicity→immunogen`,
   `myocarditis→myocard`, `vaccinations→vaccin`); kstem kept usable forms (`immunogenic`, `vaccination`,
   `analyze`). Minor non-dictionary stems (`myocarditi`) are harmless — query uses the same analyzer, so
   matching stays consistent.
3. **Highlighting → default (unified, from `_source`).** No term vectors — saves ~15–30% index size; fine
   at this scale.
4. **Shards → 1 primary + 1 replica.** Confirmed right for ~3–6 GB / 4.28M docs at low QPS.
5. **`filename` → `bates_analyzer` + `.raw` keyword.** Standard tokenizer left `125742_s1_m5_c4591001` as one
   blob; the bates analyzer split out `adsl`, `4591001`, `m5`, etc. (findable). Verified live.
6. **Main `text` → kept clean** (standard tokenizer, no `word_delimiter`). IDs/Bates are searchable via
   `bates_text`, `filename`, and `markers`; keeping `text` clean avoids index bloat and noise.

**Also fixed during review:** `ingest_opensearch.py` used positional opensearch-py client calls
(`indices.exists(name)`) that the client rejects; changed to keyword args (`index=name`). Verified.
