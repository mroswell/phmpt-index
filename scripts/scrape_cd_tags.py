"""Scrape + normalize the tag vocabulary from the companion site coviddocuments.com.

The three per-navigator data files that feed `#tagSearchInput` each hold one
record per file, with a `tags` array of descriptive phrases. We union every
record's tags across all three, normalize to collapse case/format variants,
and emit a canonical tag list for the FOIA site's tags browse/search page.

    uv run python scripts/scrape_cd_tags.py            # fetch live + write outputs
    uv run python scripts/scrape_cd_tags.py --offline  # reuse data/external copies

Outputs:
    data/external/cd-tagged-files/<name>.json   raw pulled files (for reuse)
    docs/data/tags.json                          canonical tags for the page
"""
from __future__ import annotations

import argparse
import json
import re
import ssl
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "external" / "cd-tagged-files"
OUT = ROOT / "docs" / "data" / "tags.json"

SOURCES = {
    "pd-bla": "https://coviddocuments.com/pd-bla-tagged-files.json",
    "pfizer-eua": "https://coviddocuments.com/pfizer-eua-tagged-files.json",
    "moderna": "https://coviddocuments.com/moderna-tagged-files.json",
}

# Keep only tags that appear on at least this many distinct files — drops
# one-off OCR garbage ("Seendy") and place names, keeps recurring topics.
MIN_DF = 2

# A tag is "code-like" (a dataset/domain code rather than a descriptive phrase)
# if it's a short all-caps/alnum token. Rare after curation; just flagged.
CODE_RE = re.compile(r"^[A-Z][A-Z0-9]{1,}$")
# Identifiers whose hyphens are meaningful (COVID-19, mRNA-1273, SARS-CoV-2):
# any token containing a digit or an interior capital. Don't split those.
HAS_DIGIT_OR_CAP = re.compile(r"[0-9]|(?<=[a-z])[A-Z]")


def descriptive(tag: str) -> bool:
    """Reject non-descriptive tags: numeric/bates IDs, %-metrics, underscore
    code identifiers, ALL-CAPS codes, short vowel-less codes."""
    t = tag.strip()
    if re.match(r"^[\W\d%]", t):          # starts with digit/punct/%
        return False
    if len(t) < 3 or "_" in t:
        return False
    if not re.search(r"[a-z]", t):        # no lowercase => ALL-CAPS code
        return False
    if re.fullmatch(r"[A-Za-z]{1,6}", t) and not re.search(r"[aeiou]", t.lower()):
        return False
    return True


def _keep_token(tok: str) -> bool:
    """Keep a token's original casing if it's an acronym or identifier
    (FDA, CRF, COVID-19, mRNA, pH, SARS-CoV, McKesson); otherwise it'll be
    lowercased. Can't perfectly detect multi-word proper names."""
    core = tok.strip("()[]{}.,;:\"'")
    if not core:
        return False
    if any(ch.isdigit() for ch in core):
        return True
    letters = [ch for ch in core if ch.isalpha()]
    if len(letters) >= 2 and all(ch.isupper() for ch in letters):
        return True                       # FDA, CRF, RNA
    if any(ch.isupper() for ch in core[1:]):
        return True                       # mRNA, pH, SARS-CoV, McKesson
    return False


def sentence_case(s: str) -> str:
    """First letter capitalized, rest lowercase — except acronyms/identifiers,
    which keep their original casing."""
    toks = [t if _keep_token(t) else t.lower() for t in s.split()]
    out = " ".join(toks)
    m = re.search(r"[A-Za-z]", out)
    if m:
        i = m.start()
        out = out[:i] + out[i].upper() + out[i + 1:]
    return out


def canonical(tag: str) -> str:
    """Lowercase + collapse whitespace; turn hyphen-joined *word* phrases
    (purely alphabetic, e.g. 'vaccine-efficacy') into spaces, but preserve
    identifier hyphens (covid-19, mrna-1273)."""
    t = tag.strip()
    if "-" in t and not HAS_DIGIT_OR_CAP.search(t):
        t = t.replace("-", " ")
    t = re.sub(r"\s+", " ", t)
    return t.casefold().strip()


def fetch(url: str) -> list:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": "foia-tag-scraper"})
    with urllib.request.urlopen(req, context=ctx, timeout=60) as r:
        data = json.loads(r.read())
    # files are a dict; the records live in the first list-valued entry
    if isinstance(data, list):
        return data
    return next(v for v in data.values() if isinstance(v, list))


def load(offline: bool) -> dict[str, list]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, url in SOURCES.items():
        cache = RAW_DIR / f"{name}.json"
        if offline and cache.exists():
            out[name] = json.loads(cache.read_text())
        else:
            recs = fetch(url)
            cache.write_text(json.dumps(recs))
            out[name] = recs
        print(f"  {name}: {len(out[name]):,} records")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="reuse data/external copies")
    args = ap.parse_args()

    sources = load(args.offline)

    # Use ONLY each record's `tags` field (the site's tag dropdown) — never the
    # separate documentType/module/fileType/vaccineCandidate/clinicalTrial fields.
    docfreq: Counter[str] = Counter()           # canonical -> # distinct files
    surfaces: dict[str, Counter] = defaultdict(Counter)  # canonical -> surface forms
    for recs in sources.values():
        for rec in recs:
            tags = rec.get("tags") or []
            if isinstance(tags, str):
                tags = [p.strip() for p in tags.split(",") if p.strip()]
            seen = set()
            for raw in tags:
                raw = (raw or "").strip()
                if not raw or not descriptive(raw):
                    continue
                key = canonical(raw)
                if not key:
                    continue
                surfaces[key][raw] += 1
                seen.add(key)
            for key in seen:                    # document frequency = distinct files
                docfreq[key] += 1

    tags = []
    for key, df in docfreq.items():
        if df < MIN_DF:
            continue
        # display = most frequent original surface form for this canonical tag;
        # de-hyphenate word-phrases ('acceptance-criteria' -> 'acceptance criteria')
        # but keep identifier hyphens (COVID-19, mRNA-1273).
        display = re.sub(r"\s+", " ", surfaces[key].most_common(1)[0][0].strip())
        if "-" in display and not HAS_DIGIT_OR_CAP.search(display):
            display = display.replace("-", " ")
        display = sentence_case(display)
        search = f'"{key}"' if len(key.split()) > 1 else key
        letter = key[0].upper() if key[:1].isalpha() else "#"
        tags.append({
            "name": display,
            "search": search,
            "files": df,
            "letter": letter,
            "code": bool(CODE_RE.match(display)),
        })

    tags.sort(key=lambda t: (t["letter"], t["name"].casefold()))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": "coviddocuments.com tag dropdown (tags field) across 3 navigators, union",
        "min_files": MIN_DF,
        "total_tags": len(tags),
        "code_tags": sum(1 for t in tags if t["code"]),
        "tags": tags,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    print(f"\ncanonical tags: {len(tags):,}  (code-like: {payload['code_tags']})")
    print(f"wrote {OUT.relative_to(ROOT)}")
    print("sample:", [t["name"] for t in tags[:12]])


if __name__ == "__main__":
    main()
