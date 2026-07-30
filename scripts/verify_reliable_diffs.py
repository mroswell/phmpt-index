"""Rigorously re-check the redaction differences by scanning BOTH versions
text-layer-only (no OCR), page by page, and trusting a difference ONLY on pages
where both versions have real extractable text. This eliminates the false
positives caused by this release delivering some documents as image scans (which
force OCR, which undercounts) while PHMPT had them as text.

Updates FDA-FOIA-2026-6007/site/redaction_diffs.json in place, adding:
  reliable        : True if >=1 reliable diff page (both text, markers differ)
  reliable_delta  : trove-minus-PHMPT summed over text-comparable pages only
  diff_pages      : pages where both have text AND markers differ (replaces old)
  uncomparable_pages : count of pages skipped because one side is an image
Run:  uv run python scripts/verify_reliable_diffs.py
"""

from __future__ import annotations

import csv
import json
import multiprocessing as mp
import os
import sys
from collections import Counter
from pathlib import Path

import fitz
import zipfile_deflate64 as zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _scan_core import MARKER_RE, normalize_marker
from build_trove_exemptions import canon_markers, k_norm, k_bates, k_exact

ROOT = Path(__file__).resolve().parent.parent
DIFFS = ROOT / "FDA-FOIA-2026-6007" / "site" / "redaction_diffs.json"
DIFF_FILES = ROOT / "FDA-FOIA-2026-6007" / "site" / "diff-files"
INDEX = ROOT / "docs" / "data" / "index.json"
PHMPT_ZIPS = ROOT / "data" / "zips"
TEXT_MIN = 30
WORKERS = 8


def perpage_textonly(pdf_bytes: bytes) -> dict:
    """{page: {'text': bool, 'markers': {..}}} using ONLY the text layer."""
    d = fitz.open(stream=pdf_bytes, filetype="pdf")
    out = {}
    try:
        for i in range(d.page_count):
            t = d.load_page(i).get_text()
            c = Counter()
            for m in MARKER_RE.finditer(t):
                c[normalize_marker(m.group(1), m.group(2))] += 1
            out[i + 1] = {"text": len(t.strip()) >= TEXT_MIN,
                          "markers": canon_markers(dict(c))}
    finally:
        d.close()
    return out


# PHMPT filename -> zip_source (built in worker init via globals)
_PH = {}


def _init(ph):
    global _PH
    _PH = ph


def _worker(fn):
    tpath = DIFF_FILES / fn
    if not tpath.exists():
        return fn, None
    # locate PHMPT copy
    rec = None
    for kf in (k_exact, k_bates, k_norm):
        rec = _PH.get(kf(fn))
        if rec:
            break
    if not rec:
        return fn, None
    zs, pmember = rec
    zp = next((p for p in PHMPT_ZIPS.rglob(zs)), None)
    if not zp:
        return fn, None
    try:
        with zipfile.ZipFile(zp) as z:
            member = next((n for n in z.namelist() if os.path.basename(n) == pmember), None)
            pdata = z.read(member)
        tpp = perpage_textonly(tpath.read_bytes())
        ppp = perpage_textonly(pdata)
    except Exception as e:
        return fn, {"error": str(e)}

    diff_pages, uncomp = [], 0
    tdelta = Counter()
    allp = set(tpp) | set(ppp)
    for p in allp:
        tv = tpp.get(p, {"text": False, "markers": {}})
        pv = ppp.get(p, {"text": False, "markers": {}})
        if not (tv["text"] and pv["text"]):
            if tv["markers"] != pv["markers"]:
                uncomp += 1
            continue
        if tv["markers"] != pv["markers"]:
            diff_pages.append(p)
            for m in set(tv["markers"]) | set(pv["markers"]):
                d = tv["markers"].get(m, 0) - pv["markers"].get(m, 0)
                if d:
                    tdelta[m] += d
    return fn, {"reliable": bool(diff_pages),
                "reliable_delta": {k: v for k, v in tdelta.items() if v},
                "diff_pages": sorted(diff_pages),
                "uncomparable_pages": uncomp}


def main():
    diffs = json.loads(DIFFS.read_text())
    # PHMPT lookup: k_* -> (zip_source, basename)
    ph = {}
    for r in json.loads(INDEX.read_text()):
        fn = r.get("filename", "")
        if fn.lower().endswith(".pdf") and r.get("zip_source"):
            val = (r["zip_source"], os.path.basename(fn))
            ph.setdefault(k_exact(fn), val)
            ph.setdefault(k_bates(fn), val)
            ph.setdefault(k_norm(fn), val)

    names = [d["filename"] for d in diffs]
    res = {}
    with mp.Pool(WORKERS, initializer=_init, initargs=(ph,)) as pool:
        for i, (fn, r) in enumerate(pool.imap_unordered(_worker, names, 4), 1):
            res[fn] = r
            if i % 100 == 0:
                print(f"  {i}/{len(names)}", flush=True)

    kept = 0
    for d in diffs:
        r = res.get(d["filename"])
        if not r or r.get("error"):
            d["reliable"] = None
            continue
        d["reliable"] = r["reliable"]
        d["reliable_delta"] = r["reliable_delta"]
        d["diff_pages"] = r["diff_pages"]
        d["uncomparable_pages"] = r["uncomparable_pages"]
        if r["reliable"]:
            kept += 1
    DIFFS.write_text(json.dumps(diffs))

    rel = [d for d in diffs if d.get("reliable")]
    net = Counter()
    for d in rel:
        net.update(d["reliable_delta"])
    print(f"\nreliable differences (both text, page-verified): {len(rel)} of {len(diffs)}")
    print(f"  net reliable delta (trove-PHMPT): {dict(net.most_common())}")
    from collections import Counter as C
    print(f"  by category: {dict(C(d['category'] for d in rel))}")


if __name__ == "__main__":
    main()
