"""List ladder matches that still have no performance data, for the next harvest batch."""
import json

import ladder_common as lc

with open(lc.LADDER_PATH, encoding="utf-8") as f:
    ladder = json.load(f)

db = lc.load_performance_db()

all_ids = [m["match_id"] for m in ladder["match_log"]]
remaining = [i for i in all_ids if db["status"].get(str(i)) not in ("ok", "unavailable", "data_missing")]

statuses = db["status"].values()
print(f"Total matches in ladder: {len(all_ids)}")
print(f"Already ok: {sum(1 for s in statuses if s == 'ok')}")
print(f"Already unavailable: {sum(1 for s in statuses if s in ('unavailable', 'data_missing'))}")
print(f"Remaining to attempt: {len(remaining)}")
print(json.dumps(remaining))
