"""HEAD-check every uploaded R2 URL in the ledger; report anything not reachable.

    uv run python scripts/verify_r2_links.py            # check all
    uv run python scripts/verify_r2_links.py --limit 20 # spot-check
"""
from __future__ import annotations

import argparse

import httpx

import _r2


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    ledger = _r2.load_ledger()
    urls = [v["public_url"] for v in ledger["individual"].values()]
    urls += [v["public_url"] for v in ledger["datasets"].values()]
    if args.limit:
        urls = urls[: args.limit]
    if not urls:
        print("ledger has no uploaded URLs yet.")
        return

    ok = bad = 0
    with httpx.Client(timeout=30, follow_redirects=True) as c:
        for u in urls:
            try:
                r = c.head(u)
                if r.status_code == 200:
                    ok += 1
                else:
                    bad += 1
                    print(f"  {r.status_code}  {u}")
            except Exception as e:
                bad += 1
                print(f"  ERR {type(e).__name__}  {u}")
    print(f"\nchecked {len(urls)}: {ok} OK, {bad} problems")


if __name__ == "__main__":
    main()
