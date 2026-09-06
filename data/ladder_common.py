"""Shared configuration and helpers for the ladder build scripts.

Everything that was copy-pasted across build_unranked_ladder.py, build_map_elo.py and
build_openclosed_elo.py lives here, so the qualifying filter and the Elo maths can only
ever be changed in one place.

Paths default to this file's own directory (the repo's data/), so the scripts run from
any checkout on any OS. Point DATA_DIR at somewhere else to keep the working data
outside the repo.
"""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent))
RAW_DIR = Path(os.environ.get("RAW_DIR", DATA_DIR / "unranked_raw"))
PERF_CHUNKS_DIR = DATA_DIR / "perf_chunks"
GRAPHS_DIR = Path(os.environ.get("GRAPHS_DIR", DATA_DIR / "graphs"))

LADDER_PATH = DATA_DIR / "unranked_ladder.json"
MAP_ELO_PATH = DATA_DIR / "map_elo.json"
OPENCLOSED_ELO_PATH = DATA_DIR / "openclosed_elo.json"
PERFORMANCE_DB_PATH = DATA_DIR / "match_performance.json"
KNOWN_IDS_PATH = PERF_CHUNKS_DIR / "known_ids.json"
INCREMENTAL_RAW_PATH = RAW_DIR / "incremental_new_matches.json"
INCREMENTAL_EXPORT_PATH = DATA_DIR / "incremental_update_export.json"

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
# Any match shorter than this is excluded regardless of whether an AI was present,
# since it's too short to be a conclusive result.
SHORT_GAME_THRESHOLD_SECONDS = 15 * 60
LADDER_START_DATE = datetime(2024, 1, 1, tzinfo=timezone.utc)

# Sort fallback for matches whose timestamp couldn't be parsed; they sort first and are
# then dropped by the LADDER_START_DATE cutoff.
EPOCH = datetime.min.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- parsing helpers

def parse_duration(s):
    """'1h 04m 12s' -> seconds. None if unparseable."""
    if not s:
        return None
    h = m = sec = 0
    hm = re.search(r"(\d+)h", s)
    mm = re.search(r"(\d+)m", s)
    sm = re.search(r"(\d+)s", s)
    if hm:
        h = int(hm.group(1))
    if mm:
        m = int(mm.group(1))
    if sm:
        sec = int(sm.group(1))
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
    """'March 11, 2023, 1:46 a.m.' -> aware datetime. None if unparseable."""
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


def match_datetime(match):
    """Parsed timestamp, or EPOCH so unparseable matches still sort deterministically."""
    return parse_exact_time(match.get("exact_time")) or EPOCH


# ---------------------------------------------------------------- loading

def load_all_matches(raw_files=None, raw_dir=None):
    """Union of every scraped match, keyed by match_id.

    The same match_id yields an identical record from whichever player's scrape it came
    from, so later files simply overwrite earlier ones.
    """
    raw_files = RAW_FILES if raw_files is None else raw_files
    raw_dir = RAW_DIR if raw_dir is None else Path(raw_dir)

    all_matches = {}
    for fname in raw_files:
        path = raw_dir / fname
        if not path.exists():
            continue  # e.g. incremental_new_matches.json before the first incremental run
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for m in data["matches"]:
            all_matches[m["match_id"]] = m
    return all_matches


def known_match_ids(raw_files=None, raw_dir=None):
    return sorted(load_all_matches(raw_files, raw_dir))


def write_known_ids(path=None):
    """Refresh the known-match-id list the browser-side incremental pipeline reads.

    Must run before every discovery pass: __discoverNewMatches only stops paging once it
    recognises an id, so a stale list makes it re-walk and re-analyze old matches against
    a throttled endpoint.
    """
    path = KNOWN_IDS_PATH if path is None else Path(path)
    ids = known_match_ids()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ids, f)
    return ids


def load_performance_db(path=None):
    path = PERFORMANCE_DB_PATH if path is None else Path(path)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"matches": {}, "status": {}}


# ---------------------------------------------------------------- match qualification

def tracked_paths_by_team(match):
    """Per team, the tracked user_paths on it (index-aligned with match['teams'])."""
    return [
        [p["user_path"] for p in team["players"] if p["user_path"] in TRACKED]
        for team in match["teams"]
    ]


def resolve_won_flags(match):
    """Which team won, as a list index-aligned with match['teams'], or None.

    The scraper reads the winner from a '.won' CSS class on the team element. When
    aoe2insights renders a match without it every team comes back won=False, and treating
    that as "everybody lost" costs every tracked player in the match a full loss - which
    deflated the whole ladder by ~120 Elo before this was fixed.

    Fall back to the per-player rating_change signs captured in the same scrape: the
    winning side gains rating and the losing side loses it. A team is only believed when
    every rating_change it has agrees in sign, and the match is only decided when exactly
    one team won and the rest lost. Verified against 866 matches where the site did
    report a winner: 866 agreements, 0 disagreements.

    Returns None when the result cannot be established, so the caller can exclude the
    match rather than invent a result. Shared victories (several teams flagged as won, as
    happens on scraped FFA rows) are also None - they don't fit a two-sided Elo update.
    """
    flags = [bool(t.get("won")) for t in match["teams"]]
    if sum(flags) == 1:
        return flags
    if sum(flags) > 1:
        return None

    inferred = []
    for team in match["teams"]:
        changes = [
            p.get("rating_change") for p in team["players"]
            if p.get("rating_change") is not None
        ]
        if not changes:
            inferred.append(None)
        elif all(c > 0 for c in changes):
            inferred.append(True)
        elif all(c < 0 for c in changes):
            inferred.append(False)
        else:
            inferred.append(None)  # mixed signs within one team - don't trust the row

    if None in inferred or sum(1 for x in inferred if x) != 1:
        return None
    return inferred


def select_qualifying(all_matches):
    """Matches that feed an Elo simulation, oldest first, plus exclusion counts.

    Applies, in order: 2+ tracked players on opposing teams, minimum duration, a
    decidable result, and the ladder start date.

    Normalises match['teams'][i]['won'] to the resolved result on the matches it returns,
    so callers and the published match_log agree on who won. The scraped files in
    unranked_raw/ are never written back to.
    """
    qualifying = []
    stats = {
        "excluded_no_opposition": 0,
        "excluded_short_game": 0,
        "excluded_short_game_match_ids": [],
        "excluded_no_result": 0,
        "excluded_no_result_match_ids": [],
        "excluded_before_start_date": 0,
    }

    for m in all_matches.values():
        if sum(1 for paths in tracked_paths_by_team(m) if paths) < 2:
            stats["excluded_no_opposition"] += 1
            continue

        duration_s = parse_duration(m.get("duration"))
        if duration_s is None or duration_s < SHORT_GAME_THRESHOLD_SECONDS:
            stats["excluded_short_game"] += 1
            stats["excluded_short_game_match_ids"].append(m["match_id"])
            continue

        won_flags = resolve_won_flags(m)
        if won_flags is None:
            stats["excluded_no_result"] += 1
            stats["excluded_no_result_match_ids"].append(m["match_id"])
            continue

        for team, won in zip(m["teams"], won_flags):
            team["won"] = won
        qualifying.append(m)

    qualifying.sort(key=match_datetime)

    before = len(qualifying)
    qualifying = [m for m in qualifying if match_datetime(m) >= LADDER_START_DATE]
    stats["excluded_before_start_date"] = before - len(qualifying)

    return qualifying, stats


# ---------------------------------------------------------------- Elo

def expected_score(own_rating, opponent_rating):
    return 1 / (1 + 10 ** ((opponent_rating - own_rating) / 400))


def simulate_winloss(matches):
    """Plain win/loss Elo over `matches` (assumed already sorted and qualified).

    Used for the per-map and open/closed ladders, which deliberately ignore the
    performance-ratio weighting the main ladder applies.

    Returns the players_out dict written into map_elo.json / openclosed_elo.json.
    """
    elo = {path: float(STARTING_ELO) for path in TRACKED}
    wins = {path: 0 for path in TRACKED}
    played = {path: 0 for path in TRACKED}

    for m in matches:
        team_paths = tracked_paths_by_team(m)
        active = [i for i, paths in enumerate(team_paths) if paths]

        # Collect every update against the pre-match ratings, then apply them together,
        # so within one match nobody is rated against a teammate's already-updated Elo.
        pending = []
        for i in active:
            won = bool(m["teams"][i]["won"])
            opp_paths = [p for j in active if j != i for p in team_paths[j]]
            if not opp_paths:
                continue
            opp_avg = sum(elo[p] for p in opp_paths) / len(opp_paths)
            for path in team_paths[i]:
                delta = K_FACTOR * ((1.0 if won else 0.0) - expected_score(elo[path], opp_avg))
                pending.append((path, elo[path] + delta, won))

        for path, new_rating, won in pending:
            elo[path] = new_rating
            played[path] += 1
            wins[path] += int(won)

    return {
        name: {
            "current_elo": round(elo[path], 1),
            "matches_played": played[path],
            "wins": wins[path],
            "losses": played[path] - wins[path],
            "win_rate": round(wins[path] / played[path] * 100, 1) if played[path] else 0.0,
        }
        for path, name in TRACKED.items()
    }


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return path
