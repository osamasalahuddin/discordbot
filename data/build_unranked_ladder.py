"""Rebuild the main unranked group ladder (unranked_ladder.json).

Shared configuration, path resolution, the qualifying filter and the Elo primitives all
come from ladder_common.py.
"""
import json
from datetime import datetime, timezone

import ladder_common as lc
from ladder_common import K_FACTOR, STARTING_ELO, TRACKED

# A player needs performance data for themselves and at least one opponent before the
# flat win/loss score is replaced by a performance-weighted one.
_perf_db = lc.load_performance_db()


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


def main():
    all_matches = lc.load_all_matches()
    print(f"Total unique unranked matches (union across {len(TRACKED)} players): {len(all_matches)}")

    qualifying, stats = lc.select_qualifying(all_matches)
    print(f"Excluded (only one side has tracked players / no real opposition): {stats['excluded_no_opposition']}")
    print(f"Excluded (game duration < 15min, regardless of AI presence): {stats['excluded_short_game']}")
    print(f"Excluded (no decidable win/loss result): {stats['excluded_no_result']}")
    print(f"Excluded (before ladder start date {lc.LADDER_START_DATE.date()}): {stats['excluded_before_start_date']}")
    print(f"Matches used for ladder simulation: {len(qualifying)}")

    elo = {path: float(STARTING_ELO) for path in TRACKED}
    history = {path: [] for path in TRACKED}
    match_log = []

    for m in qualifying:
        team_paths = lc.tracked_paths_by_team(m)
        active = [i for i, paths in enumerate(team_paths) if paths]

        # Collect every update against the pre-match ratings, then apply them together,
        # so within one match nobody is rated against a teammate's already-updated Elo.
        pending = []
        for i in active:
            won = bool(m["teams"][i]["won"])
            # Opponent = average Elo of tracked players on all OTHER active teams.
            # Untracked players in the lobby are ignored for rating purposes.
            opp_paths = [p for j in active if j != i for p in team_paths[j]]
            if not opp_paths:
                continue
            opp_avg = sum(elo[p] for p in opp_paths) / len(opp_paths)

            for path in team_paths[i]:
                own_rating = elo[path]
                expected = lc.expected_score(own_rating, opp_avg)

                perf_ratio = compute_performance_ratio(m["match_id"], path, opp_paths)
                if perf_ratio is not None:
                    margin = max(-1.0, min(1.0, (perf_ratio - 0.5) / 0.5))
                    margin_effective = max(margin, 0.0) if won else min(margin, 0.0)
                    actual = 0.5 + 0.5 * margin_effective
                else:
                    actual = 1.0 if won else 0.0

                delta = K_FACTOR * (actual - expected)
                pending.append(
                    (path, own_rating, own_rating + delta, delta, won, opp_avg, perf_ratio, actual)
                )

        if not pending:
            continue

        match_date = lc.match_datetime(m)
        for path, old_r, new_r, delta, won, opp_avg, perf_ratio, actual in pending:
            elo[path] = new_r
            history[path].append({
                "match_id": m["match_id"],
                "date": match_date.isoformat(),
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
            "date": match_date.isoformat(),
            "map": m["map"],
            "duration_seconds": lc.parse_duration(m["duration"]),
            "had_ai": any(p["is_ai"] for team in m["teams"] for p in team["players"]),
            "teams": [
                {
                    "won": team["won"],
                    "players": [
                        {"name": p["name"], "user_path": p["user_path"], "civ": p["civ"],
                         "is_ai": p["is_ai"], "tracked": p["user_path"] in TRACKED}
                        for p in team["players"]
                    ],
                }
                for team in m["teams"]
            ],
        })

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
            "short_game_exclusion_threshold_seconds": lc.SHORT_GAME_THRESHOLD_SECONDS,
            "ladder_start_date": lc.LADDER_START_DATE.date().isoformat(),
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
                "players in the match are ignored for rating purposes). This filtering only affects "
                "which matches feed the Elo calculation here - the underlying scraped datasets in "
                "unranked_raw/ are never modified. Any match under 15 minutes is excluded regardless "
                "of whether an AI was present, since it's too short to be a conclusive result. Longer "
                "matches with an AI player present use the recorded win/loss as-is, since AoE2 games "
                "resolve via resignation and the AI side cannot resign, so the recorded result already "
                "reflects who resigned first. Matches whose winner cannot be established - the site "
                "rendered no winning team and the per-player rating changes don't agree on one - are "
                "excluded rather than scored as a loss for everyone involved."
            ),
        },
        "scrape_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_unique_unranked_matches_scanned": len(all_matches),
            "qualifying_matches_used": len(qualifying),
            "excluded_no_opposition": stats["excluded_no_opposition"],
            "excluded_short_game": stats["excluded_short_game"],
            "excluded_short_game_match_ids": stats["excluded_short_game_match_ids"],
            "excluded_no_result": stats["excluded_no_result"],
            "excluded_no_result_match_ids": stats["excluded_no_result_match_ids"],
            "excluded_before_start_date": stats["excluded_before_start_date"],
        },
        "players": players_out,
        "match_log": match_log,
    }

    out_path = lc.write_json(lc.LADDER_PATH, result)
    print(f"\nWrote {out_path}")

    total = sum(p["current_elo"] for p in players_out.values())
    print("\nFinal Elo standings:")
    for name, p in sorted(players_out.items(), key=lambda kv: kv[1]["current_elo"], reverse=True):
        print(f"  {name:20s} {p['current_elo']:8.1f}  ({p['matches_played']} matches, {p['win_rate']}% win rate)")
    # Elo is zero-sum, so this should stay close to players * starting_elo. A large gap
    # means results are being scored asymmetrically somewhere.
    print(f"\nElo total: {total:.0f} (expected ~{STARTING_ELO * len(TRACKED)})")


if __name__ == "__main__":
    main()
