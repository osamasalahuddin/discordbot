import json
import os
import re
from datetime import datetime, timezone

RAW_DIR = r"E:\Work\Claude\data\unranked_raw"
OUT_PATH = r"E:\Work\Claude\data\unranked_ladder.json"
PERFORMANCE_DB_PATH = r"E:\Work\Claude\data\match_performance.json"

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
AI_SHORT_GAME_THRESHOLD_SECONDS = 15 * 60  # 15 minutes
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
    month_str, day_str, year_str, time_str = None, None, None, None
    m = DATE_RE.match(s.strip())
    if not m:
        return None
    month_raw = m.group("month")
    month_full = MONTH_MAP.get(month_raw, month_raw)
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

# ---- Load performance data (military/economy/eAPM stats) where available ----
try:
    with open(PERFORMANCE_DB_PATH, encoding="utf-8") as f:
        _perf_db = json.load(f)
except FileNotFoundError:
    _perf_db = {"matches": {}, "status": {}}

PROFILE_ID_TO_PATH = {int(path.strip("/").split("/")[1]): path for path in TRACKED}


def get_player_perf(match_id, user_path):
    match_perf = _perf_db["matches"].get(str(match_id))
    if not match_perf:
        return None
    profile_id = int(user_path.strip("/").split("/")[1])
    for entry in match_perf.values():
        if entry.get("profile_id") == profile_id:
            return entry
    return None


def compute_performance_ratio(match_id, own_path, opp_paths):
    own = get_player_perf(match_id, own_path)
    if own is None:
        return None
    opps = [get_player_perf(match_id, p) for p in opp_paths]
    opps = [o for o in opps if o is not None]
    if not opps:
        return None

    ratios = []

    if own.get("eapm_mean") is not None:
        opp_vals = [o["eapm_mean"] for o in opps if o.get("eapm_mean") is not None]
        if opp_vals:
            opp_avg = sum(opp_vals) / len(opp_vals)
            total = own["eapm_mean"] + opp_avg
            if total > 0:
                ratios.append(own["eapm_mean"] / total)

    if own.get("eco_apm_mean") is not None:
        opp_vals = [o["eco_apm_mean"] for o in opps if o.get("eco_apm_mean") is not None]
        if opp_vals:
            opp_avg = sum(opp_vals) / len(opp_vals)
            total = own["eco_apm_mean"] + opp_avg
            if total > 0:
                ratios.append(own["eco_apm_mean"] / total)

    if own.get("castle_time_ms") is not None:
        opp_vals = [o["castle_time_ms"] for o in opps if o.get("castle_time_ms") is not None]
        if opp_vals:
            opp_avg = sum(opp_vals) / len(opp_vals)
            total = own["castle_time_ms"] + opp_avg
            if total > 0:
                ratios.append(opp_avg / total)  # faster own time -> higher ratio

    if not ratios:
        return None
    return sum(ratios) / len(ratios)


# ---- Load and dedupe matches across all 9 players' unranked scrapes ----
all_matches = {}
for fname in RAW_FILES:
    path = rf"{RAW_DIR}\{fname}"
    if not os.path.exists(path):
        continue  # e.g. incremental_new_matches.json before the first incremental run
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for m in data["matches"]:
        all_matches[m["match_id"]] = m  # same match_id -> identical record from any source

print(f"Total unique unranked matches (union across 9 players): {len(all_matches)}")

# ---- Filter: matches with >=2 tracked players on opposing teams ----
qualifying = []
excluded_short_game = []
excluded_no_opposition = 0

for m in all_matches.values():
    team_tracked = []  # list of sets of tracked user_paths per team
    for team in m["teams"]:
        tracked_in_team = {p["user_path"] for p in team["players"] if p["user_path"] in TRACKED}
        team_tracked.append(tracked_in_team)

    teams_with_tracked = [t for t in team_tracked if t]
    if len(teams_with_tracked) < 2:
        excluded_no_opposition += 1
        continue

    duration_s = parse_duration(m["duration"])

    if duration_s is None or duration_s < AI_SHORT_GAME_THRESHOLD_SECONDS:
        excluded_short_game.append(m["match_id"])
        continue

    qualifying.append(m)

print(f"Qualifying matches (2+ tracked players on opposing teams): {len(qualifying)}")
print(f"Excluded (only one side has tracked players / no real opposition): {excluded_no_opposition}")
print(f"Excluded (game duration < 15min, regardless of AI presence): {len(excluded_short_game)}")

# ---- Sort chronologically (oldest first) ----
def sort_key(m):
    dt = parse_exact_time(m["exact_time"])
    return dt if dt else datetime.min.replace(tzinfo=timezone.utc)

qualifying.sort(key=sort_key)

before_cutoff = sum(1 for m in qualifying if sort_key(m) < LADDER_START_DATE)
qualifying = [m for m in qualifying if sort_key(m) >= LADDER_START_DATE]
print(f"Excluded (before ladder start date {LADDER_START_DATE.date()}): {before_cutoff}")
print(f"Matches used for ladder simulation: {len(qualifying)}")

# ---- ELO ladder simulation ----
elo = {path: STARTING_ELO for path in TRACKED}
history = {path: [] for path in TRACKED}
match_log = []

for m in qualifying:
    team_tracked_paths = []
    for team in m["teams"]:
        paths = [p["user_path"] for p in team["players"] if p["user_path"] in TRACKED]
        team_tracked_paths.append(paths)

    # Only teams that actually have tracked players matter for the ladder
    active_team_indices = [i for i, paths in enumerate(team_tracked_paths) if paths]

    if len(active_team_indices) != 2:
        # Rare FFA-style case with tracked players on 3+ sides: pool "everyone else" as the opponent per side
        pass

    match_deltas = []  # (user_path, old_elo, new_elo, delta, won, opp_avg_elo)

    for i in active_team_indices:
        own_paths = team_tracked_paths[i]
        won = bool(m["teams"][i]["won"])
        # Opponent = average elo of tracked players on all OTHER active teams (handles >2-team edge case too)
        opp_paths = [p for j in active_team_indices if j != i for p in team_tracked_paths[j]]
        if not opp_paths:
            continue
        opp_avg = sum(elo[p] for p in opp_paths) / len(opp_paths)

        for path in own_paths:
            own_rating = elo[path]
            expected = 1 / (1 + 10 ** ((opp_avg - own_rating) / 400))

            perf_ratio = compute_performance_ratio(m["match_id"], path, opp_paths)
            if perf_ratio is not None:
                margin = max(-1.0, min(1.0, (perf_ratio - 0.5) / 0.5))
                margin_effective = max(margin, 0.0) if won else min(margin, 0.0)
                actual = 0.5 + 0.5 * margin_effective
            else:
                actual = 1.0 if won else 0.0

            delta = K_FACTOR * (actual - expected)
            new_rating = own_rating + delta
            match_deltas.append((path, own_rating, new_rating, delta, won, opp_avg, perf_ratio, actual))

    if not match_deltas:
        continue

    match_date = parse_exact_time(m["exact_time"])
    for path, old_r, new_r, delta, won, opp_avg, perf_ratio, actual in match_deltas:
        elo[path] = new_r
        history[path].append({
            "match_id": m["match_id"],
            "date": match_date.isoformat() if match_date else None,
            "map": m["map"],
            "won": won,
            "elo_before": round(old_r, 1),
            "elo_after": round(new_r, 1),
            "delta": round(delta, 1),
            "opponent_avg_elo": round(opp_avg, 1),
            "performance_ratio": round(perf_ratio, 3) if perf_ratio is not None else None,
            "actual_score": round(actual, 3),
        })

    match_log.append({
        "match_id": m["match_id"],
        "date": match_date.isoformat() if match_date else None,
        "map": m["map"],
        "duration_seconds": parse_duration(m["duration"]),
        "had_ai": any(p["is_ai"] for team in m["teams"] for p in team["players"]),
        "teams": [
            {
                "won": team["won"],
                "players": [
                    {"name": p["name"], "user_path": p["user_path"], "civ": p["civ"], "is_ai": p["is_ai"],
                     "tracked": p["user_path"] in TRACKED}
                    for p in team["players"]
                ]
            }
            for team in m["teams"]
        ],
    })

# ---- Build final output ----
players_out = {}
for path, name in TRACKED.items():
    matches_played = len(history[path])
    wins = sum(1 for h in history[path] if h["won"])
    players_out[name] = {
        "user_path": path,
        "starting_elo": STARTING_ELO,
        "current_elo": round(elo[path], 1),
        "matches_played": matches_played,
        "wins": wins,
        "losses": matches_played - wins,
        "win_rate": round(wins / matches_played * 100, 1) if matches_played else 0.0,
        "history": history[path],
    }

result = {
    "config": {
        "k_factor": K_FACTOR,
        "starting_elo": STARTING_ELO,
        "short_game_exclusion_threshold_seconds": AI_SHORT_GAME_THRESHOLD_SECONDS,
        "ladder_start_date": LADDER_START_DATE.date().isoformat(),
        "performance_matches_available": sum(1 for s in _perf_db["status"].values() if s == "ok"),
        "performance_adjustment_notes": (
            "Where per-match performance data is available (match_performance.json), the actual "
            "score fed into the Elo update is no longer flat 1.0/0.0 for win/loss. Instead it's "
            "0.5 +/- 0.5*margin, where margin comes from a performance_ratio blending eAPM, "
            "economy-focused APM, and Castle Age timing versus the opponent(s) in that match. "
            "Winning always yields actual in [0.5, 1.0] and losing always yields actual in "
            "[0.0, 0.5] (win/loss still anchors direction), but a dominant win gains more Elo than "
            "a narrow one, and a close loss costs less than a blowout. Matches without performance "
            "data (most of the history, since analysis is only retrievable for roughly the last "
            "8-9 months of games) fall back to the original flat win/loss actual score."
        ),
        "notes": (
            "Elo computed only across unranked (ladder=0) matches where 2+ of the tracked "
            "players appear on opposing teams. Each player's expected score is computed against "
            "the average current Elo of tracked opponents on the other side(s) (untracked/random "
            "players in the match are ignored for rating purposes). This filtering (short-game "
            "and pre-start-date exclusion) only affects which matches feed the Elo calculation "
            "here — the underlying scraped datasets in unranked_raw/ are never modified. Any "
            "match under 15 minutes is excluded from the Elo calculation regardless of whether an "
            "AI was present, since it's too short to be a conclusive result. Longer matches with "
            "an AI player present use the site's recorded win/loss as-is, since AoE2 games resolve "
            "via resignation and the AI side cannot resign, so the recorded result already reflects "
            "who resigned first."
        ),
    },
    "scrape_meta": {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_unique_unranked_matches_scanned": len(all_matches),
        "qualifying_matches_used": len(qualifying),
        "excluded_no_opposition": excluded_no_opposition,
        "excluded_short_game": len(excluded_short_game),
        "excluded_short_game_match_ids": excluded_short_game,
        "excluded_before_start_date": before_cutoff,
    },
    "players": players_out,
    "match_log": match_log,
}

with open(OUT_PATH, "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2)

print(f"\nWrote {OUT_PATH}")
print("\nFinal Elo standings:")
for name, p in sorted(players_out.items(), key=lambda kv: kv[1]["current_elo"], reverse=True):
    print(f"  {name:20s} {p['current_elo']:8.1f}  ({p['matches_played']} matches, {p['win_rate']}% win rate)")
