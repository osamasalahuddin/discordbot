import json
import os

_DATA_DIR = os.path.dirname(os.path.abspath(__file__))

d = json.load(open(os.path.join(_DATA_DIR, "map_elo.json"), encoding="utf-8"))
maps = sorted(d.items(), key=lambda kv: -kv[1]["total_matches"])
for name, data in maps:
    print(f"{data['total_matches']:4d}  {name}")
