"""Refresh perf_chunks/known_ids.json — the match ids the browser-side pipeline treats
as already harvested.

Run before every discovery pass. __discoverNewMatches only stops paging once it recognises
an id, so a stale list makes it re-walk old pages and re-analyze matches it already has
against a throttled endpoint. fetch_incremental_update.py and run_full_refresh.py both
call this automatically.
"""
import ladder_common as lc

if __name__ == "__main__":
    ids = lc.write_known_ids()
    print(f"Total known match IDs: {len(ids)}")
    print(f"Wrote {lc.KNOWN_IDS_PATH}")
