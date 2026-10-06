"""Extract per-page text from every PDF, for the full-text search index.

This is the text SOURCE for search. `data/cache/ocr_text/` only holds the
image-only pages that needed OCR — the bulk of the text lives in the PDFs'
own text layer, which we read here with PyMuPDF `get_text`. Image-only pages
(no text layer) fall back to the OCR cache, or to live tesseract with --ocr.

Output: resumable JSONL per batch_code at `data/fulltext/{batch_code}.jsonl`,
one record per page: {"doc_id": <int>, "page": <int>, "text": <str>}.
Metadata is NOT duplicated here — ingest_opensearch.py joins it from
docs/data/index.json by doc_id. Resumable via data/fulltext/_done.json.

    uv run python scripts/extract_fulltext.py --limit 5        # smoke test
    uv run python scripts/extract_fulltext.py --batch p1215d-eua
    uv run python scripts/extract_fulltext.py                  # full corpus
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

try:
    import zipfile_deflate64 as zipfile
except Exception:  # pragma: no cover
    import zipfile

import fitz  # PyMuPDF
from tqdm import tqdm

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
INDEX = ROOT / "docs" / "data" / "index.json"
TOC = DATA / "toc.json"
ZIPS = DATA / "zips"
OCR_CACHE = DATA / "cache" / "ocr_text"
OUT_DIR = DATA / "fulltext"

SPARSE_CHARS = 20  # a page with fewer stripped chars is treated as image-only


def _rows(obj):
    return obj if isinstance(obj, list) else next(v for v in obj.values() if isinstance(v, list))


def ocr_fallback(filename: str, page0: int, run_ocr: bool, pdf_page) -> str:
    """Return OCR text for an image-only page: cache first, then optional live OCR."""
    for cand in (f"{filename}__p{page0 + 1:04d}.txt", f"{filename}__p{page0:04d}.txt"):
        p = OCR_CACHE / cand
        if p.exists():
            return p.read_text(encoding="utf-8", errors="ignore")
    if run_ocr:
        try:
            import pytesseract
            from PIL import Image
            import io
            pix = pdf_page.get_pixmap(dpi=200)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            return pytesseract.image_to_string(img)
        except Exception:
            return ""
    return ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", help="only this batch_code")
    ap.add_argument("--limit", type=int, help="max PDFs to process (smoke test)")
    ap.add_argument("--ocr", action="store_true", help="run live tesseract on image-only pages not in cache")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    done_path = OUT_DIR / "_done.json"
    done = set(json.loads(done_path.read_text())) if done_path.exists() else set()

    index = _rows(json.load(open(INDEX)))
    toc = _rows(json.load(open(TOC)))
    member_of = {(m["zip_source"], os.path.basename(m["member_name"])): m["member_name"] for m in toc}

    jobs = []
    for r in index:
        if (r.get("extension") or "").lower() != "pdf":
            continue
        if not (r.get("page_count") or 0):
            continue
        if args.batch and r.get("batch_code") != args.batch:
            continue
        if r["id"] in done:
            continue
        key = (r.get("zip_source"), r.get("filename"))
        if key not in member_of:
            continue  # phmpt-only orphan, no local bytes
        jobs.append((r, member_of[key]))
    if args.limit:
        jobs = jobs[: args.limit]

    print(f"{len(jobs)} PDFs to extract ({len(done)} already done)")
    zip_cache: dict[str, zipfile.ZipFile] = {}
    out_files: dict[str, object] = {}
    pages_written = sparse_pages = ocr_used = errors = 0

    def out_for(batch_code: str):
        if batch_code not in out_files:
            out_files[batch_code] = open(OUT_DIR / f"{batch_code}.jsonl", "a", encoding="utf-8")
        return out_files[batch_code]

    try:
        for r, member in tqdm(jobs, desc="pdfs"):
            zs, bc = r["zip_source"], r["batch_code"]
            zpath = ZIPS / bc / zs
            if not zpath.exists():
                errors += 1
                continue
            try:
                if zs not in zip_cache:
                    zip_cache[zs] = zipfile.ZipFile(zpath)
                doc = fitz.open(stream=zip_cache[zs].read(member), filetype="pdf")
            except Exception:
                errors += 1
                continue

            fh = out_for(bc)
            lines = []
            for i in range(doc.page_count):
                try:
                    page = doc[i]
                    text = page.get_text("text")
                    if len(text.strip()) < SPARSE_CHARS and page.get_images(full=False):
                        ocr = ocr_fallback(r["filename"], i, args.ocr, page)
                        if ocr.strip():
                            text = ocr
                            ocr_used += 1
                        sparse_pages += 1
                except Exception:
                    text = ""
                lines.append(json.dumps({"doc_id": r["id"], "page": i + 1, "text": text}, ensure_ascii=False))
                pages_written += 1
            fh.write("\n".join(lines) + "\n")
            doc.close()
            done.add(r["id"])
            if len(done) % 200 == 0:
                done_path.write_text(json.dumps(sorted(done)))
                for f in out_files.values():
                    f.flush()
    finally:
        for f in out_files.values():
            f.close()
        done_path.write_text(json.dumps(sorted(done)))

    print(f"done: {pages_written:,} pages written | {sparse_pages:,} image-only "
          f"({ocr_used:,} filled via OCR) | {errors} pdf errors")
    print(f"output: {OUT_DIR}/<batch_code>.jsonl   progress: {done_path}")


if __name__ == "__main__":
    main()
