import json
import re
from datetime import datetime, timezone
from collections import defaultdict
import os

_DATA_DIR = os.path.dirname(os.path.abspath(__file__))

RAW_PATH = r"C:\Users\osama\AppData\Local\Temp\claude\E--Work-Claude\b32b4be8-8f6d-408d-a9eb-10e7960bcc06\scratchpad\wabbit_matches_raw.json"
OUT_PATH = os.path.join(_DATA_DIR, "players", "wabbit.json")

WABBIT_PATH = "/user/12047120/"

def parse_duration(s):
    if not s:
        return None
    h = m = sec = 0
    hm = re.search(r"(\d+)h", s)
    mm = re.search(r"(\d+)m", s)
    sm = re.search(r"(\d+)s", s)
    if hm: h = int(hm.group(1))
    if mm: m = int(mm.group(1))
    if sm: sec = int(sm.group(1))
    return h * 3600 + m * 60 + sec

def parse_exact_time(s):
    if not s:
        return None
    norm = s.replace("a.m.", "AM").replace("p.m.", "PM")
    try:
        dt = datetime.strptime(norm, "%B %d, %Y, %I:%M %p")
        return dt.replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None

def user_id_from_path(path):
    if not path:
        return None
    m = re.search(r"/user/(\d+)/", path)
    return int(m.group(1)) if m else None

with open(RAW_PATH, encoding="utf-8") as f:
    raw = json.load(f)

matches = []
civ_stats = defaultdict(lambda: {"matches": 0, "wins": 0})
map_stats = defaultdict(lambda: {"matches": 0, "wins": 0})
opponent_stats = defaultdict(lambda: {"matches": 0, "wins": 0, "losses": 0})
teammate_stats = defaultdict(lambda: {"matches": 0, "wins": 0, "losses": 0})

for m in raw:
    wabbit_team_idx = None
    for i, team in enumerate(m["teams"]):
        for p in team["players"]:
            if p["user_path"] == WABBIT_PATH:
                wabbit_team_idx = i
                break
        if wabbit_team_idx is not None:
            break
    if wabbit_team_idx is None:
        continue  # shouldn't happen, safety guard

    own_team = m["teams"][wabbit_team_idx]
    other_teams = [t for i, t in enumerate(m["teams"]) if i != wabbit_team_idx]

    wabbit_player = next(p for p in own_team["players"] if p["user_path"] == WABBIT_PATH)
    teammates = [p for p in own_team["players"] if p["user_path"] != WABBIT_PATH]
    opponents = [p for t in other_teams for p in t["players"]]

    won = bool(own_team["won"])
    civ = wabbit_player["civ"]
    map_name = m["map"]

    civ_stats[civ]["matches"] += 1
    civ_stats[civ]["wins"] += int(won)
    map_stats[map_name]["matches"] += 1
    map_stats[map_name]["wins"] += int(won)

    for opp in opponents:
        if opp["is_ai"]:
            continue
        key = opp["name"]
        opponent_stats[key]["matches"] += 1
        opponent_stats[key]["wins" if won else "losses"] += 1

    for tm in teammates:
        if tm["is_ai"]:
            continue
        key = tm["name"]
        teammate_stats[key]["matches"] += 1
        teammate_stats[key]["wins" if won else "losses"] += 1

    matches.append({
        "match_id": m["match_id"],
        "map": map_name,
        "duration_seconds": parse_duration(m["duration"]),
        "date": parse_exact_time(m["exact_time"]),
        "wabbit": {
            "civ": civ,
            "rating": wabbit_player["rating"],
            "rating_change": wabbit_player["rating_change"],
            "won": won,
        },
        "teammates": [
            {
                "name": p["name"],
                "user_id": user_id_from_path(p["user_path"]),
                "civ": p["civ"],
                "rating": p["rating"],
                "rating_change": p["rating_change"],
                "is_ai": p["is_ai"],
            } for p in teammates
        ],
        "opponents": [
            {
                "name": p["name"],
                "user_id": user_id_from_path(p["user_path"]),
                "civ": p["civ"],
                "rating": p["rating"],
                "rating_change": p["rating_change"],
                "is_ai": p["is_ai"],
            } for p in opponents
        ],
    })

# sort newest first
matches.sort(key=lambda x: x["match_id"], reverse=True)

total = len(matches)
total_wins = sum(1 for x in matches if x["wabbit"]["won"])

def to_sorted_list(d):
    out = []
    for k, v in d.items():
        wins = v.get("wins", 0)
        losses = v.get("losses", v["matches"] - wins if "losses" not in v else v["losses"])
        out.append({
            "name": k,
            "matches": v["matches"],
            "wins": wins,
            "losses": v["matches"] - wins,
            "win_rate": round(wins / v["matches"] * 100, 1) if v["matches"] else 0.0,
        })
    out.sort(key=lambda x: x["matches"], reverse=True)
    return out

result = {
    "player": {
        "name": "wabbit",
        "user_id": 12047120,
        "country": "Germany",
        "profile_url": "https://www.aoe2insights.com/user/12047120/",
        "ratings": {
            "rm_1v1": 976,
            "rm_1v1_unranked": 1004,
            "rm_team": 1081,
            "rm_team_unranked": 1087,
            "ew_1v1": None,
            "ew_team": None,
        },
    },
    "scrape_meta": {
        "source": "aoe2insights.com",
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "matches_scraped": total,
    },
    "aggregate_stats": {
        "overall_matches": total,
        "overall_wins": total_wins,
        "overall_losses": total - total_wins,
        "overall_win_rate": round(total_wins / total * 100, 1) if total else 0.0,
        "by_civ": to_sorted_list(civ_stats),
        "by_map": to_sorted_list(map_stats),
        "top_opponents": to_sorted_list(opponent_stats)[:25],
        "top_teammates": to_sorted_list(teammate_stats)[:25],
    },
    "matches": matches,
}

with open(OUT_PATH, "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2)

print("wrote", OUT_PATH)
print("matches:", total, "win_rate:", result["aggregate_stats"]["overall_win_rate"])
print("civs tracked:", len(civ_stats))
print("maps tracked:", len(map_stats))
