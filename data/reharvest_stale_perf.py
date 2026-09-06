"""Re-analyze the matches whose stored performance record is below the current
schema, using the richer extractor in incremental_update.js.

Preconditions (same as fetch_incremental_update.py):
  - launch_chrome_debug.ps1 has been run, aoe2insights.com is open in that window
    and past any Cloudflare challenge.

Then:
  python get_stale_perf_ids.py          # refresh the stale list
  python reharvest_stale_perf.py        # this script - resumable, run repeatedly

It processes in small batches, stops cleanly on rate-limiting (HTTP 403), writes an
export, and merges it into match_performance.json via merge_incremental_update.py.
Re-run until 'stale remaining' reaches 0.
"""
import json
import subprocess
import sys
import time

from playwright.sync_api import sync_playwright

CDP_URL = "http://localhost:9222"
INCREMENTAL_JS_PATH = r"E:\Work\Claude\data\incremental_update.js"
STALE_IDS_PATH = r"E:\Work\Claude\data\perf_chunks\stale_perf_ids.json"
EXPORT_PATH = r"E:\Work\Claude\data\reharvest_export.json"
MERGE_SCRIPT = r"E:\Work\Claude\data\merge_incremental_update.py"

BATCH_SIZE = 15
PAUSE_BETWEEN_BATCHES_S = 6


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def main():
    with open(STALE_IDS_PATH, encoding="utf-8") as f:
        stale_ids = json.load(f)
    if not stale_ids:
        print("Nothing stale - match_performance.json is fully on the current schema.")
        return
    print(f"{len(stale_ids)} match(es) to re-analyze.")

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            die(f"Could not connect to Chrome at {CDP_URL}. Run launch_chrome_debug.ps1 first.\n{e}")

        target = None
        for context in browser.contexts:
            for page in context.pages:
                if "aoe2insights.com" in page.url:
                    target = page
                    break
            if target:
                break
        if target is None:
            die("No aoe2insights.com tab open in the debug Chrome.")
        if "just a moment" in target.title().lower():
            die("The aoe2insights.com tab is stuck on a Cloudflare challenge. Solve it and re-run.")

        with open(INCREMENTAL_JS_PATH, encoding="utf-8") as f:
            target.evaluate(f.read())

        # Fresh accumulators for this run.
        target.evaluate("() => { window.__perfData = {}; window.__perfStatus = {}; window.__rateLimited = false; }")

        done = 0
        rate_limited = False
        for i in range(0, len(stale_ids), BATCH_SIZE):
            batch = stale_ids[i:i + BATCH_SIZE]
            summary = target.evaluate("(ids) => window.__processBatch(ids, true)", batch)
            done += len(batch)
            counts = summary.get("counts", {})
            print(f"  {done}/{len(stale_ids)}  ok={counts.get('ok', 0)} "
                  f"data_missing={counts.get('data_missing', 0)} unavailable={counts.get('unavailable', 0)} "
                  f"timeout={counts.get('timeout', 0)}")
            if summary.get("rateLimited"):
                rate_limited = True
                print(f"  Rate-limited (403). Stopping at {done}. Re-run later to continue.")
                break
            if i + BATCH_SIZE < len(stale_ids):
                time.sleep(PAUSE_BETWEEN_BATCHES_S)

        perf_data = target.evaluate("() => window.__perfData")
        perf_status = target.evaluate("() => window.__perfStatus")

    with open(EXPORT_PATH, "w", encoding="utf-8") as f:
        json.dump({"newRawMatches": {}, "perfData": perf_data, "perfStatus": perf_status}, f)
    print(f"\nWrote {EXPORT_PATH} ({len(perf_data)} records)")

    print("Merging into match_performance.json ...")
    subprocess.run([sys.executable, MERGE_SCRIPT, EXPORT_PATH], check=True)

    subprocess.run([sys.executable, r"E:\Work\Claude\data\get_stale_perf_ids.py"], check=True)
    if rate_limited:
        print("\nStopped early due to throttling - run this script again in a while.")


if __name__ == "__main__":
    main()
