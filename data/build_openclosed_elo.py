"""Rebuild open-map vs closed-map Elo (openclosed_elo.json).

Two independent ladders, one over open maps and one over closed maps, to see who is
actually better at which style of game.
"""
from collections import defaultdict

import ladder_common as lc

# Standard AoE2 community categorization. Not from the site itself - maps not
# confidently "open" (flat/exposed starts) or "closed" (naturally walled-off
# starts) are left out of this classification entirely (water/hybrid/custom/
# unrecorded maps), rather than guessed.
OPEN_MAPS = {
    "Arabia", "Ghost Lake", "Sacred Springs", "Gold Rush", "Golden Pit", "Mongolia",
    "Steppe", "Valley", "Meadow", "Oasis", "Acclivity", "Wolf Hill", "Runestones",
    "Yucatan", "Atacama", "Marketplace", "Salt Marsh", "Hamburger", "Prairie",
    "Serengeti", "Kilimanjaro", "Haboob", "African Clearing", "Shrubland", "Budapest",
    "Acropolis",
}
CLOSED_MAPS = {
    "Arena", "Black Forest", "Fortress", "Hideout", "Land Madness", "Enclosed",
    "Fortified Clearing", "Team Moats", "Moats", "Ring Fortress", "Lombardia",
    "Murkwood", "Golden Swamp", "QS Arena", "QS Black Forest",
    "Rage Arena V4 Custom", "Populationboost Arena Custom",
    "Populationboost Black Forest Custom", "Rage Forest 5 - Official Map Custom",
}


def main():
    all_matches = lc.load_all_matches()
    qualifying, stats = lc.select_qualifying(all_matches)

    by_category = defaultdict(list)
    unclassified_maps = defaultdict(int)
    for m in qualifying:
        if m["map"] in OPEN_MAPS:
            by_category["open"].append(m)
        elif m["map"] in CLOSED_MAPS:
            by_category["closed"].append(m)
        else:
            unclassified_maps[m["map"]] += 1

    print(f"Total qualifying matches: {len(qualifying)}")
    print(f"  excluded - no opposition: {stats['excluded_no_opposition']}, "
          f"short game: {stats['excluded_short_game']}, "
          f"no decidable result: {stats['excluded_no_result']}, "
          f"before start date: {stats['excluded_before_start_date']}")
    print(f"Open-map matches: {len(by_category['open'])}")
    print(f"Closed-map matches: {len(by_category['closed'])}")
    print(f"Unclassified (excluded) matches: {sum(unclassified_maps.values())} "
          f"across {len(unclassified_maps)} maps")

    results = {
        category: {
            "total_matches": len(matches),
            "players": lc.simulate_winloss(matches),
        }
        for category, matches in by_category.items()
    }

    out_path = lc.write_json(
        lc.OPENCLOSED_ELO_PATH,
        {"results": results, "unclassified_maps": dict(unclassified_maps)},
    )
    print(f"\nWrote {out_path}\n")

    for category in ("open", "closed"):
        data = results.get(category)
        if not data:
            continue
        print(f"=== {category.upper()} maps ({data['total_matches']} matches) ===")
        standings = sorted(
            ((name, p) for name, p in data["players"].items() if p["matches_played"] > 0),
            key=lambda kv: kv[1]["current_elo"],
            reverse=True,
        )
        for name, p in standings:
            print(f"  {name:20s} {p['current_elo']:8.1f}  ({p['matches_played']} matches, {p['win_rate']}% win rate)")
        print()

    print("Unclassified maps (excluded from open/closed comparison):")
    for name, count in sorted(unclassified_maps.items(), key=lambda kv: -kv[1]):
        print(f"  {count:4d}  {name}")


if __name__ == "__main__":
    main()
