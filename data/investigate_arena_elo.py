"""Diagnostic: why two players' Arena Elo diverged.

Replays the Arena-only ladder logging per-match detail for cheetah001 and SauronSlayer,
plus their head-to-head record. Uses the same filter and Elo maths as the real build via
ladder_common, so its numbers line up with map_elo.json.
"""
import ladder_common as lc
from ladder_common import K_FACTOR, STARTING_ELO, TRACKED

MAP_NAME = "Arena"
CHEETAH = "/user/11907023/"
SAURON = "/user/12676944/"


def main():
    all_matches = {
        mid: m for mid, m in lc.load_all_matches().items() if m["map"] == MAP_NAME
    }
    qualifying, _ = lc.select_qualifying(all_matches)
    print(f"{MAP_NAME} qualifying matches: {len(qualifying)}\n")

    elo = {path: float(STARTING_ELO) for path in TRACKED}
    log = {CHEETAH: [], SAURON: []}
    head_to_head = {"cheetah_wins": 0, "sauron_wins": 0, "total": 0}

    for m in qualifying:
        team_paths = lc.tracked_paths_by_team(m)
        active = [i for i, paths in enumerate(team_paths) if paths]

        cheetah_team = next((i for i in active if CHEETAH in team_paths[i]), None)
        sauron_team = next((i for i in active if SAURON in team_paths[i]), None)
        if cheetah_team is not None and sauron_team is not None and cheetah_team != sauron_team:
            head_to_head["total"] += 1
            if m["teams"][cheetah_team]["won"]:
                head_to_head["cheetah_wins"] += 1
            else:
                head_to_head["sauron_wins"] += 1

        pending = []
        for i in active:
            won = bool(m["teams"][i]["won"])
            opp_paths = [p for j in active if j != i for p in team_paths[j]]
            if not opp_paths:
                continue
            opp_avg = sum(elo[p] for p in opp_paths) / len(opp_paths)
            for path in team_paths[i]:
                expected = lc.expected_score(elo[path], opp_avg)
                delta = K_FACTOR * ((1.0 if won else 0.0) - expected)
                pending.append((path, elo[path], opp_avg, expected, delta, won))

        for path, old_r, opp_avg, expected, delta, won in pending:
            elo[path] = old_r + delta
            if path in log:
                log[path].append({
                    "match_id": m["match_id"], "date": lc.match_datetime(m).isoformat(), "won": won,
                    "own_elo_before": round(old_r, 1), "opp_avg_elo": round(opp_avg, 1),
                    "expected": round(expected, 3), "delta": round(delta, 2),
                    "elo_after": round(old_r + delta, 1),
                })

    print(f"Head to head ({MAP_NAME}, qualifying matches, cheetah001 vs SauronSlayer on opposing teams):")
    print(head_to_head)
    print()

    for path, name in [(CHEETAH, "cheetah001"), (SAURON, "SauronSlayer")]:
        entries = log[path]
        if not entries:
            print(f"=== {name} on {MAP_NAME} === no qualifying matches\n")
            continue
        wins = [e for e in entries if e["won"]]
        losses = [e for e in entries if not e["won"]]
        avg_opp_when_win = sum(e["opp_avg_elo"] for e in wins) / len(wins) if wins else 0
        avg_opp_when_loss = sum(e["opp_avg_elo"] for e in losses) / len(losses) if losses else 0
        avg_expected_overall = sum(e["expected"] for e in entries) / len(entries)
        total_delta_wins = sum(e["delta"] for e in wins)
        total_delta_losses = sum(e["delta"] for e in losses)
        print(f"=== {name} on {MAP_NAME} ===")
        print(f"  matches: {len(entries)}, wins: {len(wins)} ({len(wins)/len(entries)*100:.1f}%), losses: {len(losses)}")
        print(f"  avg opponent elo when WINNING: {avg_opp_when_win:.1f}")
        print(f"  avg opponent elo when LOSING:  {avg_opp_when_loss:.1f}")
        print(f"  avg pre-match expected score (own perspective): {avg_expected_overall:.3f}  (>0.5 = favored on average)")
        print(f"  total elo gained from wins: {total_delta_wins:+.1f}")
        print(f"  total elo lost from losses: {total_delta_losses:+.1f}")
        print(f"  net elo change: {total_delta_wins+total_delta_losses:+.1f}"
              f"  (starting {STARTING_ELO} -> {STARTING_ELO+total_delta_wins+total_delta_losses:.1f})")
        print()


if __name__ == "__main__":
    main()
