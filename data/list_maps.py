import json

d = json.load(open(r"E:\Work\Claude\data\map_elo.json", encoding="utf-8"))
maps = sorted(d.items(), key=lambda kv: -kv[1]["total_matches"])
for name, data in maps:
    print(f"{data['total_matches']:4d}  {name}")
