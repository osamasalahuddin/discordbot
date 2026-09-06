"""Fold a browser export ({newRawMatches, perfData, perfStatus}) into the on-disk stores."""
import json
import sys

import ladder_common as lc


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def merge(export_path):
    with open(export_path, encoding="utf-8") as f:
        export = json.load(f)

    # --- merge newly discovered raw matches (all of them, not just qualifying) ---
    incremental = load_json(lc.INCREMENTAL_RAW_PATH, {"matches": []})
    existing_ids = {m["match_id"] for m in incremental["matches"]}
    added = 0
    for m in export.get("newRawMatches", {}).values():
        if m["match_id"] not in existing_ids:
            incremental["matches"].append(m)
            existing_ids.add(m["match_id"])
            added += 1
    lc.write_json(lc.INCREMENTAL_RAW_PATH, incremental)
    print(f"Raw match store: +{added} new matches (total {len(incremental['matches'])})")

    # --- merge performance data (same logic as merge_performance_data.py) ---
    perf_db = lc.load_performance_db()
    added_ok = added_unavailable = left_pending = 0
    for match_id, status in export.get("perfStatus", {}).items():
        if perf_db["status"].get(match_id) in ("ok", "unavailable", "data_missing"):
            continue  # already terminal, don't downgrade
        if status == "ok":
            perf_db["matches"][match_id] = export["perfData"][match_id]
            perf_db["status"][match_id] = "ok"
            added_ok += 1
        elif status in ("unavailable", "data_missing"):
            perf_db["status"][match_id] = status
            added_unavailable += 1
        else:
            perf_db["status"][match_id] = "pending"
            left_pending += 1
    lc.write_json(lc.PERFORMANCE_DB_PATH, perf_db)

    statuses = perf_db["status"].values()
    print(f"Performance DB: +{added_ok} ok, +{added_unavailable} unavailable/data_missing, {left_pending} pending")
    print(f"Performance DB totals: {sum(1 for s in statuses if s == 'ok')} ok, "
          f"{sum(1 for s in statuses if s in ('unavailable', 'data_missing'))} unavailable, "
          f"{sum(1 for s in statuses if s == 'pending')} pending")


if __name__ == "__main__":
    merge(sys.argv[1] if len(sys.argv) > 1 else lc.INCREMENTAL_EXPORT_PATH)
