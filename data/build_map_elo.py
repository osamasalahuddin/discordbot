import json
import os
import re
import sys
from datetime import datetime, timezone
from collections import defaultdict

_DATA_DIR = os.path.dirname(os.path.abspath(__file__))
# run_full_refresh.py invokes these via runpy, which does not put the script's
# own directory on sys.path - do it here so sibling modules import cleanly.
if _DATA_DIR not in sys.path:
    sys.path.insert(0, _DATA_DIR)
from match_results import resolve_match_results

RAW_DIR = os.path.join(_DATA_DIR, "unranked_raw")
OUT_PATH = os.path.join(_DATA_DIR, "map_elo.json")

RAW_FILES = [
    "unranked_wabbit.json",
    "unranked_SauronSlayer.json",
    "unranked_zubair.json",
    "unranked_l.inc.json",
    "unranked_toXic.json",
    "unranked_StrengthHonour.json",
    "unranked_cheetah001.json",
    "unranked_NaKiyaKar.json",
    "unranked_neXus.json",
    "incremental_new_matches.json",
]

TRACKED = {
    "/user/12047120/": "wabbit",
    "/user/12676944/": "SauronSlayer",
    "/user/12667372/": "zubair",
    "/user/12080589/": "l.inc",
    "/user/12499000/": "toXic",
    "/user/4607974/": "Strength & Honour",
    "/user/11907023/": "cheetah001",
    "/user/12693189/": "NaKiyaKar",
    "/user/12805097/": "neXus",
}

K_FACTOR = 32
STARTING_ELO = 1000
SHORT_GAME_THRESHOLD_SECONDS = 15 * 60
LADDER_START_DATE = datetime(2024, 1, 1, tzinfo=timezone.utc)


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


MONTH_MAP = {
    "Jan.": "January", "Feb.": "February", "March": "March", "April": "April",
    "May": "May", "June": "June", "July": "July", "Aug.": "August",
    "Sept.": "September", "Oct.": "October", "Nov.": "November", "Dec.": "December",
}

DATE_RE = re.compile(
    r"^(?P<month>[A-Za-z.]+)\s+(?P<day>\d{1,2}),\s+(?P<year>\d{4}),\s+(?P<time>.+)$"
)


def parse_exact_time(s):
    if not s:
        return None
    m = DATE_RE.match(s.strip())
    if not m:
        return None
    month_full = MONTH_MAP.get(m.group("month"), m.group("month"))
    day = int(m.group("day"))
    year = int(m.group("year"))
    time_str = m.group("time").strip().lower()

    if time_str == "midnight":
        hour, minute = 0, 0
    elif time_str == "noon":
        hour, minute = 12, 0
    else:
        time_norm = time_str.replace("a.m.", "am").replace("p.m.", "pm")
        tm = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", time_norm)
        if not tm:
            return None
        hour = int(tm.group(1))
        minute = int(tm.group(2)) if tm.group(2) else 0
        ampm = tm.group(3)
        if ampm == "pm" and hour != 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0

    try:
        month_num = datetime.strptime(month_full, "%B").month
        return datetime(year, month_num, day, hour, minute, tzinfo=timezone.utc)
    except ValueError:
        return None


# ---- Load and dedupe matches across all 9 players' unranked scrapes ----
all_matches = {}
for fname in RAW_FILES:
    path = os.path.join(RAW_DIR, fname)
    if not os.path.exists(path):
        continue
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for m in data["matches"]:
        all_matches[m["match_id"]] = m

# ---- Same qualifying filter as the main ladder: 2+ tracked opposing, >=15min, >= Jan 2024 ----
qualifying = []
for m in all_matches.values():
    team_tracked = [
        {p["user_path"] for p in team["players"] if p["user_path"] in TRACKED}
        for team in m["teams"]
    ]
    if len([t for t in team_tracked if t]) < 2:
        continue
    duration_s = parse_duration(m["duration"])
    if duration_s is None or duration_s < SHORT_GAME_THRESHOLD_SECONDS:
        continue
    qualifying.append(m)


def sort_key(m):
    dt = parse_exact_time(m["exact_time"])
    return dt if dt else datetime.min.replace(tzinfo=timezone.utc)


qualifying.sort(key=sort_key)
qualifying = [m for m in qualifying if sort_key(m) >= LADDER_START_DATE]

# Repair matches whose winner flag was lost in the scrape, then override the
# engine's verdict on AI matches from resign data. Same rule as the main ladder.
_res = resolve_match_results(qualifying)
_unresolved = set(_res["unresolved"])
if _unresolved:
    qualifying = [m for m in qualifying if m["match_id"] not in _unresolved]
    print(f"Excluded (result unresolvable): {len(_unresolved)}")

print(f"Total qualifying matches (same filter as main ladder): {len(qualifying)}")

# ---- Group by map ----
by_map = defaultdict(list)
for m in qualifying:
    by_map[m["map"]].append(m)

print(f"Distinct maps: {len(by_map)}")

# ---- Per-map win/loss-only Elo simulation ----
map_results = {}
for map_name, matches in by_map.items():
    elo = {path: STARTING_ELO for path in TRACKED}
    history = {path: [] for path in TRACKED}

    for m in matches:
        team_tracked_paths = [
            [p["user_path"] for p in team["players"] if p["user_path"] in TRACKED]
            for team in m["teams"]
        ]
        active_team_indices = [i for i, paths in enumerate(team_tracked_paths) if paths]

        match_deltas = []
        for i in active_team_indices:
            own_paths = team_tracked_paths[i]
            won = bool(m["teams"][i]["won"])
            opp_paths = [p for j in active_team_indices if j != i for p in team_tracked_paths[j]]
            if not opp_paths:
                continue
            opp_avg = sum(elo[p] for p in opp_paths) / len(opp_paths)

            for path in own_paths:
                own_rating = elo[path]
                expected = 1 / (1 + 10 ** ((opp_avg - own_rating) / 400))
                actual = 1.0 if won else 0.0
                delta = K_FACTOR * (actual - expected)
                new_rating = own_rating + delta
                match_deltas.append((path, own_rating, new_rating, delta, won))

        for path, old_r, new_r, delta, won in match_deltas:
            elo[path] = new_r
            history[path].append({"match_id": m["match_id"], "won": won, "elo_after": round(new_r, 1)})

    players_out = {}
    for path, name in TRACKED.items():
        matches_played = len(history[path])
        wins = sum(1 for h in history[path] if h["won"])
        players_out[name] = {
            "current_elo": round(elo[path], 1),
            "matches_played": matches_played,
            "wins": wins,
            "losses": matches_played - wins,
            "win_rate": round(wins / matches_played * 100, 1) if matches_played else 0.0,
        }

    map_results[map_name] = {
        "total_matches": len(matches),
        "players": players_out,
    }

with open(OUT_PATH, "w", encoding="utf-8") as f:
    json.dump(map_results, f, indent=2)

print(f"\nWrote {OUT_PATH}")

# ---- Top 5 most played maps ----
top_maps = sorted(map_results.items(), key=lambda kv: kv[1]["total_matches"], reverse=True)[:5]

print("\nTop 5 most played maps:")
for map_name, data in top_maps:
    print(f"\n=== {map_name} ({data['total_matches']} matches) ===")
    standings = sorted(
        [(name, p) for name, p in data["players"].items() if p["matches_played"] > 0],
        key=lambda kv: kv[1]["current_elo"], reverse=True
    )
    for name, p in standings:
        print(f"  {name:20s} {p['current_elo']:8.1f}  ({p['matches_played']} matches, {p['win_rate']}% win rate)")
