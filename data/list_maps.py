"""Print every map that has Elo data, most-played first."""
import json

import ladder_common as lc

with open(lc.MAP_ELO_PATH, encoding="utf-8") as f:
    d = json.load(f)

for name, data in sorted(d.items(), key=lambda kv: -kv[1]["total_matches"]):
    print(f"{data['total_matches']:4d}  {name}")
