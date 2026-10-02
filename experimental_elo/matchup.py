"""Query the experimental probability ladder (probability_ladder.json).

    python matchup.py                                  full head-to-head table
    python matchup.py toXic                            one player vs everyone
    python matchup.py toXic,neXus vs wabbit,zubair     team vs team

Names are case-insensitive and can be shortened to any unique prefix
("str" -> Strength & Honour). Quote names that contain spaces.
Team probability uses the same rule as the ladder: the mean log-odds over every
cross-team pair, converted back to a probability.
"""
import json
import math
import os
import sys

LADDER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probability_ladder.json")


def load():
    if not os.path.exists(LADDER_PATH):
        sys.exit("probability_ladder.json not found - run build_probability_ladder.py first.")
    with open(LADDER_PATH, encoding="utf-8") as f:
        return json.load(f)


def resolve(name, players):
    key = name.strip().lower()
    exact = [p for p in players if p.lower() == key]
    if exact:
        return exact[0]
    hits = [p for p in players if p.lower().startswith(key)]
    if len(hits) == 1:
        return hits[0]
    sys.exit(f"'{name}' matches {hits or 'no player'}. Players: {', '.join(players)}")


def pair_prob(h2h, a, b):
    return h2h[a][b]["win_probability"] / 100


def team_prob(h2h, team_a, team_b):
    def logit(p):
        p = min(max(p, 1e-6), 1 - 1e-6)
        return math.log(p / (1 - p))
    logits = [logit(pair_prob(h2h, a, b)) for a in team_a for b in team_b]
    return 1 / (1 + math.exp(-sum(logits) / len(logits)))


def show_table(data):
    players = data["ladder"]
    h2h = data["head_to_head"]
    print("Win probability, row beats column (%):\n")
    print(" " * 20 + "".join(f"{p[:7]:>8}" for p in players))
    for a in players:
        cells = "".join("       -" if a == b else f"{h2h[a][b]['win_probability']:8.1f}" for b in players)
        print(f"{a:20s}{cells}")


def show_player(data, name):
    h2h = data["head_to_head"]
    p = data["players"][name]
    print(f"{name}  (rank {p['rank']}, {p['expected_win_rate']}% vs the field, "
          f"{p['wins']}-{p['losses']} overall)\n")
    print(f"  {'opponent':20s} {'win %':>6}  {'games*':>7}  {'record win %':>12}  {'transitive %':>12}")
    for opp, v in sorted(h2h[name].items(), key=lambda kv: -kv[1]["win_probability"]):
        g = v["weighted_games"]
        raw = f"{v['weighted_wins'] / g * 100:11.1f}%" if g else "           -"
        print(f"  {opp:20s} {v['win_probability']:5.1f}%  {g:7.2f}  {raw}  {v['transitive_estimate']:11.1f}%")
    print("\n  * weighted: a 1v1 counts 1, bigger games count less per opponent, older games decay.")


def show_teams(data, team_a, team_b):
    h2h = data["head_to_head"]
    overlap = set(team_a) & set(team_b)
    if overlap:
        sys.exit(f"{', '.join(overlap)} can't be on both teams.")
    p = team_prob(h2h, team_a, team_b)
    print(f"{' + '.join(team_a)}  vs  {' + '.join(team_b)}\n")
    print(f"  {' + '.join(team_a)}: {p * 100:5.1f}%")
    print(f"  {' + '.join(team_b)}: {(1 - p) * 100:5.1f}%\n")
    print("  Pairs behind it (left team player beats right team player):")
    for a in team_a:
        for b in team_b:
            print(f"    {a:20s} vs {b:20s} {pair_prob(h2h, a, b) * 100:5.1f}%")


def main():
    data = load()
    players = data["ladder"]
    args = sys.argv[1:]
    if not args:
        show_table(data)
        return
    text = " ".join(args)
    if " vs " in f" {text.lower()} ":
        idx = [a.lower() for a in args].index("vs")
        side = lambda xs: [resolve(n, players) for n in ",".join(xs).split(",") if n.strip()]
        team_a, team_b = side(args[:idx]), side(args[idx + 1:])
        if not team_a or not team_b:
            sys.exit("Usage: matchup.py playerA,playerB vs playerC,playerD")
        show_teams(data, team_a, team_b)
    else:
        show_player(data, resolve(text, players))


if __name__ == "__main__":
    main()
