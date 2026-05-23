#!/bin/bash
# Wait until phmpt.org is responsive (Cloudflare block lifted),
# then run the rate-limited PDF page-count extractor.
#
# Polls phmpt.org/ every 6 minutes via Playwright. When we get HTTP 200,
# the until-loop exits and we invoke the extractor (which has its own
# circuit-breaker if Cloudflare flags us mid-run).

set -e
cd "$(dirname "$0")/.."

echo "Started polling at $(date)"
attempt=0

until uv run python -c "
from playwright.sync_api import sync_playwright
import sys
try:
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(user_data_dir='.profile', headless=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        r = page.goto('https://phmpt.org/', timeout=20000)
        status = r.status if r else 0
        ctx.close()
        print(f'status={status}')
        sys.exit(0 if status == 200 else 1)
except Exception as e:
    print(f'error: {e}')
    sys.exit(1)
"; do
  attempt=$((attempt + 1))
  echo "attempt $attempt: still blocked at $(date), waiting 6 min..."
  sleep 360
done

echo ""
echo "✅ phmpt.org responding at $(date) — starting extraction"
echo ""
PYTHONUNBUFFERED=1 uv run python scripts/extract_individual_pdf_pages.py
