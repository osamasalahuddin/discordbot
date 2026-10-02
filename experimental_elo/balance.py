"""Experimental team balancer: Elo split first, then checked against the matchup map.

    python balance.py toXic neXus str sauron nak l.inc zubair cheetah
    python balance.py "toXic, neXus, wabbit, zubair"

1. ELO SPLIT - exactly what the bot's /balance does today: code/team_balancer.py
   on each player's current_elo from data/unranked_ladder.json (odd counts get an
   AI at 1000 Elo on the lower-rated team).
2. CHECK - score that split with the head-to-head probability map
   (probability_ladder.json): P(team A wins) from every cross-team pair, the
   same rule as matchup.py.
3. ADJUST - try every other split of the same players (same team sizes, AI
   included) and find the one whose win probability is closest to 50%. If the Elo
   split is already within KEEP_TOLERANCE of that best achievable balance it is
   kept, so teams aren't reshuffled over a rounding difference. Otherwise the
   more balanced split is recommended, with the players that moved listed.

The AI has no head-to-head record, so the map treats it as an average player:
50% against everyone, which matches the 1000 Elo the Elo balancer gives it.

Experimental only: nothing here is used by the bot.
"""
import json
import math
import os
import sys
from itertools import combinations

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "code"))
from team_balancer import balance_teams, AI_ELO  # the bot's own Elo balancer, unchanged
from matchup import load, resolve

DEFAULT_LADDER_PATH = os.path.join(_ROOT, "data", "unranked_ladder.json")

# Keep the Elo split if it is within this many percentage points of the most
# balanced split the matchup map can find.
KEEP_TOLERANCE = 1.0
AI = "AI"


def logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def team_prob(h2h, team_a, team_b):
    """P(team_a beats team_b); any pair involving the AI counts as 50/50."""
    logits = [
        0.0 if AI in (a, b) else logit(h2h[a][b]["win_probability"] / 100)
        for a in team_a for b in team_b
    ]
    return 1 / (1 + math.exp(-sum(logits) / len(logits)))


def all_splits(players):
    """Every way to split players into two halves, each split once."""
    first, rest = players[0], players[1:]
    half = len(players) // 2
    for mates in combinations(rest, half - 1):
        team_a = [first, *mates]
        yield team_a, [p for p in rest if p not in mates]


def main():
    args = " ".join(sys.argv[1:]).replace(",", " ").split()
    if len(args) < 2:
        sys.exit(__doc__)

    data = load()
    h2h = data["head_to_head"]
    names = data["ladder"]
    chosen = []
    for a in args:
        n = resolve(a, names)
        if n in chosen:
            sys.exit(f"{n} was picked twice.")
        chosen.append(n)

    with open(DEFAULT_LADDER_PATH, encoding="utf-8") as f:
        elo = {n: p["current_elo"] for n, p in json.load(f)["players"].items()}
    elo[AI] = AI_ELO

    # ---- 1. Elo split (the bot's balancer) ----
    res = balance_teams([(n, elo[n]) for n in chosen])
    elo_a = [n for n, _ in res["team_a"]]
    elo_b = [n for n, _ in res["team_b"]]

    # ---- 2. check it against the matchup map ----
    p_elo = team_prob(h2h, elo_a, elo_b)

    # ---- 3. search every split for the most even matchup ----
    pool = elo_a + elo_b
    scored = []
    for a, b in all_splits(pool):
        p = team_prob(h2h, a, b)
        gap = abs(sum(elo[x] for x in a) - sum(elo[x] for x in b))
        scored.append((abs(p - 0.5), gap, a, b, p))
    scored.sort(key=lambda s: (s[0], s[1]))
    best_dev, best_gap, best_a, best_b, best_p = scored[0]

    elo_dev = abs(p_elo - 0.5)
    keep = (elo_dev - best_dev) * 100 <= KEEP_TOLERANCE

    def show(title, a, b, p):
        ga = sum(elo[x] for x in a)
        gb = sum(elo[x] for x in b)
        print(f"{title}")
        print(f"  Team A  {p * 100:5.1f}%   " + ", ".join(f"{x} ({elo[x]:.0f})" for x in a))
        print(f"  Team B  {(1 - p) * 100:5.1f}%   " + ", ".join(f"{x} ({elo[x]:.0f})" for x in b))
        print(f"  Elo gap {abs(ga - gb):.0f} (team totals {ga:.0f} vs {gb:.0f})\n")

    if res["ai_added"]:
        print("Odd number of players: AI added (1000 Elo, 50% vs everyone in the matchup map).\n")
    show("1. Elo split (what /balance gives today)", elo_a, elo_b, p_elo)

    if keep:
        why = ("it is already the most balanced split"
               if elo_dev == best_dev else
               f"the best alternative is only {(elo_dev - best_dev) * 100:.1f} points closer to 50/50")
        print(f"2. Matchup check: Elo split is balanced - keeping it ({why}).")
        return

    # Orient the recommended teams to overlap the Elo split as much as possible,
    # so "moved" lists the minimum number of players.
    if len(set(best_a) & set(elo_a)) < len(set(best_b) & set(elo_a)):
        best_a, best_b, best_p = best_b, best_a, 1 - best_p
    show("2. Matchup-balanced split (recommended)", best_a, best_b, best_p)
    to_b = [x for x in elo_a if x in best_b]
    to_a = [x for x in elo_b if x in best_a]
    print(f"Adjustment: swap {', '.join(to_b)}  <->  {', '.join(to_a)}")
    print(f"  moves the matchup from {p_elo * 100:.1f}/{(1 - p_elo) * 100:.1f} "
          f"to {best_p * 100:.1f}/{(1 - best_p) * 100:.1f}")


if __name__ == "__main__":
    main()
