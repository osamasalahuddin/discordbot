"""List match IDs whose stored performance record predates the current schema.

Writes E:\\Work\\Claude\\data\\perf_chunks\\stale_perf_ids.json - feed it to the
harvester in force mode to re-analyze those matches with the richer extractor:

    # in the debug Chrome console (aoe2insights.com tab), paste incremental_update.js, then:
    ids = <contents of stale_perf_ids.json>
    for (let i = 0; i < ids.length; i += 15) {
      await window.__processBatch(ids.slice(i, i + 15), true);   // true = force re-analyze
      if (window.__rateLimited) { console.log("throttled at", i); break; }
    }
    window.__exportIncremental();   // downloads incremental_update_export.json

Then move the download next to this script and run:
    python merge_incremental_update.py incremental_update_export.json

Records are upgraded in place (status stays 'ok', schema bumps to the current version).
Re-run this script afterwards to confirm the stale count dropped to 0.
"""
import json
import os

DB_PATH = r"E:\Work\Claude\data\match_performance.json"
OUT_PATH = r"E:\Work\Claude\data\perf_chunks\stale_perf_ids.json"
CURRENT_SCHEMA = 2


def perf_schema(record):
    if not record:
        return 0
    return record.get("_meta", {}).get("schema", 1)


def main():
    with open(DB_PATH, encoding="utf-8") as f:
        db = json.load(f)

    stale = []
    by_schema = {}
    for match_id, status in db["status"].items():
        if status != "ok":
            continue
        s = perf_schema(db["matches"].get(match_id))
        by_schema[s] = by_schema.get(s, 0) + 1
        if s < CURRENT_SCHEMA:
            stale.append(int(match_id))

    stale.sort()
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(stale, f)

    print(f"ok records by schema: {dict(sorted(by_schema.items()))}")
    print(f"{len(stale)} record(s) below schema {CURRENT_SCHEMA} -> {OUT_PATH}")
    if stale:
        print("Re-harvest in batches of ~15-20; the site throttles /analyze/ within a session.")


if __name__ == "__main__":
    main()
