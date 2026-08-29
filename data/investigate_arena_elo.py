import json
import re
from datetime import datetime, timezone

RAW_DIR = r"E:\Work\Claude\data\unranked_raw"
RAW_FILES = [
    "unranked_wabbit.json", "unranked_SauronSlayer.json", "unranked_zubair.json",
    "unranked_l.inc.json", "unranked_toXic.json", "unranked_StrengthHonour.json",
    "unranked_cheetah001.json", "unranked_NaKiyaKar.json", "unranked_neXus.json",
]
TRACKED = {
    "/user/12047120/": "wabbit", "/user/12676944/": "SauronSlayer", "/user/12667372/": "zubair",
    "/user/12080589/": "l.inc", "/user/12499000/": "toXic", "/user/4607974/": "Strength & Honour",
    "/user/11907023/": "cheetah001", "/user/12693189/": "NaKiyaKar", "/user/12805097/": "neXus",
}
K_FACTOR = 32
STARTING_ELO = 1000
SHORT_GAME_THRESHOLD_SECONDS = 15 * 60
LADDER_START_DATE = datetime(2024, 1, 1, tzinfo=timezone.utc)

def parse_duration(s):
    if not s: return None
    h = m = sec = 0
    hm = re.search(r"(\d+)h", s); mm = re.search(r"(\d+)m", s); sm = re.search(r"(\d+)s", s)
    if hm: h = int(hm.group(1))
    if mm: m = int(mm.group(1))
    if sm: sec = int(sm.group(1))
    return h*3600 + m*60 + sec

MONTH_MAP = {"Jan.": "January", "Feb.": "February", "March": "March", "April": "April", "May": "May",
             "June": "June", "July": "July", "Aug.": "August", "Sept.": "September", "Oct.": "October",
             "Nov.": "November", "Dec.": "December"}
DATE_RE = re.compile(r"^(?P<month>[A-Za-z.]+)\s+(?P<day>\d{1,2}),\s+(?P<year>\d{4}),\s+(?P<time>.+)$")

def parse_exact_time(s):
    if not s: return None
    m = DATE_RE.match(s.strip())
    if not m: return None
    month_full = MONTH_MAP.get(m.group("month"), m.group("month"))
    day, year = int(m.group("day")), int(m.group("year"))
    time_str = m.group("time").strip().lower()
    if time_str == "midnight": hour, minute = 0, 0
    elif time_str == "noon": hour, minute = 12, 0
    else:
        time_norm = time_str.replace("a.m.", "am").replace("p.m.", "pm")
        tm = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", time_norm)
        if not tm: return None
        hour = int(tm.group(1)); minute = int(tm.group(2)) if tm.group(2) else 0
        ampm = tm.group(3)
        if ampm == "pm" and hour != 12: hour += 12
        if ampm == "am" and hour == 12: hour = 0
    try:
        return datetime(year, datetime.strptime(month_full, "%B").month, day, hour, minute, tzinfo=timezone.utc)
    except ValueError:
        return None

all_matches = {}
for fname in RAW_FILES:
    with open(rf"{RAW_DIR}\{fname}", encoding="utf-8") as f:
        data = json.load(f)
    for m in data["matches"]:
        all_matches[m["match_id"]] = m

qualifying = []
for m in all_matches.values():
    if m["map"] != "Arena":
        continue
    team_tracked = [{p["user_path"] for p in team["players"] if p["user_path"] in TRACKED} for team in m["teams"]]
    if len([t for t in team_tracked if t]) < 2:
        continue
    duration_s = parse_duration(m["duration"])
    if duration_s is None or duration_s < SHORT_GAME_THRESHOLD_SECONDS:
        continue
    qualifying.append(m)

qualifying.sort(key=lambda m: parse_exact_time(m["exact_time"]) or datetime.min.replace(tzinfo=timezone.utc))
qualifying = [m for m in qualifying if (parse_exact_time(m["exact_time"]) or datetime.min.replace(tzinfo=timezone.utc)) >= LADDER_START_DATE]

# Run the same simulation but log per-match detail for cheetah001 and SauronSlayer
elo = {path: STARTING_ELO for path in TRACKED}
CHEETAH = "/user/11907023/"
SAURON = "/user/12676944/"
log = {CHEETAH: [], SAURON: []}
head_to_head = {"cheetah_wins": 0, "sauron_wins": 0, "total": 0}

for m in qualifying:
    team_tracked_paths = [[p["user_path"] for p in team["players"] if p["user_path"] in TRACKED] for team in m["teams"]]
    active = [i for i, paths in enumerate(team_tracked_paths) if paths]

    # head to head check
    cheetah_team = next((i for i in active if CHEETAH in team_tracked_paths[i]), None)
    sauron_team = next((i for i in active if SAURON in team_tracked_paths[i]), None)
    if cheetah_team is not None and sauron_team is not None and cheetah_team != sauron_team:
        head_to_head["total"] += 1
        if m["teams"][cheetah_team]["won"]:
            head_to_head["cheetah_wins"] += 1
        else:
            head_to_head["sauron_wins"] += 1

    match_deltas = []
    for i in active:
        own_paths = team_tracked_paths[i]
        won = bool(m["teams"][i]["won"])
        opp_paths = [p for j in active if j != i for p in team_tracked_paths[j]]
        if not opp_paths:
            continue
        opp_avg = sum(elo[p] for p in opp_paths) / len(opp_paths)
        for path in own_paths:
            own_rating = elo[path]
            expected = 1 / (1 + 10 ** ((opp_avg - own_rating) / 400))
            actual = 1.0 if won else 0.0
            delta = K_FACTOR * (actual - expected)
            match_deltas.append((path, own_rating, opp_avg, expected, delta, won))

    for path, old_r, opp_avg, expected, delta, won in match_deltas:
        elo[path] = old_r + delta
        if path in log:
            log[path].append({
                "match_id": m["match_id"], "date": m["exact_time"], "won": won,
                "own_elo_before": round(old_r, 1), "opp_avg_elo": round(opp_avg, 1),
                "expected": round(expected, 3), "delta": round(delta, 2), "elo_after": round(old_r + delta, 1),
            })

print("Head to head (Arena, qualifying matches, cheetah001 vs SauronSlayer on opposing teams):")
print(head_to_head)
print()

for path, name in [(CHEETAH, "cheetah001"), (SAURON, "SauronSlayer")]:
    entries = log[path]
    wins = [e for e in entries if e["won"]]
    losses = [e for e in entries if not e["won"]]
    avg_opp_when_win = sum(e["opp_avg_elo"] for e in wins) / len(wins) if wins else 0
    avg_opp_when_loss = sum(e["opp_avg_elo"] for e in losses) / len(losses) if losses else 0
    avg_expected_overall = sum(e["expected"] for e in entries) / len(entries)
    total_delta_wins = sum(e["delta"] for e in wins)
    total_delta_losses = sum(e["delta"] for e in losses)
    print(f"=== {name} on Arena ===")
    print(f"  matches: {len(entries)}, wins: {len(wins)} ({len(wins)/len(entries)*100:.1f}%), losses: {len(losses)}")
    print(f"  avg opponent elo when WINNING: {avg_opp_when_win:.1f}")
    print(f"  avg opponent elo when LOSING:  {avg_opp_when_loss:.1f}")
    print(f"  avg pre-match expected score (own perspective): {avg_expected_overall:.3f}  (>0.5 = favored on average)")
    print(f"  total elo gained from wins: {total_delta_wins:+.1f}")
    print(f"  total elo lost from losses: {total_delta_losses:+.1f}")
    print(f"  net elo change: {total_delta_wins+total_delta_losses:+.1f}  (starting 1000 -> {1000+total_delta_wins+total_delta_losses:.1f})")
    print(f"  first 3 matches: {entries[:3]}")
    print(f"  last 3 matches: {entries[-3:]}")
    print()
