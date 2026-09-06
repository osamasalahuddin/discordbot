"""Rebuild per-map Elo (map_elo.json).

Each map gets its own independent ladder, every player restarting from STARTING_ELO, so a
map Elo is only comparable with other Elos on the same map. The bot rescales them onto the
overall ladder before using them to balance teams (see code/ladder_data.py).

Deliberately win/loss only - the performance-ratio weighting is applied to the main ladder
alone, since per-map samples are far too small to carry it.
"""
from collections import defaultdict

import ladder_common as lc


def main():
    all_matches = lc.load_all_matches()
    qualifying, stats = lc.select_qualifying(all_matches)

    print(f"Total qualifying matches (same filter as main ladder): {len(qualifying)}")
    print(f"  excluded - no opposition: {stats['excluded_no_opposition']}, "
          f"short game: {stats['excluded_short_game']}, "
          f"no decidable result: {stats['excluded_no_result']}, "
          f"before start date: {stats['excluded_before_start_date']}")

    by_map = defaultdict(list)
    for m in qualifying:
        by_map[m["map"]].append(m)
    print(f"Distinct maps: {len(by_map)}")

    map_results = {
        map_name: {
            "total_matches": len(matches),
            "players": lc.simulate_winloss(matches),
        }
        for map_name, matches in by_map.items()
    }

    out_path = lc.write_json(lc.MAP_ELO_PATH, map_results)
    print(f"\nWrote {out_path}")

    top_maps = sorted(map_results.items(), key=lambda kv: kv[1]["total_matches"], reverse=True)[:5]
    print("\nTop 5 most played maps:")
    for map_name, data in top_maps:
        print(f"\n=== {map_name} ({data['total_matches']} matches) ===")
        standings = sorted(
            ((name, p) for name, p in data["players"].items() if p["matches_played"] > 0),
            key=lambda kv: kv[1]["current_elo"],
            reverse=True,
        )
        for name, p in standings:
            print(f"  {name:20s} {p['current_elo']:8.1f}  ({p['matches_played']} matches, {p['win_rate']}% win rate)")


if __name__ == "__main__":
    main()
