"""Enrich the redaction-differences dataset with:
  - phmpt_link  : the coviddocuments-style link (phmpt.org for single docs,
                  Google Drive for zip-derived files) — already in doc_link.
  - trove_link  : this-release copy. Site-relative for files <=25 MB (Cloudflare
                  Pages limit); GitHub raw URL for larger files still in the repo.
  - diff_pages  : for same-document re-redactions, the page numbers where the
                  redaction markers differ between the two versions.

Run AFTER extract_diff_files.py.  Output overwrites site/redaction_diffs.json.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_trove_exemptions import canon_markers, k_norm

ROOT = Path(__file__).resolve().parent.parent
DIFFS = ROOT / "FDA-FOIA-2026-6007" / "site" / "redaction_diffs.json"
PERPAGE_CACHE = ROOT / "data" / "cache" / "diff_perpage"
SIZES = ROOT / "data" / "diff_file_sizes.json"
PHMPT_PER = [ROOT / "data" / f"M{i}_exemptions.json" for i in range(1, 6)] + \
            [ROOT / "data" / "individual_phmpt_exemptions.json",
             ROOT / "data" / "individual_exemptions.json"]
RAW_BASE = "https://github.com/DNAIntegrityProject/trial-foia/blob/main/diff-files/"
SIZE_LIMIT = 25 * 10 ** 6


def phmpt_perpage_for(knorms: set) -> dict:
    """k_norm -> {page(str): canon by_marker} for the wanted files only."""
    out: dict[str, dict] = {}
    for path in PHMPT_PER:
        if not path.exists():
            continue
        for r in json.loads(path.read_text())["files"]:
            fn = r.get("filename")
            if not fn or k_norm(fn) not in knorms or k_norm(fn) in out:
                continue
            pages: dict[str, Counter] = {}
            for ep in r.get("exemption_pages", []):
                pages.setdefault(str(ep["page"]), Counter())[ep["marker"]] += ep.get("count", 1)
            out[k_norm(fn)] = {p: canon_markers(dict(c)) for p, c in pages.items()}
    return out


def diff_pages(trove_pp: dict, phmpt_pp: dict) -> list:
    allp = set(trove_pp) | set(phmpt_pp)
    return sorted((int(p) for p in allp
                   if trove_pp.get(p, {}) != phmpt_pp.get(p, {})))


def main():
    diffs = json.loads(DIFFS.read_text())
    sizes = json.loads(SIZES.read_text())["sizes"] if SIZES.exists() else {}
    knorms = {k_norm(d["filename"]) for d in diffs}
    phmpt_pp = phmpt_perpage_for(knorms)

    for d in diffs:
        fn = d["filename"]
        d["phmpt_link"] = d.get("doc_link")
        sz = sizes.get(fn, 0)
        d["trove_size_mb"] = round(sz / 1e6, 1) if sz else None
        enc = quote("diff-files/" + fn)
        d["trove_link"] = enc if sz and sz <= SIZE_LIMIT else RAW_BASE + quote(fn)
        d["trove_oversize"] = bool(sz and sz > SIZE_LIMIT)
        # pages where redactions differ (re-redactions only; versions don't align)
        if d["kind"] == "reredaction":
            cp = PERPAGE_CACHE / f"{hashlib.md5(fn.encode()).hexdigest()}.json"
            tpp = json.loads(cp.read_text()).get("pages", {}) if cp.exists() else {}
            d["diff_pages"] = diff_pages(tpp, phmpt_pp.get(k_norm(fn), {}))
        else:
            d["diff_pages"] = None

    DIFFS.write_text(json.dumps(diffs))
    rr = [d for d in diffs if d["kind"] == "reredaction"]
    withpages = [d for d in rr if d["diff_pages"]]
    print(f"enriched {len(diffs)} diffs")
    print(f"  re-redactions with identified diff pages: {len(withpages)}/{len(rr)}")
    print(f"  oversize (GitHub-linked): {sum(1 for d in diffs if d['trove_oversize'])}")
    print("  sample diff pages:")
    for d in sorted(withpages, key=lambda x: -len(x["diff_pages"]))[:5]:
        print(f"    {len(d['diff_pages'])} pages  {d['filename'][:50]}  e.g. {d['diff_pages'][:8]}")


if __name__ == "__main__":
    main()
