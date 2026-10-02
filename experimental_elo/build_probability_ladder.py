"""Experimental ladder: head-to-head win-probability map instead of a running Elo.

The default ladder (data/build_unranked_ladder.py) walks the matches in order and
nudges one number per player. This one never keeps a running number. Instead:

1. HEAD-TO-HEAD MAP
   Every match is broken into the pairs of tracked players who were on opposite
   sides. A 2v2 of A+B beating C+D records A>C, A>D, B>C, B>D. Each pair is
   weighted 2 / (tracked players on side 1 + tracked players on side 2), so a
   player's opponents in one match share a total weight of 1: a 1v1 counts
   fully, and a 4v4 doesn't count 16x more than a 1v1.

2. PROBABILITY MAP  P[i][j] = chance i beats j
   Raw head-to-head records are noisy (3-0 does not mean 100%) and some pairs have
   never met. So each pair is shrunk toward a Bradley-Terry estimate fitted on ALL
   the pairs. Bradley-Terry is the transitive guess: if A usually beats B and B
   usually beats C, A probably beats C.
       P[i][j] = (wins_ij + K * BT_ij) / (games_ij + K)        K = PRIOR_GAMES
   Pairs with lots of games follow their actual record, including match-ups that
   break transitivity. Pairs with few games fall back to the transitive guess.

3. TEAM PROBABILITY
   P(team A beats team B) = sigmoid(mean over a in A, b in B of logit P[a][b]).
   For 1v1 this is exactly P[a][b], and P(A beats B) + P(B beats A) = 1.

4. LADDER
   For every player and every format (1v1 .. 4v4), enumerate EVERY possible
   line-up of the tracked players that includes them, and average their team's
   win probability. The overall score weights the formats by how often each one
   is actually played in the data. It is shown as a win % against the field and
   as an Elo-equivalent number (1000 = an average player) so it can sit next to
   the default ladder.

Uses the same match filter and result repair as the default ladder, so both
ladders are built from identical matches. Writes probability_ladder.json next to
this script and never touches the default outputs.

Usage:
    python build_probability_ladder.py [--prior-games K] [--half-life-days D]

Normally triggered by  fetch_incremental_update.py --experimental-elo  (or
run_full_refresh.py --experimental-elo to rebuild without fetching).
"""
import argparse
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import combinations

_HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(_HERE), "data")
if DATA_DIR not in sys.path:
    sys.path.insert(0, DATA_DIR)
from match_results import resolve_match_results

_parser = argparse.ArgumentParser(description="Build the experimental probability ladder.")
_parser.add_argument(
    "--prior-games", type=float, default=float(os.environ.get("PRIOR_GAMES", "4")),
    help="how many games' worth of weight the transitive Bradley-Terry estimate gets "
         "when smoothing each head-to-head record (default 4)",
)
_parser.add_argument(
    "--half-life-days", type=float, default=180,
    help="weight older matches down, halving every D days (default 180; 0 = all matches "
         "equal). 180 scored best in the walk-forward check.",
)
_parser.add_argument("--out", default=None, help="override the output path")
_args, _ = _parser.parse_known_args()

PRIOR_GAMES = _args.prior_games
HALF_LIFE_DAYS = _args.half_life_days
OUT_PATH = _args.out or os.path.join(_HERE, "probability_ladder.json")
DEFAULT_LADDER_PATH = os.path.join(DATA_DIR, "unranked_ladder.json")

RAW_DIR = os.path.join(DATA_DIR, "unranked_raw")
RAW_FILES = [
    "unranked_wabbit.json",
    "unranked_SauronSlayer.json",
    "unranked_zubair.json",
    "unranked_l.inc.json",
    "unranked_toXic.json",
    "unranked_StrengthHonour.json",
    "unranked_cheetah001.json",
    "unranked_NaKiyaKar.json",
    "unranked_neXus.json",
    "incremental_new_matches.json",
]

TRACKED = {
    "/user/12047120/": "wabbit",
    "/user/12676944/": "SauronSlayer",
    "/user/12667372/": "zubair",
    "/user/12080589/": "l.inc",
    "/user/12499000/": "toXic",
    "/user/4607974/": "Strength & Honour",
    "/user/11907023/": "cheetah001",
    "/user/12693189/": "NaKiyaKar",
    "/user/12805097/": "neXus",
}

SHORT_GAME_THRESHOLD_SECONDS = 15 * 60
LADDER_START_DATE = datetime(2024, 1, 1, tzinfo=timezone.utc)
MAX_FORMAT = 4        # up to 4v4
ELO_BASE = 1000       # Elo-equivalent of a 50% win rate against the field
BT_PRIOR_GAMES = 1.0  # virtual 1-1 record vs an average player; keeps BT finite for 0-win / 0-loss players
EPS = 1e-6


def parse_duration(s):
    if not s:
        return None
    h = m = sec = 0
    hm = re.search(r"(\d+)h", s)
    mm = re.search(r"(\d+)m", s)
    sm = re.search(r"(\d+)s", s)
    if hm: h = int(hm.group(1))
    if mm: m = int(mm.group(1))
    if sm: sec = int(sm.group(1))
    return h * 3600 + m * 60 + sec


MONTH_MAP = {
    "Jan.": "January", "Feb.": "February", "March": "March", "April": "April",
    "May": "May", "June": "June", "July": "July", "Aug.": "August",
    "Sept.": "September", "Oct.": "October", "Nov.": "November", "Dec.": "December",
}

DATE_RE = re.compile(
    r"^(?P<month>[A-Za-z.]+)\s+(?P<day>\d{1,2}),\s+(?P<year>\d{4}),\s+(?P<time>.+)$"
)


def parse_exact_time(s):
    if not s:
        return None
    m = DATE_RE.match(s.strip())
    if not m:
        return None
    month_full = MONTH_MAP.get(m.group("month"), m.group("month"))
    day = int(m.group("day"))
    year = int(m.group("year"))
    time_str = m.group("time").strip().lower()

    if time_str == "midnight":
        hour, minute = 0, 0
    elif time_str == "noon":
        hour, minute = 12, 0
    else:
        time_norm = time_str.replace("a.m.", "am").replace("p.m.", "pm")
        tm = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", time_norm)
        if not tm:
            return None
        hour = int(tm.group(1))
        minute = int(tm.group(2)) if tm.group(2) else 0
        ampm = tm.group(3)
        if ampm == "pm" and hour != 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0

    try:
        month_num = datetime.strptime(month_full, "%B").month
        return datetime(year, month_num, day, hour, minute, tzinfo=timezone.utc)
    except ValueError:
        return None


def sort_key(m):
    dt = parse_exact_time(m["exact_time"])
    return dt if dt else datetime.min.replace(tzinfo=timezone.utc)


def logit(p):
    p = min(max(p, EPS), 1 - EPS)
    return math.log(p / (1 - p))


def sigmoid(x):
    return 1 / (1 + math.exp(-x))


def elo_equivalent(p):
    """Elo number whose expected score against an average (ELO_BASE) player is p."""
    return ELO_BASE + 400 * math.log10(min(max(p, EPS), 1 - EPS) / (1 - min(max(p, EPS), 1 - EPS)))


# ---- Load, filter and resolve matches (same rules as the default ladder) ----
def load_matches():
    all_matches = {}
    for fname in RAW_FILES:
        path = os.path.join(RAW_DIR, fname)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for m in data["matches"]:
            all_matches[m["match_id"]] = m

    qualifying = []
    for m in all_matches.values():
        team_tracked = [
            {p["user_path"] for p in team["players"] if p["user_path"] in TRACKED}
            for team in m["teams"]
        ]
        if len([t for t in team_tracked if t]) < 2:
            continue
        duration_s = parse_duration(m["duration"])
        if duration_s is None or duration_s < SHORT_GAME_THRESHOLD_SECONDS:
            continue
        qualifying.append(m)

    res = resolve_match_results(qualifying, verbose=False)
    drop = set(res["excluded"])
    qualifying = [m for m in qualifying if m["match_id"] not in drop]
    qualifying = [m for m in qualifying if sort_key(m) >= LADDER_START_DATE]
    qualifying.sort(key=sort_key)
    return all_matches, qualifying


def sides(m):
    """(winning tracked paths, losing tracked paths), or None if not a clean 2-sided result."""
    active = [
        (bool(t["won"]), [p["user_path"] for p in t["players"] if p["user_path"] in TRACKED])
        for t in m["teams"]
    ]
    active = [(won, paths) for won, paths in active if paths]
    winners = [paths for won, paths in active if won]
    losers = [paths for won, paths in active if not won]
    if len(winners) != 1 or len(losers) != 1:
        return None
    return winners[0], losers[0]


# ---- Step 1: head-to-head map --------------------------------------------------
def build_head_to_head(matches, now=None):
    """wins[i][j] = weighted wins of i over j; games[i][j] = weighted games between them."""
    wins = defaultdict(lambda: defaultdict(float))
    games = defaultdict(lambda: defaultdict(float))
    for m in matches:
        s = sides(m)
        if s is None:
            continue
        win_side, lose_side = s
        w = 2 / (len(win_side) + len(lose_side))
        if HALF_LIFE_DAYS and now is not None:
            dt = parse_exact_time(m["exact_time"])
            if dt:
                w *= 0.5 ** (max((now - dt).total_seconds(), 0) / 86400 / HALF_LIFE_DAYS)
        for a in win_side:
            for b in lose_side:
                wins[a][b] += w
                games[a][b] += w
                games[b][a] += w
    return wins, games


# ---- Step 2: Bradley-Terry fit + smoothed probability map -----------------------
def fit_bradley_terry(players, wins, games, iters=500):
    """Strength s_i with P(i beats j) = s_i / (s_i + s_j). Standard MM algorithm,
    plus a virtual 1-1 record against a strength-1 average player so everyone
    stays finite. Normalised to geometric mean 1."""
    s = {p: 1.0 for p in players}
    for _ in range(iters):
        new = {}
        for i in players:
            w_i = sum(wins[i][j] for j in players) + BT_PRIOR_GAMES
            denom = sum(games[i][j] / (s[i] + s[j]) for j in players if games[i][j])
            denom += 2 * BT_PRIOR_GAMES / (s[i] + 1.0)
            new[i] = w_i / denom
        g = math.exp(sum(math.log(v) for v in new.values()) / len(new))
        new = {p: v / g for p, v in new.items()}
        delta = max(abs(new[p] - s[p]) for p in players)
        s = new
        if delta < 1e-10:
            break
    return s


def build_probability_map(players, wins, games, strength):
    prob = {}
    for i in players:
        prob[i] = {}
        for j in players:
            if i == j:
                continue
            bt = strength[i] / (strength[i] + strength[j])
            prob[i][j] = (wins[i][j] + PRIOR_GAMES * bt) / (games[i][j] + PRIOR_GAMES)
    return prob


def fit(players, matches, now=None):
    wins, games = build_head_to_head(matches, now)
    strength = fit_bradley_terry(players, wins, games)
    return wins, games, strength, build_probability_map(players, wins, games, strength)


# ---- Step 3: team probability ---------------------------------------------------
def team_win_prob(prob, team_a, team_b):
    logits = [logit(prob[a][b]) for a in team_a for b in team_b]
    return sigmoid(sum(logits) / len(logits))


# ---- Step 4: ladder over every possible line-up ---------------------------------
def format_expectations(players, prob, player, k):
    """Mean win probability for `player` over every k-v-k line-up of `players` they're in."""
    others = [p for p in players if p != player]
    if len(players) < 2 * k:
        return None, 0
    total = n = 0
    for mates in combinations(others, k - 1):
        team_a = (player,) + mates
        rest = [p for p in others if p not in mates]
        for team_b in combinations(rest, k):
            total += team_win_prob(prob, team_a, team_b)
            n += 1
    return total / n, n


def format_of(m):
    s = sides(m)
    if s is None:
        return None
    return min(max(len(s[0]), len(s[1])), MAX_FORMAT)


# ---- Honest accuracy check: predict each match using only earlier matches -------
def evaluate(players, matches, min_history=50):
    """Chronological walk-forward: before each match, refit on everything earlier
    and predict it. Returns accuracy / Brier / log-loss over the predicted matches,
    plus the same scores for the default Elo ladder's own pre-match expectation."""
    default_hist = {}
    if os.path.exists(DEFAULT_LADDER_PATH):
        with open(DEFAULT_LADDER_PATH, encoding="utf-8") as f:
            ladder = json.load(f)
        for pdata in ladder["players"].values():
            for h in pdata["history"]:
                default_hist[(h["match_id"], pdata["user_path"])] = h["elo_before"]

    ours, theirs = [], []
    for idx, m in enumerate(matches):
        if idx < min_history:
            continue
        s = sides(m)
        if s is None:
            continue
        win_side, lose_side = s
        # Predict from the first team's point of view so it's not always the winner's.
        first_won = bool([t for t in m["teams"] if any(p["user_path"] in TRACKED for p in t["players"])][0]["won"])
        team_a, team_b = (win_side, lose_side) if first_won else (lose_side, win_side)

        _, _, _, prob = fit(players, matches[:idx], now=sort_key(m))
        ours.append((team_win_prob(prob, team_a, team_b), first_won))

        ra = [default_hist.get((m["match_id"], p)) for p in team_a]
        rb = [default_hist.get((m["match_id"], p)) for p in team_b]
        if all(r is not None for r in ra + rb):
            diff = sum(rb) / len(rb) - sum(ra) / len(ra)
            theirs.append((1 / (1 + 10 ** (diff / 400)), first_won))

    def score(preds):
        if not preds:
            return None
        n = len(preds)
        acc = sum(1 for p, y in preds if (p > 0.5) == y) / n
        brier = sum((p - y) ** 2 for p, y in preds) / n
        ll = -sum(math.log(p if y else 1 - p) for p, y in
                  ((min(max(p, EPS), 1 - EPS), y) for p, y in preds)) / n
        return {"matches": n, "accuracy": round(acc, 4), "brier": round(brier, 4), "log_loss": round(ll, 4)}

    return {
        "method": (f"walk-forward: each match predicted from a fit on all earlier matches only "
                   f"(first {min_history} matches used as warm-up, not scored). Lower Brier / "
                   f"log-loss is better; always guessing 50% scores Brier 0.25, log-loss 0.693."),
        "probability_ladder": score(ours),
        "default_elo_ladder": score(theirs),
    }


def main():
    all_matches, matches = load_matches()
    print(f"Matches used: {len(matches)} (of {len(all_matches)} scanned)")

    now = datetime.now(timezone.utc)
    active = {p for m in matches if sides(m) for side in sides(m) for p in side}
    players = [p for p in TRACKED if p in active]

    wins, games, strength, prob = fit(players, matches, now)

    fmt_counts = Counter(f for f in (format_of(m) for m in matches) if f)
    fmt_total = sum(fmt_counts.values())
    fmt_weights = {k: fmt_counts.get(k, 0) / fmt_total for k in range(1, MAX_FORMAT + 1)}

    record = {p: {"wins": 0, "losses": 0} for p in players}
    for m in matches:
        s = sides(m)
        if s is None:
            continue
        for p in s[0]:
            record[p]["wins"] += 1
        for p in s[1]:
            record[p]["losses"] += 1

    players_out = {}
    for p in players:
        by_format = {}
        overall = wsum = 0.0
        for k in range(1, MAX_FORMAT + 1):
            exp, n = format_expectations(players, prob, p, k)
            if exp is None:
                continue
            by_format[f"{k}v{k}"] = {"expected_win_rate": round(exp * 100, 1), "lineups": n}
            overall += fmt_weights[k] * exp
            wsum += fmt_weights[k]
        overall /= wsum
        played = record[p]["wins"] + record[p]["losses"]
        players_out[TRACKED[p]] = {
            "user_path": p,
            "expected_win_rate": round(overall * 100, 1),
            "elo_equivalent": round(elo_equivalent(overall), 1),
            "by_format": by_format,
            "bradley_terry_strength": round(strength[p], 4),
            "matches_played": played,
            "wins": record[p]["wins"],
            "losses": record[p]["losses"],
            "win_rate": round(record[p]["wins"] / played * 100, 1) if played else 0.0,
        }

    ladder = sorted(players_out, key=lambda n: players_out[n]["expected_win_rate"], reverse=True)
    for rank, name in enumerate(ladder, 1):
        players_out[name]["rank"] = rank

    head_to_head = {}
    for i in players:
        head_to_head[TRACKED[i]] = {
            TRACKED[j]: {
                "win_probability": round(prob[i][j] * 100, 1),
                "weighted_wins": round(wins[i][j], 2),
                "weighted_games": round(games[i][j], 2),
                "transitive_estimate": round(strength[i] / (strength[i] + strength[j]) * 100, 1),
            }
            for j in players if j != i
        }

    print("Running walk-forward accuracy check (refits once per match)...")
    evaluation = evaluate(players, matches)

    result = {
        "config": {
            "prior_games": PRIOR_GAMES,
            "half_life_days": HALF_LIFE_DAYS,
            "format_weights": {f"{k}v{k}": round(w, 3) for k, w in fmt_weights.items()},
            "short_game_exclusion_threshold_seconds": SHORT_GAME_THRESHOLD_SECONDS,
            "ladder_start_date": LADDER_START_DATE.date().isoformat(),
            "notes": (
                "Experimental ladder. P[i][j] = (weighted wins of i over j + K * Bradley-Terry "
                "estimate) / (weighted games + K). Team win probability = sigmoid of the mean "
                "logit over all cross-team pairs. A player's expected_win_rate is their average "
                "team win probability over every possible line-up of the tracked players, per "
                "format, blended by how often each format is played. elo_equivalent is that win "
                "rate expressed as an Elo against an average (1000) player. Untracked players "
                "and AIs in a match are ignored."
            ),
        },
        "generated_at": now.isoformat(),
        "matches_used": len(matches),
        "ladder": ladder,
        "players": players_out,
        "head_to_head": head_to_head,
        "evaluation": evaluation,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"\nWrote {OUT_PATH}")

    print("\nProbability ladder (expected win % vs the field, all line-ups):")
    for name in ladder:
        p = players_out[name]
        fmts = "  ".join(f"{f} {v['expected_win_rate']:5.1f}%" for f, v in p["by_format"].items())
        print(f"  {p['rank']}. {name:20s} {p['expected_win_rate']:5.1f}%  (~{p['elo_equivalent']:6.0f})   {fmts}")

    names = [TRACKED[p] for p in players]
    short = [n[:6] for n in names]
    print("\nHead-to-head win probability (row beats column, %):")
    print("  " + " " * 20 + " ".join(f"{s:>6}" for s in short))
    for i in players:
        cells = " ".join("     -" if i == j else f"{prob[i][j] * 100:6.1f}" for j in players)
        print(f"  {TRACKED[i]:20s}{cells}")

    print("\nAccuracy (walk-forward, same matches):")
    for label, key in (("probability ladder", "probability_ladder"), ("default Elo ladder", "default_elo_ladder")):
        e = evaluation[key]
        if e:
            print(f"  {label:20s} acc {e['accuracy'] * 100:5.1f}%  brier {e['brier']:.4f}  "
                  f"log-loss {e['log_loss']:.4f}  ({e['matches']} matches)")


if __name__ == "__main__":
    main()
