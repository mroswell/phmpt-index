"""Phase 2 OCR pilot: for the worst-scoring documents, compare the current
embedded text against a fresh 300-DPI Tesseract re-OCR, and report whether
re-OCR actually fixes the word-splitting — and whether the page is a scanned
image (re-OCR helps) or born-digital (bad text layer; re-OCR may not help).

    uv run python scripts/ocr_pilot.py                # default targets
    uv run python scripts/ocr_pilot.py "file1.pdf" "file2.pdf"
"""
from __future__ import annotations

import io
import json
import re
import ssl
import sys
import urllib.request
from pathlib import Path

import fitz  # PyMuPDF
import pytesseract
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
IDX = ROOT / "docs" / "data" / "index.json"
OUT = ROOT / "data" / "cache" / "ocr_pilot"
OUT.mkdir(parents=True, exist_ok=True)
TOKEN = re.compile(r"[A-Za-z]+")

DEFAULT_TARGETS = [
    "30_BLA 125752-0_10-29-2021_Telecon_Other.pdf",
    "27034_S2_M5_CRF_c4591001-1231-12311579.pdf",
    "78G_BLA 125752-0_01-11-2022_Memo_Committee Memo_Review.pdf",
]


def load_dict() -> set[str]:
    for p in ("/usr/share/dict/words", "/usr/dict/words"):
        fp = Path(p)
        if fp.exists():
            return {w.strip().lower() for w in fp.read_text(errors="ignore").splitlines()
                    if len(w.strip()) >= 3}
    return set()


DICT = load_dict()


def garble(text: str):
    lc = [t.lower() for t in TOKEN.findall(text or "")]
    words = sum(1 for t in lc if len(t) >= 3)
    m = 0
    for i in range(len(lc) - 1):
        a, b = lc[i], lc[i + 1]
        if len(a) >= 2 and len(b) <= 4 and len(a) + len(b) >= 5 and (a + b) in DICT and not (a in DICT and b in DICT):
            m += 1
    return (m / words if words else 0.0), words


def main() -> None:
    rows = json.load(open(IDX))
    rows = rows if isinstance(rows, list) else next(v for v in rows.values() if isinstance(v, list))
    byname = {r.get("filename"): r for r in rows}
    targets = sys.argv[1:] or DEFAULT_TARGETS
    ctx = ssl._create_unverified_context()

    for fn in targets:
        r = byname.get(fn) or {}
        url = r.get("hosted_url") or r.get("individual_url")
        dest = OUT / fn
        if not dest.exists():
            if not url:
                print(f"\n### {fn[:60]} — no URL, skipping"); continue
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "ocr-pilot"})
                dest.write_bytes(urllib.request.urlopen(req, context=ctx, timeout=180).read())
            except Exception as e:
                print(f"\n### {fn[:60]} — download failed: {e}"); continue
        doc = fitz.open(dest)
        scored = []
        for i, page in enumerate(doc):
            emb = page.get_text("text")
            g, w = garble(emb)
            if w >= 25:
                scored.append((g, i))
        scored.sort(reverse=True)
        print(f"\n### {fn[:60]}  ({doc.page_count} pp; {dest.stat().st_size//1024} KB)")
        if not scored:
            print("  no text pages with >=25 words"); continue
        for g, i in scored[:2]:
            page = doc[i]
            emb = page.get_text("text")
            imgs = page.get_images(full=True)
            # image coverage: area of largest image vs page area (scanned ≈ 1 big image)
            kind = "born-digital"
            try:
                pa = page.rect.width * page.rect.height
                for im in imgs:
                    for rc in page.get_image_rects(im[0]):
                        if (rc.width * rc.height) / pa > 0.6:
                            kind = "scanned-image"; break
            except Exception:
                pass
            pix = page.get_pixmap(dpi=300)
            ocr = pytesseract.image_to_string(Image.open(io.BytesIO(pix.tobytes("png"))))
            go, _ = garble(ocr)
            print(f"  p{i+1}: {kind} | embedded garble {g:.3f} -> re-OCR garble {go:.3f}")
            print(f"     embedded: {' '.join(emb.split())[:150]}")
            print(f"     re-OCR  : {' '.join(ocr.split())[:150]}")


if __name__ == "__main__":
    main()
