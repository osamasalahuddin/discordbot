import json
import re
import sys
from datetime import datetime, timezone
from collections import defaultdict

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

def clean_rating(v):
    if v is None or v == "-":
        return None
    try:
        return int(v.replace(",", ""))
    except (ValueError, AttributeError):
        return v

def build(raw_path, out_path):
    with open(raw_path, encoding="utf-8") as f:
        raw = json.load(f)

    player_info = raw["player"]
    profile = raw["profile"]
    own_path = player_info["path"]
    raw_matches = raw["matches"]

    ratings_out = {}
    for label, data in profile.get("ratings", {}).items():
        key = label.lower().replace(" ", "_").replace("1v1", "1v1").replace("team", "team")
        ratings_out[label] = {
            "current": clean_rating(data.get("current")),
            **{d["title"]: clean_rating(d["value"]) for d in data.get("details", []) if d.get("title")}
        }

    matches = []
    civ_stats = defaultdict(lambda: {"matches": 0, "wins": 0})
    map_stats = defaultdict(lambda: {"matches": 0, "wins": 0})
    opponent_stats = defaultdict(lambda: {"matches": 0, "wins": 0})
    teammate_stats = defaultdict(lambda: {"matches": 0, "wins": 0})

    for m in raw_matches:
        own_team_idx = None
        for i, team in enumerate(m["teams"]):
            if any(p["user_path"] == own_path for p in team["players"]):
                own_team_idx = i
                break
        if own_team_idx is None:
            continue

        own_team = m["teams"][own_team_idx]
        other_teams = [t for i, t in enumerate(m["teams"]) if i != own_team_idx]

        own_player = next(p for p in own_team["players"] if p["user_path"] == own_path)
        teammates = [p for p in own_team["players"] if p["user_path"] != own_path]
        opponents = [p for t in other_teams for p in t["players"]]

        won = bool(own_team["won"])
        civ = own_player["civ"]
        map_name = m["map"]

        civ_stats[civ]["matches"] += 1
        civ_stats[civ]["wins"] += int(won)
        map_stats[map_name]["matches"] += 1
        map_stats[map_name]["wins"] += int(won)

        for opp in opponents:
            if opp["is_ai"]:
                continue
            opponent_stats[opp["name"]]["matches"] += 1
            opponent_stats[opp["name"]]["wins"] += int(won)

        for tm in teammates:
            if tm["is_ai"]:
                continue
            teammate_stats[tm["name"]]["matches"] += 1
            teammate_stats[tm["name"]]["wins"] += int(won)

        matches.append({
            "match_id": m["match_id"],
            "map": map_name,
            "duration_seconds": parse_duration(m["duration"]),
            "date": parse_exact_time(m["exact_time"]),
            "player": {
                "civ": civ,
                "rating": own_player["rating"],
                "rating_change": own_player["rating_change"],
                "won": won,
            },
            "teammates": [
                {"name": p["name"], "user_id": user_id_from_path(p["user_path"]), "civ": p["civ"],
                 "rating": p["rating"], "rating_change": p["rating_change"], "is_ai": p["is_ai"]}
                for p in teammates
            ],
            "opponents": [
                {"name": p["name"], "user_id": user_id_from_path(p["user_path"]), "civ": p["civ"],
                 "rating": p["rating"], "rating_change": p["rating_change"], "is_ai": p["is_ai"]}
                for p in opponents
            ],
        })

    matches.sort(key=lambda x: x["match_id"], reverse=True)
    total = len(matches)
    total_wins = sum(1 for x in matches if x["player"]["won"])

    def to_sorted_list(d):
        out = []
        for k, v in d.items():
            wins = v["wins"]
            out.append({
                "name": k, "matches": v["matches"], "wins": wins, "losses": v["matches"] - wins,
                "win_rate": round(wins / v["matches"] * 100, 1) if v["matches"] else 0.0,
            })
        out.sort(key=lambda x: x["matches"], reverse=True)
        return out

    result = {
        "player": {
            "name": player_info["name"],
            "user_id": player_info["id"],
            "country": profile.get("country"),
            "profile_url": f"https://www.aoe2insights.com{own_path}",
            "ratings": ratings_out,
        },
        "scrape_meta": {
            "source": "aoe2insights.com",
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "matches_scraped": total,
            "site_reported_total": profile.get("matches_total"),
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

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    print(f"{player_info['name']}: wrote {out_path} | matches={total} win_rate={result['aggregate_stats']['overall_win_rate']}")

if __name__ == "__main__":
    players = [
        ("SauronSlayer", "raw_SauronSlayer.json"),
        ("zubair", "raw_zubair.json"),
        ("l.inc", "raw_l.inc.json"),
        ("toXic", "raw_toXic.json"),
        ("Strength_Honour", "raw_StrengthHonour.json"),
        ("cheetah001", "raw_cheetah001.json"),
        ("NaKiyaKar", "raw_NaKiyaKar.json"),
        ("neXus", "raw_neXus.json"),
    ]
    for out_name, raw_name in players:
        build(
            raw_path=rf"E:\Work\Claude\data\raw\{raw_name}",
            out_path=rf"E:\Work\Claude\data\players\{out_name}.json",
        )
