import random
from itertools import combinations

# Above this many participants the exhaustive search gets expensive (it is
# C(n, n/2) splits: ~185k at 20, ~10M at 26), so switch to a greedy split refined by
# pairwise swaps. Exact below the threshold, which covers any realistic lobby.
EXHAUSTIVE_LIMIT = 20
# Randomised restarts for that fallback. A single strongest-first pass can settle well
# short of the best split; a couple of dozen restarts closes almost all of the gap.
GREEDY_RESTARTS = 24


def _split_diff(team_a, team_b):
    return abs(sum(e for _, e in team_a) - sum(e for _, e in team_b))


def _best_split_exhaustive(working, half):
    indices = range(len(working))
    best_split = None
    best_diff = None
    for combo in combinations(indices, half):
        chosen = set(combo)
        team_a = [working[i] for i in combo]
        team_b = [working[i] for i in indices if i not in chosen]
        diff = _split_diff(team_a, team_b)
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best_split = (team_a, team_b)
    return best_split, best_diff


def _fill(order, half, total):
    """Deal players out in the given order, always to the currently weaker team."""
    team_a, team_b = [], []
    for player in order:
        if len(team_a) == half:
            team_b.append(player)
        elif len(team_b) == total - half:
            team_a.append(player)
        elif sum(e for _, e in team_a) <= sum(e for _, e in team_b):
            team_a.append(player)
        else:
            team_b.append(player)
    return team_a, team_b


def _improve(team_a, team_b):
    """Swap pairs across the two teams while that narrows the gap."""
    best_diff = _split_diff(team_a, team_b)
    improved = True
    while improved:
        improved = False
        for i in range(len(team_a)):
            for j in range(len(team_b)):
                team_a[i], team_b[j] = team_b[j], team_a[i]
                diff = _split_diff(team_a, team_b)
                if diff < best_diff:
                    best_diff = diff
                    improved = True
                else:
                    team_a[i], team_b[j] = team_b[j], team_a[i]
    return best_diff


def _best_split_greedy(working, half, restarts=GREEDY_RESTARTS):
    """Strongest-first split refined by pairwise swaps, repeated from random starts.

    Approximate where the exhaustive search is exact, but the restarts keep the residual
    imbalance to a fraction of an Elo point per player - far below the noise in the
    ratings themselves.
    """
    # Fixed seed: the same lobby should always produce the same teams.
    rng = random.Random(0xA0E2)
    total = len(working)
    best_split = None
    best_diff = None
    for attempt in range(restarts + 1):
        order = sorted(working, key=lambda kv: -kv[1]) if attempt == 0 else rng.sample(working, total)
        team_a, team_b = _fill(order, half, total)
        diff = _improve(team_a, team_b)
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best_split = (team_a, team_b)
            if best_diff == 0:
                break
    return best_split, best_diff


def balance_teams(players, ai_elo=None):
    """Split (name, elo) pairs into the two most evenly matched teams.

    An odd number of players gets a filler "AI" entry so the teams come out the same
    size; its rating defaults to the mean of the given players, keeping it neutral rather
    than accidentally the best or worst player present. Pass `ai_elo` to override.

    Returns dict with team_a, team_b, avg_a, avg_b, elo_diff, ai_added, ai_team.
    """
    working = list(players)
    if not working:
        raise ValueError("balance_teams needs at least one player")

    ai_added = False
    if len(working) % 2 == 1:
        if ai_elo is None:
            # The mean, so the filler is neutral on whatever scale the ladder currently
            # sits at. It used to be pinned at 1000, which silently made it stronger than
            # every human on the board once the ladder drifted below that.
            ai_elo = sum(e for _, e in working) / len(working)
        working.append(("AI", float(ai_elo)))
        ai_added = True

    half = len(working) // 2
    if len(working) <= EXHAUSTIVE_LIMIT:
        (team_a, team_b), best_diff = _best_split_exhaustive(working, half)
    else:
        (team_a, team_b), best_diff = _best_split_greedy(working, half)

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
