# OCR pilot findings (Phase 2)

Re-OCR'd the worst-scoring documents at 300 DPI (Tesseract) and compared to the
current embedded text.

## Result: re-OCR is NOT the right fix

| doc | page kind | embedded garble | re-OCR garble | verdict |
|---|---|---:|---:|---|
| 30_BLA …Telecon_Other | born-digital | 0.318 | 0.318 (garbage) | re-OCR worse |
| 78G_BLA …Memo_Review | born-digital | 0.318 | 0.318 | no change |
| 27034…CRF c4591001-1231 | born-digital | 0.077 | 0.026 | lower score but LOSES content |

**Key findings**
- The worst "garbled" pages are **born-digital tables**, not scans. `TREATMENT`
  renders as `TREATME NT` because the word wraps inside a narrow column — the
  text layer faithfully captures that. Re-OCR (rasterize + Tesseract) produces
  **garbage** on these dense tables (`2 eS) a" a" …`), so it makes them worse.
- The CRF page's embedded text is actually readable; re-OCR only *looks* better
  by the garble metric but **drops structured content** (form names, headers).
- Truly image-only pages (near-empty text) are already handled by the existing
  OCR fallback in `extract_fulltext.py` → `data/cache/ocr_text/`.

## Recommendation
Do **not** run a re-OCR (neither targeted rollout nor full corpus): the measured
problem is ~0.5% of pages, and its worst cases are layout artifacts that OCR
cannot fix. If we ever want to recover the split-word recall (so "treatment"
matches "TREATME NT"), the cheap lever is an **index-time normalization** that
re-joins obvious OCR-split words — no re-OCR needed. Low priority given scope.
