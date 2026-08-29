from itertools import combinations

AI_ELO = 1000.0


def balance_teams(players):
    """players: list of (name, elo). Returns dict with team_a, team_b, avg_a, avg_b, elo_diff, ai_added, ai_team."""
    working = list(players)
    ai_added = False
    if len(working) % 2 == 1:
        working.append(("AI", AI_ELO))
        ai_added = True

    half = len(working) // 2
    indices = list(range(len(working)))

    best_split = None
    best_diff = None
    for combo in combinations(indices, half):
        team_a = [working[i] for i in combo]
        team_b = [working[i] for i in indices if i not in combo]
        diff = abs(sum(e for _, e in team_a) - sum(e for _, e in team_b))
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best_split = (team_a, team_b)

    team_a, team_b = best_split
    ai_team = None
    if ai_added:
        ai_team = "A" if any(name == "AI" for name, _ in team_a) else "B"

    return {
        "team_a": team_a,
        "team_b": team_b,
        "avg_a": sum(e for _, e in team_a) / len(team_a),
        "avg_b": sum(e for _, e in team_b) / len(team_b),
        "elo_diff": best_diff,
        "ai_added": ai_added,
        "ai_team": ai_team,
    }
