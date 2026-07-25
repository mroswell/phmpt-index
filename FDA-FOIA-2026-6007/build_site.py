"""Build a static website for the FDA-FOIA-2026-6007 release.

For every document in the release (excluding regulatory correspondence), emit
a row with the best available link:
  - NEW  -> the MuckRock bundle zip that contains it (only public source)
  - PHMPT-> the phmpt.org individual document URL (+ zip as backup)
  - Google Drive link from the coviddocuments.com navigators (Pfizer BLA /
    Pfizer EUA / Moderna BLA), matched by filename.

Inputs (relative to this dir):
  .members_full.json         - all 7,417 release members (bundle, filename, size)
  ../docs/data/index.json    - PHMPT corpus (individual_url, zip_url, size)
  ../.scratch/pbla.json      - Pfizer BLA navigator (filename -> googleDriveLink)
  ../.scratch/peua.json      - Pfizer EUA navigator
  ../.scratch/mbla.json      - Moderna BLA navigator

Outputs:  site/documents.json  and  site/index.html
"""
import json, re, os
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
os.makedirs("site", exist_ok=True)

# ---------- normalization helpers ----------
BATES = re.compile(r'^(?:~\$)?(?:bates-)?fda-cber-\d+-\d+-\s*\d+\s*[-–—]?\s*\d*[ _to–-]*', re.I)
SUBM  = re.compile(r'^0*\d+(?:-\d+)?_s0*\d+_m[0-9.]+[._-]', re.I)
def base(fn): return os.path.basename(fn)
def k_exact(fn): return base(fn).lower()
def k_bates(fn): return BATES.sub('', base(fn)).lower().strip()
def k_norm(fn):
    s = k_bates(fn)
    s = SUBM.sub('', s)
    s = re.sub(r'\s*\(\d+\)', '', s)             # " (1)" suffixes
    s = re.sub(r'\b0+(\d)', r'\1', s)            # leading zeros
    s = re.sub(r'\.(pdf|xpt|txt|docx?|xlsx?|xml|xsl|jmp|png|jpg)$', '', s)
    s = re.sub(r'[ _\-]', '', s)
    return s
DS = re.compile(r'[-_]([a-z0-9]+)(?:_reissue)?\.xpt$', re.I)
def dstoken(fn):
    m = DS.search(fn); return m.group(1).lower() if m else None

# ---------- end-truncation matching ----------
# The PHMPT index truncated some filenames at 74 chars, dropping the tail of the
# name (e.g. "..._A_D_admh.xpt" was stored as "..._A_D_ad.xpt"). Normalization
# above can't restore dropped characters, so match on the (unique) Bates range +
# extension + a prefix relationship between the two stems instead.
BATES_KEY = re.compile(r'^(?:~\$)?(?:bates-)?(fda-cber-\d+-\d+-\s*\d+\s*[-–—]\s*\d+)', re.I)
def bates_key(fn):
    m = BATES_KEY.match(base(fn))
    if not m: return None
    return re.sub(r'\s+', '', m.group(1)).replace('–', '-').replace('—', '-').lower()
def squash(fn):  # lowercased basename, spaces removed, dashes unified
    return re.sub(r'\s+', '', base(fn).lower()).replace('–', '-').replace('—', '-')
def stem_ext_sq(fn):
    s = squash(fn); root, ext = os.path.splitext(s); return root, ext

# ---------- load PHMPT corpus ----------
idx = json.load(open("../docs/data/index.json"))
ph_exact, ph_bates, ph_norm = {}, {}, {}
ph_size_ds = {}   # (dataset_token, size) -> rec  (for re-filed xpt)
ph_trunc = {}     # bates_key -> list of (stem, ext, rec)  (for suffix-truncated names)
for it in idx:
    rec = {"individual_url": it.get("individual_url"), "zip_url": it.get("zip_url"),
           "filename": it["filename"]}
    ph_exact.setdefault(k_exact(it["filename"]), rec)
    ph_bates.setdefault(k_bates(it["filename"]), rec)
    ph_norm.setdefault(k_norm(it["filename"]), rec)
    if it["filename"].lower().endswith(".xpt") and it.get("size"):
        dt = dstoken(it["filename"])
        if dt: ph_size_ds.setdefault((dt, it["size"]), rec)
    bk = bates_key(it["filename"])
    if bk:
        st, ex = stem_ext_sq(it["filename"])
        ph_trunc.setdefault(bk, []).append((st, ex, rec))

def phmpt_lookup(fn, size):
    for keyfn, d in ((k_exact, ph_exact), (k_bates, ph_bates), (k_norm, ph_norm)):
        r = d.get(keyfn(fn))
        if r: return r
    if fn.lower().endswith(".xpt"):
        r = ph_size_ds.get((dstoken(fn), size))
        if r: return r
    # end-truncation: same Bates range + extension, one stem a prefix of the other
    bk = bates_key(fn)
    if bk and bk in ph_trunc:
        st, ex = stem_ext_sq(fn)
        for (pst, pex, r) in ph_trunc[bk]:
            if pex == ex and st != pst and (pst.startswith(st) or st.startswith(pst)):
                return r
    return None

# ---------- load navigators (google drive) ----------
NAVS = [("Pfizer BLA", "../.scratch/pbla.json"),
        ("Pfizer EUA", "../.scratch/peua.json"),
        ("Moderna BLA", "../.scratch/mbla.json")]
gd_exact, gd_bates, gd_norm = {}, {}, {}
def as_list(obj):
    if isinstance(obj, list): return obj
    return next(v for v in obj.values() if isinstance(v, list))
for navname, path in NAVS:
    for d in as_list(json.load(open(path))):
        if not isinstance(d, dict): continue
        fn = d.get("filename") or ""
        link = d.get("googleDriveLink")
        if not fn or not link: continue
        val = {"link": link, "nav": navname,
               "module": d.get("module"), "documentType": d.get("documentType"),
               "clinicalTrial": d.get("clinicalTrial")}
        gd_exact.setdefault(k_exact(fn), val)
        gd_bates.setdefault(k_bates(fn), val)
        gd_norm.setdefault(k_norm(fn), val)
def gdrive_lookup(fn):
    for keyfn, d in ((k_exact, gd_exact), (k_bates, gd_bates), (k_norm, gd_norm)):
        v = d.get(keyfn(fn))
        if v: return v
    return None

# ---------- BIMO (Bioresearch Monitoring) record detection ----------
# Actual inspection records, not the trial data the inspections examined.
def is_bimo(fn):
    f = fn.lower()
    if "bimo" in f:                                   return True   # sponsor BIMO submission (5.3.5.4)
    if "inspection related" in f:                     return True   # FDA inspection reports / EIRs
    if "483-response" in f or "483_response" in f:    return True   # Form FDA 483 response
    if re.search(r"compliance check|request for complianc", f):  return True  # post-inspection memos
    if "inspectn" in f or "preliminary inspection summary" in f: return True  # named inspection memos
    return False

# ---------- category from filename ----------
def category(fn):
    f = fn.lower()
    if f.startswith("125742") or "_125742" in f: return "Pfizer BLA"
    if f.startswith("27034") or "_27034" in f:    return "Pfizer EUA"
    if f.startswith(("019736", "19736")) or "_19736" in f: return "Pfizer IND/EUA"
    if f.startswith("125752") or "_125752" in f:  return "Moderna BLA"
    if f.startswith("19745") or "_19745" in f:    return "Moderna EUA"
    return "Other"

# ---------- exclusion: only the 3 original administrative FOIA files ----------
# (the acknowledgement, partial-response letter, and the MuckRock file listing).
# These are not ZIP members, but exclude by name defensively.
ADMIN_FILES = {
    "report_acknowledgement__fda-foia-2026-6007_jun_23_2026__4.pdf",
    "fda-foia-2026-6007-cber-1_partial_response_letter.pdf",
    "image003_onsiayh.png",
}
def is_correspondence(bundle, fn):
    if fn.lower() in ADMIN_FILES: return True
    if fn.lower().startswith("files for freedom of information act request"): return True
    return False

# ---------- build rows ----------
members = json.load(open(".members_full.json"))
hosted = json.load(open("site/hosted_map.json")) if os.path.exists("site/hosted_map.json") else {}
rows = []
excluded = 0
for m in members:
    fn, bundle, size = m["filename"], m["bundle"], m["size"]
    if is_correspondence(bundle, fn):
        excluded += 1
        continue
    ph = phmpt_lookup(fn, size)
    gd = gdrive_lookup(fn)
    status = "PHMPT" if ph else "NEW"
    gdrive_link = (gd or {}).get("link") or ""
    if ph:
        doc_link = ph["individual_url"] or ph["zip_url"]
        zip_link = ph["zip_url"]
    else:
        # new: prefer our self-hosted individual copy; fall back to the bundle
        doc_link = hosted.get(fn) or m["muckrock_zip"]
        zip_link = m["muckrock_zip"]

    # Resolve the PRIMARY link the filename points to, and its source.
    #   new    -> our self-hosted copy in files/
    #   phmpt  -> the individual phmpt.org page
    #   gdrive -> Google Drive (used when an existing file has no individual PHMPT link)
    is_zip = doc_link.lower().endswith(".zip")
    if status == "NEW":
        source, primary = "new", doc_link
    elif not is_zip:
        source, primary = "phmpt", doc_link
    elif gdrive_link:
        source, primary = "gdrive", gdrive_link
    else:
        source, primary = "phmpt", doc_link   # zip-only, no Drive: fall back to ZIP

    rows.append({
        "filename": fn,
        "ext": (os.path.splitext(fn)[1].lower().lstrip(".") or "(none)"),
        "bimo": is_bimo(fn),
        "category": category(fn),
        "module": (gd or {}).get("module") or "",
        "type": (gd or {}).get("documentType") or "",
        "size_mb": round(size / 1e6, 2),
        "status": status,
        "source": source,
        "primary": primary,
        "doc_link": doc_link,
        "zip_link": zip_link,
        "gdrive": gdrive_link,
        "gdrive_nav": (gd or {}).get("nav") or "",
        "bundle": bundle,
    })

rows.sort(key=lambda r: (r["category"], r["filename"].lower()))
json.dump(rows, open("site/documents.json", "w"))

print(f"members total : {len(members)}")
print(f"correspondence excluded: {excluded}")
print(f"documents in site: {len(rows)}")
print("  status :", dict(Counter(r['status'] for r in rows)))
print("  category:", dict(Counter(r['category'] for r in rows)))
print("  with gdrive:", sum(1 for r in rows if r['gdrive']))
