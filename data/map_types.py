"""Single source of truth for open / closed map classification.

Used by build_openclosed_elo.py (the open-vs-closed ladders) and by
build_unranked_ladder.py (which picks the performance-ratio reference time by
map type). Keep additions here - there must never be a second copy.

Standard AoE2 community categorisation, not something the site reports:
  OPEN   - flat or exposed starts, early aggression is viable.
  CLOSED - naturally walled or forest-sealed starts, games go long.
Maps that are not confidently one or the other (water, hybrid, most customs,
and the site's "Unknown") are deliberately left out rather than guessed; they
are reported as unclassified by build_openclosed_elo.py.
"""

OPEN_MAPS = {
    "Arabia", "Ghost Lake", "Sacred Springs", "Gold Rush", "Golden Pit", "Mongolia",
    "Steppe", "Valley", "Meadow", "Oasis", "Acclivity", "Wolf Hill", "Runestones",
    "Yucatan", "Atacama", "Marketplace", "Salt Marsh", "Hamburger", "Prairie",
    "Serengeti", "Kilimanjaro", "Haboob", "African Clearing", "Shrubland", "Budapest",
    "Acropolis", "Four Lakes",
}

CLOSED_MAPS = {
    "Arena", "Black Forest", "Fortress", "Hideout", "Land Madness", "Enclosed",
    "Fortified Clearing", "Team Moats", "Moats", "Ring Fortress", "Lombardia",
    "Murkwood", "Golden Swamp", "QS Arena", "QS Black Forest",
    "Rage Arena V4 Custom", "Populationboost Arena Custom",
    "Populationboost Black Forest Custom", "Rage Forest 5 - Official Map Custom",
    "Amazon Tunnel", "Hill Fort", "Michi",
}

assert not (OPEN_MAPS & CLOSED_MAPS), f"map classified both ways: {OPEN_MAPS & CLOSED_MAPS}"


def map_type(name):
    """'open', 'closed', or None if unclassified."""
    if name in CLOSED_MAPS:
        return "closed"
    if name in OPEN_MAPS:
        return "open"
    return None
