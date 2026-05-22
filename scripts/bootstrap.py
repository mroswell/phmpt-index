"""One-time interactive Playwright session.

Opens a real Chromium window with a persistent profile. In the browser:
solve any Cloudflare/Kasada challenge, then set the per-page dropdown at
the bottom of the listing to 100. When done, CLOSE the browser window —
the script saves the profile and exits automatically. No terminal input
required (works fine under `!` in Claude Code).
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / ".profile"
TARGET = "https://phmpt.org/multiple-file-downloads/"


def main() -> None:
    PROFILE.mkdir(exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            headless=False,
            viewport={"width": 1400, "height": 1000},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(TARGET, wait_until="domcontentloaded")
        print()
        print("Browser open at:", TARGET)
        print()
        print("In the browser:")
        print("  1. Wait for the Cloudflare challenge to clear (if any).")
        print("  2. Scroll to the bottom; set the per-page dropdown to 100.")
        print("  3. CLOSE the browser window when done.")
        print()
        print("(script saves and exits automatically on window close)")
        try:
            page.wait_for_event("close", timeout=0)
        except Exception:
            pass
        try:
            ctx.close()
        except Exception:
            pass
    print()
    print("Saved persistent profile to:", PROFILE)


if __name__ == "__main__":
    main()
