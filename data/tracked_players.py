"""Loads the tracked players from players.json - the single source of truth.

Used by every ladder builder (build_unranked_ladder.py, build_map_elo.py,
build_openclosed_elo.py, ../experimental_elo/build_probability_ladder.py), by
get_known_match_ids.py, and by fetch_incremental_update.py, which pushes the
list into incremental_update.js so the scraper follows the same players.

players.json is data, not code, so the Discord bot's /addplayer can append to
it (it commits and pushes the change; fetch_incremental_update.py pulls before
every run). It can also be edited by hand. Each entry:
  id          aoe2insights profile id - the number in /user/<id>/
  name        display name in the ladder and the Discord bot
  raw_file    full unranked-history scrape in unranked_raw/; absent for a player
              added later, whose matches arrive through the incremental update
  since       optional "YYYY-MM-DD" the player's ladder starts on. Before it they
              are treated exactly as an untracked player, so adding someone never
              rewrites anyone's earlier history; their Elo starts at the starting
              value on this date. Omit to count every match from the ladder start.
  discord_id, discord_name, added_by, added_at
              optional bookkeeping written by /addplayer; ignored by the builders

No backfill scrape is needed for a new player - a match only counts when
another tracked player is on the opposing side, and every tracked player's
match list is already scraped.
"""
import json
import os
from datetime import datetime, timezone

PLAYERS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "players.json")

with open(PLAYERS_PATH, encoding="utf-8") as _f:
    PLAYERS = json.load(_f)["players"]

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
