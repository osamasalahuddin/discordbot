"""Resolve the win/loss of a scraped match before it reaches any Elo simulation.

Two separate problems, handled in order:

1. REPAIR - the scrape lost the winner flag.
   aoe2insights marks the winning team with a CSS class; for 39 of 1407 matches
   (92% of them with an AI in the lobby) the parse produced zero teams flagged
   won, or more than one. A zero-winner match silently scores as a LOSS FOR
   EVERYONE, which is the worst possible failure mode.

   Repaired from `rating_change`: the team whose human players all gained rating
   won. Validated against the 1368 well-formed matches - 873 have usable
   rating_change and it agrees with the winner flag 873/873, 0 disagreements.
   Where rating_change is absent, fall back to resign data. Where neither is
   available the match is UNRESOLVED and must be excluded rather than counted.

2. CORRECT - the engine's verdict itself is wrong for our purposes.
   An AI can never resign. If the AI's human team-mates all quit and the
   opposing team then quits too, AoE2 still awards the win to the AI's team,
   because a team wins while any member survives. The engine is right by its own
   rules; we deliberately disagree, because a player who resigned two minutes
   before the end did not win that game.

   Rule: the team whose human players have ALL resigned, earliest, is the loser.
   "All", not "any": one player often quits while team-mates fight on and
   legitimately win (469715691 - one of four quit at 72 min, rest won at 105).
   Validated on 185 non-AI matches: 180 agree with the recorded result, 0
   disagree, 5 undecidable. Only applied when an AI is present.

Set AI_RESULT_CORRECTION=0 to skip step 2 (A/B). Set RESULT_REPAIR=0 to skip
step 1.
"""
import json
import os

_DATA_DIR = os.path.dirname(os.path.abspath(__file__))
PERFORMANCE_DB_PATH = os.path.join(_DATA_DIR, "match_performance.json")


def load_perf_db():
    try:
        with open(PERFORMANCE_DB_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"matches": {}, "status": {}}


def _flagged_winners(match):
    return [i for i, t in enumerate(match["teams"]) if t["won"]]


def repair_duplicate_players(match):
    """Drop phantom player rows left by a mis-parsed scrape.

    Seen once in 1407 matches (502721922, Arena, 2026-08-28): a 6-player game
    scraped as 7 rows, with zubair appearing on BOTH teams. The phantom row
    carried another player's civ (armenians, which was actually neXus's) and
    null rating / rating_change, while the real row had civ khmer and +20 -
    matching the replay, which puts zubair on the winning side. So the fix is to
    keep the occurrence that has real rating data and drop the others.

    Returns the number of rows removed, or None if the duplication can't be
    resolved this way (caller should then exclude the match).
    """
    from collections import defaultdict
    seen = defaultdict(list)
    for ti, team in enumerate(match["teams"]):
        for p in team["players"]:
            if p.get("user_path"):
                seen[p["user_path"]].append((ti, p))

    removed = 0
    for occ in seen.values():
        if len(occ) < 2:
            continue
        real = [(ti, p) for ti, p in occ
                if p.get("rating_change") is not None or p.get("rating") is not None]
        if len(real) != 1:
            return None                      # can't tell which row is genuine
        keep = real[0][1]
        for ti, p in occ:
            if p is not keep:
                match["teams"][ti]["players"].remove(p)
                removed += 1
    return removed


def structural_problem(match):
    """Reason this match can't be rated at all, or None if it's well-formed.

    - 'ffa': more than two teams. aoe2insights renders free-for-all lobbies with
      every player in their own .team div. These aren't real team games and the
      Elo model (own side vs the average of the other side) doesn't describe
      them, so they're dropped rather than shoehorned in.
    - 'duplicate_player': the same user_path appears on more than one team, so a
      player would be scored as both winning and losing the same game.
    - 'degenerate': fewer than two teams.
    """
    if len(match["teams"]) > 2:
        return "ffa"
    if len(match["teams"]) < 2:
        return "degenerate"
    paths = [p["user_path"] for t in match["teams"] for p in t["players"] if p.get("user_path")]
    if len(paths) != len(set(paths)):
        return "duplicate_player"
    return None


def is_result_broken(match):
    """True only when NO team is flagged as the winner.

    More than one winner is NOT broken: aoe2insights renders FFA-style lobbies
    with every player in their own .team div, so a 2v2 can legitimately appear as
    6 teams with 2 winners. Only a total absence of a winner flag is corrupt -
    and that scores as a loss for everyone, which is why it matters.
    """
    return len(_flagged_winners(match)) == 0


def winner_from_rating_change(match):
    """Team index whose human players all gained rating. None if unusable."""
    winner = None
    for i, team in enumerate(match["teams"]):
        vals = [p["rating_change"] for p in team["players"]
                if not p["is_ai"] and p.get("rating_change") not in (None, 0)]
        if not vals:
            continue
        gained = all(v > 0 for v in vals)
        lost = all(v < 0 for v in vals)
        if not (gained or lost):
            return None                  # mixed signs inside a team - untrustworthy
        if gained:
            if winner is not None:
                return None              # two winning teams - untrustworthy
            winner = i
    return winner


def winner_from_resign(match, perf_db):
    """Team index that actually won, from replay resign data. None if undecidable.

    A team is 'out' only once every one of its humans has resigned; the team out
    earliest is the loser.
    """
    mp = perf_db["matches"].get(str(match["match_id"]))
    if not mp or mp.get("_meta", {}).get("schema", 1) < 2:
        return None

    path_to_idx = {}
    for i, team in enumerate(match["teams"]):
        for p in team["players"]:
            if p.get("user_path"):
                path_to_idx[p["user_path"]] = i

    team_to_idx, humans = {}, {}
    for key, e in mp.items():
        if key == "_meta":
            continue
        pid = e.get("profile_id")
        idx = path_to_idx.get("/user/%s/" % pid) if pid else None
        if idx is not None:
            team_to_idx[e["team"]] = idx
        if not e.get("is_ai"):
            humans.setdefault(e["team"], []).append(e)

    if len(team_to_idx) < 2 or len(humans) < 2:
        return None

    out_at = {}
    for t, members in humans.items():
        times = [x["resigned_time_s"] for x in members if x.get("resigned_time_s") is not None]
        out_at[t] = max(times) if times and len(times) == len(members) else None

    done = {t: v for t, v in out_at.items() if v is not None}
    if len(done) == 1:
        loser = next(iter(done))
    elif len(done) == len(out_at) and len(done) >= 2:
        loser = min(done, key=done.get)
    else:
        return None

    winners = [t for t in out_at if t != loser]
    if len(winners) != 1:
        return None
    return team_to_idx.get(winners[0])


def _set_winner(match, idx):
    for i, team in enumerate(match["teams"]):
        team["won"] = (i == idx)


def resolve_match_results(matches, perf_db=None, verbose=True):
    """Repair broken winner flags, then apply the AI resign correction.

    Mutates `match["teams"][i]["won"]` in place. Returns a dict with the lists of
    match_ids that were repaired / corrected / left unresolved. UNRESOLVED
    matches must be excluded by the caller - they have no trustworthy result.
    """
    if perf_db is None:
        perf_db = load_perf_db()

    repaired_rc, repaired_resign, unresolved, corrected = [], [], [], []
    repaired_dupes = []
    invalid = {}
    do_repair = os.environ.get("RESULT_REPAIR") != "0"
    do_correct = os.environ.get("AI_RESULT_CORRECTION") != "0"

    for m in matches:
        # ---- step 0: repair phantom rows, then drop what's still unrateable
        if do_repair and repair_duplicate_players(m):
            repaired_dupes.append(m["match_id"])
        problem = structural_problem(m)
        if problem:
            invalid[m["match_id"]] = problem
            continue

        # EXCLUDE_UNVERIFIED_AI=1 drops AI matches we cannot check. The AI-side
        # win rate is 53.5% where resign data lets us verify the result and
        # 69.9% where it does not - a 16-point gap that is the can't-resign bug
        # still live in the unverifiable ones. Exposure is asymmetric (toXic and
        # SauronSlayer played WITH the AI far more than against it), so it is
        # not a wash across the ladder.
        if os.environ.get("EXCLUDE_UNVERIFIED_AI") == "1":
            has_ai = any(p["is_ai"] for t in m["teams"] for p in t["players"])
            if has_ai and winner_from_resign(m, perf_db) is None:
                invalid[m["match_id"]] = "unverified_ai"
                continue

        # ---- step 1: repair a broken winner flag -------------------------
        if do_repair and is_result_broken(m):
            idx = winner_from_rating_change(m)
            if idx is not None:
                _set_winner(m, idx)
                repaired_rc.append(m["match_id"])
            else:
                idx = winner_from_resign(m, perf_db)
                if idx is not None:
                    _set_winner(m, idx)
                    repaired_resign.append(m["match_id"])
                else:
                    unresolved.append(m["match_id"])
                    continue

        # ---- step 2: override the engine on AI matches --------------------
        if not do_correct:
            continue
        if not any(p["is_ai"] for team in m["teams"] for p in team["players"]):
            continue
        idx = winner_from_resign(m, perf_db)
        if idx is None:
            continue
        if not m["teams"][idx]["won"]:
            _set_winner(m, idx)
            corrected.append(m["match_id"])

    if verbose:
        from collections import Counter
        by_reason = dict(Counter(invalid.values()))
        if repaired_dupes:
            print(f"Phantom duplicate player rows removed in {len(repaired_dupes)} match(es): {repaired_dupes}")
        print(f"Structurally unrateable, excluded: {len(invalid)} {by_reason}")
        print(f"Result repair: {len(repaired_rc)} from rating_change, "
              f"{len(repaired_resign)} from resign data, "
              f"{len(unresolved)} UNRESOLVED (excluded)")
        print(f"AI matches with the engine verdict overridden by resign data: {len(corrected)}")

    return {
        "repaired_rating_change": repaired_rc,
        "repaired_resign": repaired_resign,
        "unresolved": unresolved,
        "ai_corrected": corrected,
        "repaired_duplicate_players": repaired_dupes,
        "invalid": invalid,                      # match_id -> reason
        "excluded": sorted(set(unresolved) | set(invalid)),
    }
