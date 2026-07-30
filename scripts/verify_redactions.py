"""Verify whether FDA-FOIA-2026-6007 (trove) redacted the SAME documents
differently than the PHMPT release.

Insight that makes this tractable: for text-based PDFs (no OCR), the PHMPT
pipeline's recorded marker counts are exactly reproducible (text extraction +
regex is deterministic). So we:

  1. Scan every overlapping trove PDF once (parallel, across cores).
  2. Compare to the PHMPT recorded counts.
  3. A difference where NEITHER side used OCR is a *confirmed* real re-redaction.
  4. A difference where OCR was involved is re-checked by scanning the PHMPT
     version with the identical scanner (fair, same-OCR comparison).

Outputs:
  data/redaction_diffs.json
  FDA-FOIA-2026-6007/redaction_differences_report.md
Resumable: per-file trove scan cache under data/cache/verify_redactions/trove/.
Run:  uv run python scripts/verify_redactions.py
"""

from __future__ import annotations

import csv
import hashlib
import json
import multiprocessing as mp
import os
import sys
from collections import Counter
from pathlib import Path

import zipfile_deflate64 as zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _scan_core import scan_pdf_bytes
from build_trove_exemptions import k_norm, k_bates, k_exact, canon_markers

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "FDA-FOIA-2026-6007" / "site" / "documents.json"
INV = ROOT / "FDA-FOIA-2026-6007" / "file_inventory.csv"
INDEX = ROOT / "docs" / "data" / "index.json"
PHMPT_EX = ROOT / "docs" / "data" / "exemptions.json"
INDIV = [ROOT / "data" / "individual_phmpt_exemptions.json",
         ROOT / "data" / "individual_exemptions.json"]
BUNDLES = ROOT / ".scratch" / "muckrock_bundles"
PHMPT_ZIPS = ROOT / "data" / "zips"
CACHE = ROOT / "data" / "cache" / "verify_redactions"
OUT_JSON = ROOT / "data" / "redaction_diffs.json"
OUT_MD = ROOT / "FDA-FOIA-2026-6007" / "redaction_differences_report.md"
WORKERS = 8


def _zip_index(zpath):
    with zipfile.ZipFile(zpath) as z:
        return {os.path.basename(n): n for n in z.namelist() if not n.endswith("/")}


def _scan_trove(task):
    """Worker: scan one trove member. task=(bundle_path, member, key)."""
    bundle_path, member, key = task
    cp = CACHE / "trove" / f"{hashlib.md5(key.encode()).hexdigest()}.json"
    if cp.exists():
        return key, json.loads(cp.read_text())
    try:
        with zipfile.ZipFile(bundle_path) as z:
            data = z.read(member)
        rec = scan_pdf_bytes(data)
    except Exception as e:
        rec = {"error": str(e), "by_marker": {}, "total_markers": 0,
               "total_pages": None, "pages_ocred": 0}
    rec["by_marker"] = canon_markers(rec.get("by_marker", {}))
    rec["total_markers"] = sum(rec["by_marker"].values())
    cp.write_text(json.dumps(rec))
    return key, rec


def load_phmpt_counts():
    """k_* -> {by_marker(canon), ocr, zip_source, filename}."""
    ex, ba, no = {}, {}, {}

    def add(fn, rec):
        ex.setdefault(k_exact(fn), rec)
        ba.setdefault(k_bates(fn), rec)
        no.setdefault(k_norm(fn), rec)

    idx_zip = {}
    for r in json.loads(INDEX.read_text()):
        if r.get("filename"):
            idx_zip[r["filename"]] = r.get("zip_source")
    for r in json.loads(PHMPT_EX.read_text())["files"]:
        fn = r.get("filename")
        if not fn:
            continue
        add(fn, {"by_marker": canon_markers(r.get("by_marker", {})),
                 "ocr": bool(r.get("ocr_candidate_pages_count")),
                 "zip_source": idx_zip.get(fn), "filename": fn})
    for path in INDIV:
        if path.exists():
            for r in json.loads(path.read_text())["files"]:
                bm = Counter()
                for ep in r.get("exemption_pages", []):
                    bm[ep["marker"]] += ep.get("count", 1)
                add(r["filename"], {"by_marker": canon_markers(dict(bm)),
                    "ocr": bool(r.get("ocr_candidate_pages")),
                    "zip_source": idx_zip.get(r["filename"]), "filename": r["filename"]})
    return ex, ba, no


def phmpt_rescan(rec):
    """Fresh same-scanner scan of the PHMPT version (for OCR-involved diffs)."""
    zs = rec.get("zip_source")
    if not zs:
        return None
    zp = next((p for p in PHMPT_ZIPS.rglob(zs)), None)
    if not zp:
        return None
    try:
        members = _zip_index(zp)
        m = members.get(os.path.basename(rec["filename"]))
        with zipfile.ZipFile(zp) as z:
            data = z.read(m)
        r = scan_pdf_bytes(data)
        return canon_markers(r.get("by_marker", {}))
    except Exception:
        return None


def main():
    (CACHE / "trove").mkdir(parents=True, exist_ok=True)
    docs = json.loads(DOCS.read_text())
    overlap = [d for d in docs if d.get("status") == "PHMPT"
               and (d.get("ext") or "").lower() == "pdf"]
    inv = {r["filename"]: r["bundle"] for r in csv.DictReader(open(INV))}
    ex, ba, no = load_phmpt_counts()

    # member indices per bundle (once)
    bundle_idx = {}
    for name in set(inv.values()):
        bz = BUNDLES / name
        if bz.exists():
            bundle_idx.setdefault(name, _zip_index(bz))

    tasks, meta = [], {}
    no_bundle = no_phmpt = 0
    for d in overlap:
        fn = d["filename"]
        b = inv.get(fn)
        if not b or b not in bundle_idx or fn not in bundle_idx[b]:
            no_bundle += 1
            continue
        prec = None
        for keyfn, dd in ((k_exact, ex), (k_bates, ba), (k_norm, no)):
            prec = dd.get(keyfn(fn))
            if prec:
                break
        if not prec:
            no_phmpt += 1
            continue
        tasks.append((str(BUNDLES / b), bundle_idx[b][fn], fn))
        meta[fn] = {"doc": d, "phmpt": prec}

    print(f"{len(overlap)} overlapping PDFs · {len(tasks)} scannable · "
          f"{no_bundle} not downloaded · {no_phmpt} no PHMPT match", flush=True)

    trove = {}
    with mp.Pool(WORKERS) as pool:
        for i, (key, rec) in enumerate(pool.imap_unordered(_scan_trove, tasks, 4), 1):
            trove[key] = rec
            if i % 200 == 0:
                print(f"  scanned {i}/{len(tasks)}", flush=True)

    # compare
    diffs, confirmed, ocr_checked = [], 0, 0
    for fn, m in meta.items():
        tm = trove[fn]["by_marker"]
        prec = m["phmpt"]
        pm = prec["by_marker"]
        if tm == pm:
            continue
        ocr_involved = bool(trove[fn].get("pages_ocred")) or prec.get("ocr")
        status = "confirmed"
        if ocr_involved:
            fresh = phmpt_rescan(prec)
            ocr_checked += 1
            if fresh is not None:
                pm = fresh
                if tm == pm:
                    continue  # was OCR noise, not a real diff
                status = "confirmed-ocr-rescan"
            else:
                status = "unconfirmed-ocr"
        delta = {mk: tm.get(mk, 0) - pm.get(mk, 0)
                 for mk in set(tm) | set(pm) if tm.get(mk, 0) != pm.get(mk, 0)}
        if not delta:
            continue
        if status.startswith("confirmed"):
            confirmed += 1
        diffs.append({"filename": fn, "category": m["doc"].get("category"),
                      "module": m["doc"].get("module"), "bimo": bool(m["doc"].get("bimo")),
                      "trove": tm, "phmpt": pm, "delta": delta, "status": status,
                      "trove_pages": trove[fn].get("total_pages")})

    _write(diffs, len(tasks), no_bundle, confirmed, ocr_checked, len(overlap))


def _write(diffs, scanned, no_bundle, confirmed, ocr_checked, overlap_total):
    net = Counter()
    more_t = more_p = 0
    for r in diffs:
        for mk, dv in r["delta"].items():
            net[mk] += dv
        s = sum(r["delta"].values())
        more_t += s > 0
        more_p += s < 0
    summary = {"overlap_pdfs": overlap_total, "scanned": scanned,
               "not_downloaded": no_bundle, "documents_that_differ": len(diffs),
               "confirmed_real": confirmed, "ocr_rechecked": ocr_checked,
               "trove_redacts_more": more_t, "phmpt_redacts_more": more_p,
               "net_marker_delta_trove_minus_phmpt": dict(net.most_common())}
    OUT_JSON.write_text(json.dumps({"summary": summary, "diffs": diffs}, indent=1))

    L = ["# FDA-FOIA-2026-6007 vs PHMPT — Redaction Differences", "",
         "Same document, same scanner. A difference means the two FOIA releases",
         "redacted the identical document differently. Text-based docs are compared",
         "against the PHMPT pipeline's counts (deterministic); OCR-involved diffs are",
         "re-scanned on the PHMPT side to rule out OCR noise.", "",
         f"- Overlapping PDFs scanned: **{scanned}** of {overlap_total}",
         f"- Documents whose redactions differ: **{len(diffs)}**",
         f"- Trove redacts MORE: {summary['trove_redacts_more']} · "
         f"PHMPT more: {summary['phmpt_redacts_more']}",
         f"- Net marker delta (trove − PHMPT): {summary['net_marker_delta_trove_minus_phmpt']}",
         f"- Not yet downloaded: {no_bundle}", "",
         "| Document | Module | trove | PHMPT | Δ (trove−PHMPT) |",
         "| --- | --- | --- | --- | --- |"]
    for r in sorted(diffs, key=lambda x: -abs(sum(x["delta"].values())))[:500]:
        L.append(f"| `{r['filename'][:58]}` | {r['module']} | {r['trove']} | "
                 f"{r['phmpt']} | {r['delta']} |")
    OUT_MD.write_text("\n".join(L) + "\n")
    print(f"\nwrote {OUT_JSON}\n  differ {len(diffs)} (confirmed {confirmed}, "
          f"ocr-rechecked {ocr_checked}) of {scanned} scanned", flush=True)


if __name__ == "__main__":
    main()
