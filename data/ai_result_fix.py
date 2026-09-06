"""Correct the recorded winner of AI matches using replay resign data.

aoe2insights records the winner by who resigned, and an AI can never resign. So
a game where the AI's human team-mates all quit first is still recorded as a WIN
for the AI's team, and the opposing side - who only quit afterwards because the
game would not end - is recorded as the loser.

Where schema-2 performance data exists (which carries a per-player
resigned_time_s), the result is recomputed:

    the team whose human players have ALL resigned, earliest, is the loser.

"All", not "any": in a team game one player often quits while their team-mates
fight on and legitimately win (match 469715691 - one of four quit at 72 min, the
rest won at 105 min).

Validated against 185 non-AI matches: 180 agree with the recorded result, 0
disagree, 5 undecidable. Of 72 checkable AI matches, 20 were scored backwards.

Shared by build_unranked_ladder.py, build_map_elo.py and build_openclosed_elo.py
so the three simulations can't drift apart. Set AI_RESULT_CORRECTION=0 to disable
(for A/B comparison).
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


def resign_verdict(match, perf_db):
    """Index of the team that actually won, from resign data. None if undecidable."""
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

    # A team is "out" only once every one of its humans has resigned.
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


def apply_ai_result_corrections(matches, perf_db=None, verbose=True):
    """Flip team['won'] in place on any AI match the resign data contradicts.

    `matches` is any iterable of raw match records. Returns the list of corrected
    match_ids.
    """
    corrected = []
    if os.environ.get("AI_RESULT_CORRECTION") == "0":
        if verbose:
            print("AI result correction DISABLED (AI_RESULT_CORRECTION=0)")
        return corrected

    if perf_db is None:
        perf_db = load_perf_db()

    for m in matches:
        if not any(p["is_ai"] for team in m["teams"] for p in team["players"]):
            continue
        winner_idx = resign_verdict(m, perf_db)
        if winner_idx is None:
            continue
        if not m["teams"][winner_idx]["won"]:
            for i, team in enumerate(m["teams"]):
                team["won"] = (i == winner_idx)
            corrected.append(m["match_id"])

    if verbose:
        print(f"AI matches with the recorded winner corrected from resign data: {len(corrected)}")
    return corrected
