import json, csv
BT = chr(96)  # backtick
rows = json.load(open('FDA-FOIA-2026-6007/site/redaction_diffs.json'))


def mag(d): return sum(d.values())
def ms(d): return "; ".join(f"{k} {v}" for k, v in sorted(d.items(), key=lambda x: -x[1]))
def ds(d): return "; ".join(f"{k} {'+' if v > 0 else ''}{v}" for k, v in sorted(d.items(), key=lambda x: -abs(x[1])))


rows.sort(key=lambda r: (r['kind'] != 'reredaction', -abs(mag(r['delta']))))

with open('FDA-FOIA-2026-6007/redaction_differences.csv', 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['kind', 'filename', 'category', 'module', 'delta_trove_minus_phmpt', 'net',
                'this_release_markers', 'phmpt_markers', 'trove_pages', 'phmpt_pages', 'doc_link'])
    for r in rows:
        w.writerow([r['kind'], r['filename'], r['category'], r['module'], ds(r['delta']), mag(r['delta']),
                    ms(r['trove']), ms(r['phmpt']), r.get('trove_pages'), r.get('phmpt_pages'), r.get('doc_link')])

rr = [r for r in rows if r['kind'] == 'reredaction']
ver = [r for r in rows if r['kind'] == 'version']
L = ["# FDA-FOIA-2026-6007 vs PHMPT — the 573 documents redacted differently", "",
     f"**{len(rr)} same-document re-redactions** (identical page count) and "
     f"**{len(ver)} re-issued / different-length versions**.",
     "Delta = this release minus PHMPT; positive = redacted MORE here.", "",
     f"## Same-document re-redactions ({len(rr)})", "",
     "| # | Document | Cat | Mod | Delta (trove-PHMPT) | pages |", "|---:|---|---|---|---|---|"]
for i, r in enumerate(rr, 1):
    L.append(f"| {i} | {BT}{r['filename']}{BT} | {r['category']} | {r['module']} | {ds(r['delta'])} | {r.get('trove_pages')} |")
L += ["", f"## Re-issued / different-length versions ({len(ver)})", "",
      "| # | Document | Cat | Mod | Delta | trove pg / phmpt pg |", "|---:|---|---|---|---|---|"]
for i, r in enumerate(ver, 1):
    L.append(f"| {i} | {BT}{r['filename']}{BT} | {r['category']} | {r['module']} | {ds(r['delta'])} | {r.get('trove_pages')}/{r.get('phmpt_pages')} |")
open('FDA-FOIA-2026-6007/redaction_differences.md', 'w').write("\n".join(L) + "\n")

print(f"wrote redaction_differences.csv and .md  ({len(rr)} re-redactions, {len(ver)} versions)")
print("\n=== first 30 same-document re-redactions ===")
for i, r in enumerate(rr[:30], 1):
    print(f"{i:3}. {ds(r['delta']):<26} {r['filename'][:62]}")
