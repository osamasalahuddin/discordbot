import json
import os
import sys

INCREMENTAL_RAW_PATH = r"E:\Work\Claude\data\unranked_raw\incremental_new_matches.json"
PERFORMANCE_DB_PATH = r"E:\Work\Claude\data\match_performance.json"


def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def merge(export_path):
    with open(export_path, encoding="utf-8") as f:
        export = json.load(f)

    # --- merge newly discovered raw matches (all of them, not just qualifying) ---
    incremental = load_json(INCREMENTAL_RAW_PATH, {"matches": []})
    existing_ids = {m["match_id"] for m in incremental["matches"]}
    added = 0
    for match_id_str, m in export.get("newRawMatches", {}).items():
        if m["match_id"] not in existing_ids:
            incremental["matches"].append(m)
            existing_ids.add(m["match_id"])
            added += 1
    os.makedirs(os.path.dirname(INCREMENTAL_RAW_PATH), exist_ok=True)
    with open(INCREMENTAL_RAW_PATH, "w", encoding="utf-8") as f:
        json.dump(incremental, f, indent=2)
    print(f"Raw match store: +{added} new matches (total {len(incremental['matches'])})")

    # --- merge performance data (same logic as merge_performance_data.py) ---
    perf_db = load_json(PERFORMANCE_DB_PATH, {"matches": {}, "status": {}})
    added_ok = added_unavailable = left_pending = 0
    for match_id, status in export.get("perfStatus", {}).items():
        existing = perf_db["status"].get(match_id)
        if existing in ("ok", "unavailable", "data_missing"):
            continue
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
    with open(PERFORMANCE_DB_PATH, "w", encoding="utf-8") as f:
        json.dump(perf_db, f, indent=2)
    print(f"Performance DB: +{added_ok} ok, +{added_unavailable} unavailable/data_missing, {left_pending} pending")
    print(f"Performance DB totals: {sum(1 for s in perf_db['status'].values() if s=='ok')} ok, "
          f"{sum(1 for s in perf_db['status'].values() if s in ('unavailable','data_missing'))} unavailable, "
          f"{sum(1 for s in perf_db['status'].values() if s=='pending')} pending")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else r"E:\Work\Claude\data\incremental_update_export.json"
    merge(path)
