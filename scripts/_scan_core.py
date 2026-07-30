"""Shared FOIA-exemption marker scanner used for BOTH the trove bundles and the
PHMPT source zips, so the two sides are scanned identically and any difference
in counts reflects a real difference in redaction — not OCR methodology.

scan_pdf_bytes(pdf_bytes) -> {total_pages, total_markers, by_marker, pages_ocred}
"""

from __future__ import annotations

import io
import re
from collections import Counter

import fitz  # PyMuPDF
import pytesseract
from PIL import Image

MARKER_RE = re.compile(r"\(\s*b\s*\)\s*\(\s*(\d+)\s*\)(?:\s*\(\s*([A-F])\s*\))?")
OCR_TEXT_THRESHOLD = 30
OCR_DPI = 300


def normalize_marker(num: str, subpart: str | None) -> str:
    return f"(b)({num})" + (f"({subpart})" if subpart else "")


def _page_text(page: fitz.Page) -> tuple[str, bool]:
    text = page.get_text()
    if len(text.strip()) >= OCR_TEXT_THRESHOLD:
        return text, False
    try:
        pix = page.get_pixmap(dpi=OCR_DPI)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        return pytesseract.image_to_string(img), True
    except Exception:
        return text, False


def scan_pdf_bytes(pdf_bytes: bytes) -> dict:
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "total_markers": 0,
                "by_marker": {}, "total_pages": None, "pages_ocred": 0}
    by_marker: Counter[str] = Counter()
    pages_ocred = 0
    try:
        for i in range(doc.page_count):
            text, ocr_used = _page_text(doc.load_page(i))
            if ocr_used:
                pages_ocred += 1
            for m in MARKER_RE.finditer(text):
                by_marker[normalize_marker(m.group(1), m.group(2))] += 1
        total_pages = doc.page_count
    finally:
        doc.close()
    return {"total_pages": total_pages, "total_markers": sum(by_marker.values()),
            "by_marker": dict(by_marker), "pages_ocred": pages_ocred}
