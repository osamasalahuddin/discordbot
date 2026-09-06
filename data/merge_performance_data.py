"""Fold a raw browser harvest ({perfData, perfStatus}) into match_performance.json."""
import json
import sys

import ladder_common as lc


def merge_raw_export(raw_path):
    """'ok', 'unavailable' and 'data_missing' are terminal states and get recorded
    permanently. Anything else (timeout/parse_error/etc) is left as 'pending' for retry,
    and never overwrites an existing terminal status for that match.
    """
    with open(raw_path, encoding="utf-8") as f:
        raw = json.load(f)

    db = lc.load_performance_db()
    added_ok = added_unavailable = left_pending = 0

    for match_id, status in raw["perfStatus"].items():
        if db["status"].get(match_id) in ("ok", "unavailable", "data_missing"):
            continue  # already terminal, don't downgrade
        if status == "ok":
            db["matches"][match_id] = raw["perfData"][match_id]
            db["status"][match_id] = "ok"
            added_ok += 1
        elif status in ("unavailable", "data_missing"):
            db["status"][match_id] = status
            added_unavailable += 1
        else:
            db["status"][match_id] = "pending"
            left_pending += 1

    lc.write_json(lc.PERFORMANCE_DB_PATH, db)
    statuses = db["status"].values()
    print(f"Merged {raw_path}")
    print(f"  newly ok: {added_ok}")
    print(f"  newly unavailable: {added_unavailable}")
    print(f"  left pending (retry later): {left_pending}")
    print(f"  DB now has {sum(1 for s in statuses if s == 'ok')} ok, "
          f"{sum(1 for s in statuses if s in ('unavailable', 'data_missing'))} unavailable/data_missing, "
          f"{sum(1 for s in statuses if s == 'pending')} pending")


if __name__ == "__main__":
    default = lc.DATA_DIR / "match_performance_partial.json"
    merge_raw_export(sys.argv[1] if len(sys.argv) > 1 else default)
