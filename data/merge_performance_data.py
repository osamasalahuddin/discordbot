import json
import os

_DATA_DIR = os.path.dirname(os.path.abspath(__file__))

DB_PATH = os.path.join(_DATA_DIR, "match_performance.json")


def load_db():
    if os.path.exists(DB_PATH):
        with open(DB_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"matches": {}, "status": {}}


def save_db(db):
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2)


def perf_schema(record):
    """Schema version of a per-match performance record. Pre-2026 records are
    flat {pid: {...}} with no _meta block -> schema 1. Missing record -> 0."""
    if not record:
        return 0
    return record.get("_meta", {}).get("schema", 1)


def merge_raw_export(raw_path):
    """Merge a raw browser-harvested export ({perfData, perfStatus}) into the persistent DB.
    'ok' and 'unavailable' are terminal states and get recorded permanently.
    Anything else (timeout/parse_error/etc) is left as 'pending' for retry, and never
    overwrites an existing terminal status for that match.
    """
    with open(raw_path, encoding="utf-8") as f:
        raw = json.load(f)

    db = load_db()
    added_ok = 0
    added_unavailable = 0
    left_pending = 0
    upgraded = 0

    for match_id, status in raw["perfStatus"].items():
        existing = db["status"].get(match_id)
        incoming = raw["perfData"].get(match_id)
        if existing in ("ok", "unavailable", "data_missing"):
            # Terminal already, but a richer extraction of an 'ok' match may replace it.
            if (
                existing == "ok"
                and status == "ok"
                and incoming is not None
                and perf_schema(incoming) > perf_schema(db["matches"].get(match_id))
            ):
                db["matches"][match_id] = incoming
                upgraded += 1
            continue
        if status == "ok":
            db["matches"][match_id] = incoming
            db["status"][match_id] = "ok"
            added_ok += 1
        elif status in ("unavailable", "data_missing"):
            db["status"][match_id] = status
            added_unavailable += 1
        else:
            db["status"][match_id] = "pending"
            left_pending += 1

    save_db(db)
    print(f"Merged {raw_path}")
    print(f"  newly ok: {added_ok}")
    print(f"  newly unavailable: {added_unavailable}")
    print(f"  left pending (retry later): {left_pending}")
    print(f"  upgraded to newer schema: {upgraded}")
    print(f"  DB now has {sum(1 for s in db['status'].values() if s=='ok')} ok, "
          f"{sum(1 for s in db['status'].values() if s in ('unavailable','data_missing'))} unavailable/data_missing, "
          f"{sum(1 for s in db['status'].values() if s=='pending')} pending")


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(_DATA_DIR, "match_performance_partial.json")
    merge_raw_export(path)
