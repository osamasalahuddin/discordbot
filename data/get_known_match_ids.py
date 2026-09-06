import json
import os

_DATA_DIR = os.path.dirname(os.path.abspath(__file__))

RAW_DIR = os.path.join(_DATA_DIR, "unranked_raw")
RAW_FILES = [
    "unranked_wabbit.json", "unranked_SauronSlayer.json", "unranked_zubair.json",
    "unranked_l.inc.json", "unranked_toXic.json", "unranked_StrengthHonour.json",
    "unranked_cheetah001.json", "unranked_NaKiyaKar.json", "unranked_neXus.json",
    "incremental_new_matches.json",
]

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
