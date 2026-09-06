import json
import os

_DATA_DIR = os.path.dirname(os.path.abspath(__file__))

LADDER_PATH = os.path.join(_DATA_DIR, "unranked_ladder.json")
DB_PATH = os.path.join(_DATA_DIR, "match_performance.json")

with open(LADDER_PATH, encoding="utf-8") as f:
    ladder = json.load(f)

try:
    with open(DB_PATH, encoding="utf-8") as f:
        db = json.load(f)
except FileNotFoundError:
    db = {"matches": {}, "status": {}}

all_ids = [m["match_id"] for m in ladder["match_log"]]
remaining = [i for i in all_ids if db["status"].get(str(i)) not in ("ok", "unavailable", "data_missing")]

print(f"Total matches in ladder: {len(all_ids)}")
print(f"Already ok: {sum(1 for s in db['status'].values() if s == 'ok')}")
print(f"Already unavailable: {sum(1 for s in db['status'].values() if s in ('unavailable', 'data_missing'))}")
print(f"Remaining to attempt: {len(remaining)}")
print(json.dumps(remaining))
