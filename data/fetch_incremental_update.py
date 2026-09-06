"""Standalone fetch script - drives the already-open debug Chrome, then rebuilds everything.

Preconditions (one-time / once-per-reboot setup):
  1. Run launch_chrome_debug.ps1 to open a dedicated Chrome window with remote
     debugging enabled.
  2. In that window, make sure https://www.aoe2insights.com/ loads cleanly (solve the
     Cloudflare CAPTCHA manually if one appears - this only needs doing again if the site
     re-challenges you later).
  3. Leave that Chrome window open.

Then just run:
    python fetch_incremental_update.py

What it does:
  - Connects to the already-open Chrome via the DevTools protocol (localhost:9222).
  - Errors out clearly (does NOT try to launch or fix anything) if Chrome isn't running
    with debugging enabled, if no aoe2insights.com tab is open, or if that tab is stuck on
    a Cloudflare challenge.
  - Refreshes the known-match-id list first, so discovery stops as soon as it reaches
    matches already on disk instead of re-analyzing them against a throttled endpoint.
  - If everything checks out: runs the full incremental update pipeline (profile staleness
    check + update, new-match discovery, analyze + fetch performance data) directly against
    that live tab.
  - Saves the result and automatically chains into run_full_refresh.py, so the database,
    ladder, per-map Elo, open/closed Elo, and graphs are all refreshed in the same run.
"""
import json
import subprocess
import sys

from playwright.sync_api import sync_playwright

import ladder_common as lc

CDP_URL = "http://localhost:9222"
INCREMENTAL_JS_PATH = lc.DATA_DIR / "incremental_update.js"
REFRESH_SCRIPT_PATH = lc.DATA_DIR / "run_full_refresh.py"


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def main():
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            die(
                f"Could not connect to Chrome at {CDP_URL}.\n"
                f"Is it running with remote debugging enabled? Run launch_chrome_debug.ps1 first.\n"
                f"Underlying error: {e}"
            )

        target_page = None
        for context in browser.contexts:
            for page in context.pages:
                if "aoe2insights.com" in page.url:
                    target_page = page
                    break
            if target_page:
                break

        if target_page is None:
            die(
                "Chrome is running, but no tab has aoe2insights.com open.\n"
                "Navigate to https://www.aoe2insights.com/ in the debug Chrome window first."
            )

        title = target_page.title()
        if "just a moment" in title.lower():
            die(
                "The aoe2insights.com tab is stuck on a Cloudflare challenge page.\n"
                "Solve it manually in the Chrome window, then re-run this script."
            )

        try:
            status = target_page.evaluate(
                "() => fetch('/', {credentials:'same-origin'}).then(r => r.status)"
            )
        except Exception as e:
            die(f"Failed to run a basic check against the open tab: {e}")

        if status != 200:
            die(
                f"aoe2insights.com returned status {status} for a basic request - "
                f"the session may be blocked or a CAPTCHA is unsolved. Check the Chrome window."
            )

        print(f"Connected OK. Tab: {target_page.url}")

        with open(INCREMENTAL_JS_PATH, encoding="utf-8") as f:
            pipeline_js = f.read()
        target_page.evaluate(pipeline_js)

        # Rebuilt from the raw stores every run: a stale list makes __discoverNewMatches
        # page past matches it already has and re-hit the throttled /analyze/ endpoint.
        known_ids = lc.write_known_ids()

        print(f"Running incremental update against {len(known_ids)} known matches...")
        result = target_page.evaluate("(ids) => window.__runIncrementalUpdate(ids)", known_ids)
        print(json.dumps(result, indent=2))

        new_raw_matches = target_page.evaluate("() => window.__newRawMatches")
        perf_data = target_page.evaluate("() => window.__perfData")
        perf_status = target_page.evaluate("() => window.__perfStatus")

        lc.write_json(
            lc.INCREMENTAL_EXPORT_PATH,
            {"newRawMatches": new_raw_matches, "perfData": perf_data, "perfStatus": perf_status},
        )
        print(f"Saved export to {lc.INCREMENTAL_EXPORT_PATH}")

    print("\nChaining into run_full_refresh.py (merge + rebuild everything)...")
    subprocess.run(
        [sys.executable, str(REFRESH_SCRIPT_PATH), str(lc.INCREMENTAL_EXPORT_PATH)], check=True
    )


if __name__ == "__main__":
    main()
