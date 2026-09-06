"""Loading and rating logic behind the bot's commands.

Deliberately free of any discord.py import: this is the part worth testing, and it should
be testable without a Discord library or a token. bot.py holds the presentation.
"""
import json
import os
from pathlib import Path

# Defaults assume the repo layout (data/ is a sibling of code/). Override with the
# DATA_DIR env var, or point at the files directly with LADDER_PATH / MAP_ELO_PATH.
# Resolves from this file's location, so it works regardless of the launch cwd.
DATA_DIR = os.environ.get("DATA_DIR", str(Path(__file__).resolve().parent.parent / "data"))
LADDER_PATH = os.environ.get("LADDER_PATH", os.path.join(DATA_DIR, "unranked_ladder.json"))
MAP_ELO_PATH = os.environ.get("MAP_ELO_PATH", os.path.join(DATA_DIR, "map_elo.json"))

# A player needs at least this many games on a map before that map's Elo is
# trusted. Below the threshold the bot falls back to the player's overall Elo.
FALLBACK_MIN_GAMES = 5

# path -> ((mtime_ns, size), parsed). The data files are rewritten wholesale by a git
# pull, so re-reading only when the file actually changed keeps every command on fresh
# data without re-parsing several MB of JSON per autocomplete keystroke.
_cache = {}


def _load_json(path, default=None):
    """Parse `path`, reusing the previous parse while the file is unchanged.

    The returned object is shared between callers - treat it as read-only.
    """
    try:
        stat = os.stat(path)
    except FileNotFoundError:
        if default is not None:
            return default
        raise
    stamp = (stat.st_mtime_ns, stat.st_size)
    cached = _cache.get(path)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    _cache[path] = (stamp, data)
    return data


def load_ladder():
    return _load_json(LADDER_PATH)


def load_players():
    """name -> overall Elo."""
    return {name: p["current_elo"] for name, p in load_ladder()["players"].items()}


def load_map_elo():
    return _load_json(MAP_ELO_PATH, default={})


def map_names_by_popularity():
    """All maps with Elo data, most-played first. 'Unknown' is dropped."""
    data = load_map_elo()
    maps = [(name, v.get("total_matches", 0)) for name, v in data.items() if name != "Unknown"]
    maps.sort(key=lambda kv: -kv[1])
    return [name for name, _ in maps]


def resolve_map_name(map_elo_data, query):
    """The stored map key matching `query` case-insensitively, or None."""
    if not query:
        return None
    return next((k for k in map_elo_data if k.lower() == query.lower()), None)


def resolve_player_name(players, query):
    """The stored player key matching `query` case-insensitively, or None."""
    if not query:
        return None
    return next((k for k in players if k.lower() == query.lower()), None)


def map_games_played(map_elo_data, map_name, name):
    return (
        map_elo_data.get(map_name, {})
        .get("players", {})
        .get(name, {})
        .get("matches_played", 0)
    )


def map_adjusted_elos(overall, map_name, map_elo_data, min_games=FALLBACK_MIN_GAMES):
    """Rate `overall` (name -> overall Elo) using per-map Elo, on one shared scale.

    Every per-map ladder restarts each player from STARTING_ELO and then drifts on its own
    small sample, so a raw map Elo is not comparable with an overall Elo - on this data the
    two sit 100-170 points apart. Mixing them directly, as the bot used to, made whoever
    fell back to their overall Elo look far weaker than they are and skewed the balance.

    So: players with at least `min_games` on the map keep their map-relative spread but are
    shifted as a group onto the overall scale, by matching the group's map-Elo mean to that
    same group's overall-Elo mean. Everyone else keeps their overall Elo unchanged.

    Returns name -> (elo, used_map_elo).
    """
    if not map_name:
        return {name: (elo, False) for name, elo in overall.items()}

    entries = map_elo_data.get(map_name, {}).get("players", {})
    qualified = [
        name for name in overall
        if entries.get(name, {}).get("matches_played", 0) >= min_games
    ]
    if not qualified:
        return {name: (elo, False) for name, elo in overall.items()}

    mean_overall = sum(overall[name] for name in qualified) / len(qualified)
    mean_map = sum(entries[name]["current_elo"] for name in qualified) / len(qualified)
    shift = mean_overall - mean_map

    qualified_set = set(qualified)
    return {
        name: (entries[name]["current_elo"] + shift, True) if name in qualified_set
        else (elo, False)
        for name, elo in overall.items()
    }


def fallback_names(rated, names, map_name):
    """Which of `names` are rated on overall Elo rather than the map's.

    Empty without a map: everyone is on overall Elo then, so flagging them all as
    fallbacks tells the reader nothing.
    """
    if not map_name:
        return []
    return [name for name in names if not rated[name][1]]


def player_map_history(ladder, name, map_name):
    """A tracked player's per-match ladder history on one map, oldest first."""
    entries = ladder["players"].get(name, {}).get("history", [])
    return [h for h in entries if h.get("map") == map_name]


def top_civs(ladder, user_path, map_name, limit=3):
    """[(civ label, games)] for a player on a map, most-played first.

    Civ is null on a handful of scraped rows, so it's labelled rather than dropped -
    formatting it blindly used to crash /playerstats.
    """
    counts = {}
    if not user_path:
        return []
    for match in ladder.get("match_log", []):
        if match.get("map") != map_name:
            continue
        for team in match["teams"]:
            for player in team["players"]:
                if player.get("user_path") == user_path:
                    civ = player.get("civ")
                    label = civ.title() if civ else "Unknown"
                    counts[label] = counts.get(label, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])[:limit]


def map_standings(map_elo_data, map_name):
    """[(name, map elo)] for players with games on the map, strongest first."""
    players = map_elo_data.get(map_name, {}).get("players", {})
    return sorted(
        ((name, entry["current_elo"]) for name, entry in players.items()
         if entry.get("matches_played", 0) > 0),
        key=lambda kv: -kv[1],
    )
