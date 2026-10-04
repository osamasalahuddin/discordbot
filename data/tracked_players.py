"""Single source of truth for the tracked players.

Used by every ladder builder (build_unranked_ladder.py, build_map_elo.py,
build_openclosed_elo.py, ../experimental_elo/build_probability_ladder.py), by
get_known_match_ids.py, and by fetch_incremental_update.py, which pushes this
list into incremental_update.js so the scraper follows the same players. Add
or change players here only - there must never be a second copy.

Each entry:
  id        aoe2insights profile id - the number in /user/<id>/
  name      display name in the ladder and the Discord bot
  raw_file  full unranked-history scrape in unranked_raw/, or None for a player
            added later whose matches arrive only through the incremental update
  since     optional "YYYY-MM-DD" the player's ladder starts on. Before it they
            are treated exactly as an untracked player, so adding someone never
            rewrites anyone's earlier history; their Elo starts at the starting
            value on this date. Omit to count every match from the ladder start.

To add a player: append an entry with a `since` date and rerun the builders
(run_full_refresh.py). No backfill scrape is needed - a match only counts when
another tracked player is on the opposing side, and every tracked player's
match list is already scraped.
"""
from datetime import datetime, timezone

PLAYERS = [
    {"id": 12047120, "name": "wabbit",            "raw_file": "unranked_wabbit.json"},
    {"id": 12676944, "name": "SauronSlayer",      "raw_file": "unranked_SauronSlayer.json"},
    {"id": 12667372, "name": "zubair",            "raw_file": "unranked_zubair.json"},
    {"id": 12080589, "name": "l.inc",             "raw_file": "unranked_l.inc.json"},
    {"id": 12499000, "name": "toXic",             "raw_file": "unranked_toXic.json"},
    {"id": 4607974,  "name": "Strength & Honour", "raw_file": "unranked_StrengthHonour.json"},
    {"id": 11907023, "name": "cheetah001",        "raw_file": "unranked_cheetah001.json"},
    {"id": 12693189, "name": "NaKiyaKar",         "raw_file": "unranked_NaKiyaKar.json"},
    {"id": 12805097, "name": "neXus",             "raw_file": "unranked_neXus.json"},
    {"id": 13194886, "name": "Ck",                "raw_file": None, "since": "2026-10-03"},
]

# Every incremental update merges into this one; always read alongside the
# per-player scrapes.
INCREMENTAL_RAW_FILE = "incremental_new_matches.json"


def _path(profile_id):
    return f"/user/{profile_id}/"


TRACKED = {_path(p["id"]): p["name"] for p in PLAYERS}

TRACKED_SINCE = {
    _path(p["id"]): datetime.strptime(p["since"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    for p in PLAYERS
    if p.get("since")
}

RAW_FILES = [p["raw_file"] for p in PLAYERS if p.get("raw_file")] + [INCREMENTAL_RAW_FILE]

# {profile_id: name} as incremental_update.js expects it.
TRACKED_USERS_JS = {str(p["id"]): p["name"] for p in PLAYERS}

assert len(TRACKED) == len(PLAYERS), "duplicate profile id in PLAYERS"
assert len(set(TRACKED.values())) == len(PLAYERS), "duplicate name in PLAYERS"


def is_tracked(user_path, when):
    """Whether user_path counts as a tracked player in a match played at `when`.

    `when` is the match's UTC datetime, or None if it couldn't be parsed. An
    undated match never counts for a player who has a start date.
    """
    if user_path not in TRACKED:
        return False
    since = TRACKED_SINCE.get(user_path)
    return since is None or (when is not None and when >= since)


def since_date(user_path):
    """ISO date a player's ladder starts on, or None if it's the ladder start."""
    since = TRACKED_SINCE.get(user_path)
    return since.date().isoformat() if since else None
