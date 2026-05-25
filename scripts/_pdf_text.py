"""Shared text-extraction helper that transparently falls back to the
OCR cache when PyMuPDF returns too-little text for a page.

Used by every scanner that does page-by-page text matching against the
corpus:
  - extract_module_exemptions.py
  - scan_individual_via_ican.py
  - scan_individual_via_phmpt.py
  - scan_pharmacovigilance_terms.py
  - scan_statute_references.py

The OCR cache is populated by scripts/extract_ocr_text.py.
"""

from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF

ROOT = Path(__file__).resolve().parent.parent
OCR_CACHE = ROOT / "data" / "cache" / "ocr_text"


def ocr_path_for(filename: str, page_num: int) -> Path:
    """Canonical OCR cache file path."""
    return OCR_CACHE / f"{filename}__p{page_num:04d}.txt"


def get_page_text(
    filename: str,
    page: fitz.Page,
    page_num: int,
    *,
    ocr_threshold: int = 30,
) -> str:
    """Return the text of a single PDF page.

    Tries `page.get_text("text")` first. If the result has less than
    `ocr_threshold` non-whitespace characters AND an OCR cache file
    exists for this (filename, page_num), uses the OCR text instead.

    If no OCR cache is available, returns whatever PyMuPDF gave us
    (possibly an empty string).
    """
    native = page.get_text("text") or ""
    if len(native.strip()) >= ocr_threshold:
        return native
    ocr_file = ocr_path_for(filename, page_num)
    if ocr_file.exists():
        try:
            return ocr_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return native
    return native
