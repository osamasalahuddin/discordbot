import json
import os
import sys

_DATA_DIR = os.path.dirname(os.path.abspath(__file__))
if _DATA_DIR not in sys.path:
    sys.path.insert(0, _DATA_DIR)
from tracked_players import RAW_FILES

RAW_DIR = os.path.join(_DATA_DIR, "unranked_raw")

known_ids = set()
for fname in RAW_FILES:
    path = os.path.join(RAW_DIR, fname)
    if not os.path.exists(path):
        continue
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for m in data["matches"]:
        known_ids.add(m["match_id"])

print(f"Total known match IDs: {len(known_ids)}")
with open(os.path.join(_DATA_DIR, "perf_chunks", "known_ids.json"), "w") as f:
    json.dump(sorted(known_ids), f)
