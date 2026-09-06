import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone

_DATA_DIR = os.path.dirname(os.path.abspath(__file__))
# run_full_refresh.py invokes these via runpy, which does not put the script's
# own directory on sys.path - do it here so sibling modules import cleanly.
if _DATA_DIR not in sys.path:
    sys.path.insert(0, _DATA_DIR)
from match_results import resolve_match_results

_parser = argparse.ArgumentParser(description="Build the unranked Elo ladder.")
_parser.add_argument(
    "--army-efficiency", action="store_true",
    help="performance ratio uses cost-weighted army value + upgrade coverage instead of "
         "raw military count + raw tech count. Writes a separate output file so the default "
         "ladder is untouched.",
)
_parser.add_argument(
    "--schema2-only", action="store_true",
    help="restrict the Elo simulation to matches that have a schema-2 performance "
         "record (recent, full replay analysis). Everyone still starts at 1000. "
         "For experimenting with a performance-only ladder.",
)
_parser.add_argument(
    "--only-schema", type=int, choices=(1, 2), default=None,
    help="restrict the Elo simulation to matches whose performance record has "
         "exactly this schema version. Everyone still starts at 1000.",
)
_parser.add_argument("--out", default=None, help="override the output path")
_args, _ = _parser.parse_known_args()
USE_ARMY_EFFICIENCY = _args.army_efficiency
ONLY_SCHEMA = 2 if _args.schema2_only else _args.only_schema

RAW_DIR = os.path.join(_DATA_DIR, "unranked_raw")
PERFORMANCE_DB_PATH = os.path.join(_DATA_DIR, "match_performance.json")
UNIT_DATA_PATH = os.path.join(_DATA_DIR, "aoe2_unit_data.json")
OUT_PATH = _args.out or (
    os.path.join(_DATA_DIR, "unranked_ladder.army_efficiency.json") if USE_ARMY_EFFICIENCY
    else os.path.join(_DATA_DIR, "unranked_ladder.json")
)

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

K_FACTOR = 32
STARTING_ELO = 1000
AI_SHORT_GAME_THRESHOLD_SECONDS = 15 * 60  # 15 minutes
LADDER_START_DATE = datetime(2024, 1, 1, tzinfo=timezone.utc)

# On matches with performance data, the score fed to the Elo update is
#   actual = PERF_RESULT_WEIGHT * (1.0 if won else 0.0) + (1 - PERF_RESULT_WEIGHT) * performance_ratio
# so the win/loss result dominates and the macro/army comparison nudges it.
# Winner lands in [W, 1.0], loser in [0, 1-W]; the result always outranks perf.
PERF_RESULT_WEIGHT = float(os.environ.get("PERF_RESULT_WEIGHT", "0.75"))
# PERF_DISABLED=1 rebuilds with flat win/loss only (for before/after comparison).
_PERF_DISABLED = os.environ.get("PERF_DISABLED") == "1"

# Settle each match so gains and losses cancel exactly, by adjusting the winning
# side. This is what stops an unrated AI from carrying Elo out of the pool (and
# equally stops uneven teams inflating it). ELO_CONSERVATION=0 disables it.
CONSERVE_ELO = os.environ.get("ELO_CONSERVATION") != "0"

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
    month_str, day_str, year_str, time_str = None, None, None, None
    m = DATE_RE.match(s.strip())
    if not m:
        return None
    month_raw = m.group("month")
    month_full = MONTH_MAP.get(month_raw, month_raw)
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

# ---- Load performance data (military/economy/eAPM stats) where available ----
try:
    with open(PERFORMANCE_DB_PATH, encoding="utf-8") as f:
        _perf_db = json.load(f)
except FileNotFoundError:
    _perf_db = {"matches": {}, "status": {}}

PROFILE_ID_TO_PATH = {int(path.strip("/").split("/")[1]): path for path in TRACKED}


def get_player_perf(match_id, user_path):
    match_perf = _perf_db["matches"].get(str(match_id))
    if not match_perf:
        return None
    profile_id = int(user_path.strip("/").split("/")[1])
    for key, entry in match_perf.items():
        if key == "_meta":
            continue
        if entry.get("profile_id") == profile_id:
            return entry
    return None


# ---- Performance ratio ------------------------------------------------------
# 0.5 == on par with the tracked opponent(s). >0.5 == out-macro'd / out-produced
# them, <0.5 == behind. Fed into the Elo update as a margin, never flips a result.
#
# schema-2 records (recent matches, full replay analysis) use _perf_ratio_v2:
#   a weighted blend of economy, age-up speed, military output and tech tempo,
#   each measured against the opponent average at a map-type-aware reference time.
# Older schema-1 records fall back to _legacy_perf_ratio (eAPM / eco-APM / castle
# time only), preserving prior behaviour for the handful of matches that predate
# the richer extraction.

# Standard community open/closed categorisation (mirrors build_openclosed_elo.py).
_CLOSED_MAPS = {
    "Arena", "Black Forest", "Fortress", "Hideout", "Land Madness", "Enclosed",
    "Fortified Clearing", "Team Moats", "Moats", "Ring Fortress", "Lombardia",
    "Murkwood", "Golden Swamp", "QS Arena", "QS Black Forest",
    "Rage Arena V4 Custom", "Populationboost Arena Custom",
    "Populationboost Black Forest Custom", "Rage Forest 5 - Official Map Custom",
}
_OPEN_MAPS = {
    "Arabia", "Ghost Lake", "Sacred Springs", "Gold Rush", "Golden Pit", "Mongolia",
    "Steppe", "Valley", "Meadow", "Oasis", "Acclivity", "Wolf Hill", "Runestones",
    "Yucatan", "Atacama", "Marketplace", "Salt Marsh", "Hamburger", "Prairie",
    "Serengeti", "Kilimanjaro", "Haboob", "African Clearing", "Shrubland", "Budapest",
    "Acropolis",
}

# Reference time (seconds) at which "by when" metrics are compared.
PERF_REF_TIME_S = {"closed": 1200, "open": 720, "other": 900}
# Weights for the schema-2 blend, set from a correlation-with-wins study over the
# 259 schema-2 matches:
#   mil (cost-weighted army value) +0.38   tech (upgrade coverage) +0.30
#   apm (military APM vs opp)      +0.18   eco (villagers @ ref)   ~0.00  -> dropped
#   age (castle-up time)          ~0.05   -> dropped (redundant with mil)
# military APM is weighted heavily on purpose: it's the only signal that also
# reflects team-fight micro, which none of the macro stats capture.
# With PERF_RESULT_WEIGHT 0.75 these are, per schema-2 match:
#   army value 13.75% | military APM 10% | upgrade coverage 1.25% | result 75%
# Override with the PERF_WEIGHTS env var, e.g. PERF_WEIGHTS='{"mil":0.5,"tech":0.3,"apm":0.2}'
PERF_WEIGHTS_V2 = {"eco": 0.0, "age": 0.0, "mil": 0.55, "tech": 0.05, "apm": 0.40}
if os.environ.get("PERF_WEIGHTS"):
    PERF_WEIGHTS_V2 = {**PERF_WEIGHTS_V2, **json.loads(os.environ["PERF_WEIGHTS"])}


def _ref_time_s(map_name, duration_s):
    if map_name in _CLOSED_MAPS:
        base = PERF_REF_TIME_S["closed"]
    elif map_name in _OPEN_MAPS:
        base = PERF_REF_TIME_S["open"]
    else:
        base = PERF_REF_TIME_S["other"]
    return min(base, duration_s) if duration_s else base


def _interp_at(count_at, t):
    """Linearly interpolate a {"<seconds>": value} checkpoint dict at time t."""
    if not count_at:
        return None
    pts = sorted((int(k), v) for k, v in count_at.items())
    if t <= pts[0][0]:
        return float(pts[0][1])
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if t <= t1:
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return float(pts[-1][1])


def _techs_by(tech_list, t):
    return sum(1 for _, tt in (tech_list or []) if tt <= t)


def _pair_ratio(own_v, opp_v, higher_better=True):
    total = own_v + opp_v
    if total <= 0:
        return None
    return (own_v / total) if higher_better else (opp_v / total)


# ---- Army-efficiency signals (only used with --army-efficiency) -------------
# Cost-weighted army value and upgrade coverage, derived from military_by_type /
# military_techs / uptimes via aoe2_unit_data.json. See analyze_army_efficiency.py
# for the exploratory version and the correlation-with-wins analysis.
_AGE_ORDER = {"dark": 0, "feudal": 1, "castle": 2, "imperial": 3}
_MIN_CLASS_COUNT = 5  # a class must be built this many times to pull in its upgrades

if USE_ARMY_EFFICIENCY:
    with open(UNIT_DATA_PATH, encoding="utf-8") as f:
        _UD = json.load(f)
    _CLASSES = _UD["classes"]
    _UNIT_TO_CLASS = _UD["unit_to_class"]
    _UPGRADE_CATS = _UD["upgrade_categories"]
    _NON_ARMY_UNITS = {"Trade Cog", "Trade Cart", "Fishing Ship"}


def _classify_army(mil_by_type):
    comp = Counter()
    for name, n in (mil_by_type or {}).items():
        if name in _NON_ARMY_UNITS:
            continue
        comp[_UNIT_TO_CLASS.get(name, "other")] += n
    return comp


def _army_resource_value(comp):
    total = 0
    for cls, n in comp.items():
        c = _CLASSES.get(cls, _CLASSES["other"])["cost"]
        total += (c["food"] + c["wood"] + c["gold"]) * n
    return total


def _reached_age(rec):
    if rec.get("imperial_time_ms"):
        return "imperial"
    if rec.get("castle_time_ms"):
        return "castle"
    if rec.get("feudal_time_ms"):
        return "feudal"
    return "dark"


def _upgrade_coverage(rec, comp):
    """Fraction of the upgrades that matter for this army (and are reachable by
    the age the player got to) that were actually researched. None if no army."""
    if not comp:
        return None
    researched = {t for t, _ in (rec.get("military_techs") or [])}
    max_age = _AGE_ORDER[_reached_age(rec)]
    got = avail = 0
    for meta in _UPGRADE_CATS.values():
        if _AGE_ORDER.get(meta["age"], 3) > max_age:
            continue
        if not any(comp.get(cls, 0) >= _MIN_CLASS_COUNT for cls in meta["applies_to"]):
            continue
        got += sum(1 for t in meta["techs"] if t in researched)
        avail += len(meta["techs"])
    return (got / avail) if avail else None


def _perf_ratio_v2(own, opps, map_name, duration_s):
    ref = _ref_time_s(map_name, duration_s)
    parts = []  # (weight, score)

    # economy: villagers by the reference time
    ov = _interp_at(own.get("villager_count_at"), ref)
    opp_vs = [v for v in (_interp_at(o.get("villager_count_at"), ref) for o in opps) if v is not None]
    if ov is not None and opp_vs:
        r = _pair_ratio(ov, sum(opp_vs) / len(opp_vs), higher_better=True)
        if r is not None:
            parts.append((PERF_WEIGHTS_V2["eco"], r))

    # age-up speed: castle time, falling back to feudal
    for key in ("castle_time_ms", "feudal_time_ms"):
        ot = own.get(key)
        opp_ts = [o[key] for o in opps if o.get(key) is not None]
        if ot is not None and opp_ts:
            r = _pair_ratio(ot, sum(opp_ts) / len(opp_ts), higher_better=False)
            if r is not None:
                parts.append((PERF_WEIGHTS_V2["age"], r))
            break

    if USE_ARMY_EFFICIENCY:
        # military: cost-weighted army value (400 spears != 400 knights).
        own_comp = _classify_army(own.get("military_by_type"))
        opp_comps = [_classify_army(o.get("military_by_type")) for o in opps]
        om = _army_resource_value(own_comp)
        opp_ms = [_army_resource_value(c) for c in opp_comps]
        opp_ms = [x for x in opp_ms if x > 0]
        if om > 0 and opp_ms:
            r = _pair_ratio(om, sum(opp_ms) / len(opp_ms), higher_better=True)
            if r is not None:
                parts.append((PERF_WEIGHTS_V2["mil"], r))

        # tech: did you research the upgrades that matter for the army you built?
        own_cov = _upgrade_coverage(own, own_comp)
        opp_covs = [_upgrade_coverage(o, c) for o, c in zip(opps, opp_comps)]
        opp_covs = [x for x in opp_covs if x is not None]
        if own_cov is not None and opp_covs:
            r = _pair_ratio(own_cov, sum(opp_covs) / len(opp_covs), higher_better=True)
            if r is not None:
                parts.append((PERF_WEIGHTS_V2["tech"], r))
    else:
        # military commitment: total units produced over the game (same length for
        # both sides, so no normalisation needed). A checkpoint doesn't work here -
        # before the reference time most players have zero army and the ratio is noise.
        om = own.get("military_trained")
        opp_ms = [o["military_trained"] for o in opps if o.get("military_trained") is not None]
        if om is not None and opp_ms:
            r = _pair_ratio(om, sum(opp_ms) / len(opp_ms), higher_better=True)
            if r is not None:
                parts.append((PERF_WEIGHTS_V2["mil"], r))

        # tech tempo: upgrades researched by the reference time
        own_tc = _techs_by(own.get("eco_techs"), ref) + _techs_by(own.get("military_techs"), ref)
        opp_tcs = [_techs_by(o.get("eco_techs"), ref) + _techs_by(o.get("military_techs"), ref) for o in opps]
        if opp_tcs:
            r = _pair_ratio(own_tc, sum(opp_tcs) / len(opp_tcs), higher_better=True)
            if r is not None:
                parts.append((PERF_WEIGHTS_V2["tech"], r))

    # mechanical intensity: military APM vs the opponent average. Same in both
    # modes; the one signal that's largely independent of macro tempo.
    if PERF_WEIGHTS_V2.get("apm"):
        oa = own.get("military_apm_mean")
        opp_a = [o["military_apm_mean"] for o in opps if o.get("military_apm_mean") is not None]
        if oa is not None and opp_a:
            r = _pair_ratio(oa, sum(opp_a) / len(opp_a), higher_better=True)
            if r is not None:
                parts.append((PERF_WEIGHTS_V2["apm"], r))

    if not parts:
        return None
    wsum = sum(w for w, _ in parts)
    return sum(w * s for w, s in parts) / wsum


def _legacy_perf_ratio(own, opps):
    ratios = []
    for key, higher_better in (("eapm_mean", True), ("eco_apm_mean", True), ("castle_time_ms", False)):
        if own.get(key) is None:
            continue
        opp_vals = [o[key] for o in opps if o.get(key) is not None]
        if not opp_vals:
            continue
        r = _pair_ratio(own[key], sum(opp_vals) / len(opp_vals), higher_better=higher_better)
        if r is not None:
            ratios.append(r)
    if not ratios:
        return None
    return sum(ratios) / len(ratios)


def compute_performance_ratio(match_id, own_path, opp_paths):
    """Return (ratio, method) where method is 'v2', 'legacy', or None."""
    if _PERF_DISABLED:
        return None, None
    own = get_player_perf(match_id, own_path)
    if own is None:
        return None, None
    opps = [o for o in (get_player_perf(match_id, p) for p in opp_paths) if o is not None]
    if not opps:
        return None, None

    match_perf = _perf_db["matches"].get(str(match_id), {})
    meta = match_perf.get("_meta", {})
    if meta.get("schema", 1) >= 2:
        dur_s = (meta.get("duration_ms") or 0) / 1000 or None
        v2 = _perf_ratio_v2(own, opps, meta.get("map"), dur_s)
        if v2 is not None:
            return v2, ("v2-armyeff" if USE_ARMY_EFFICIENCY else "v2")

    legacy = _legacy_perf_ratio(own, opps)
    return (legacy, "legacy") if legacy is not None else (None, None)


# ---- Load and dedupe matches across all 9 players' unranked scrapes ----
all_matches = {}
for fname in RAW_FILES:
    path = os.path.join(RAW_DIR, fname)
    if not os.path.exists(path):
        continue  # e.g. incremental_new_matches.json before the first incremental run
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for m in data["matches"]:
        all_matches[m["match_id"]] = m  # same match_id -> identical record from any source

print(f"Total unique unranked matches (union across 9 players): {len(all_matches)}")

# ---- Filter: matches with >=2 tracked players on opposing teams ----
qualifying = []
excluded_short_game = []
excluded_no_opposition = 0

for m in all_matches.values():
    team_tracked = []  # list of sets of tracked user_paths per team
    for team in m["teams"]:
        tracked_in_team = {p["user_path"] for p in team["players"] if p["user_path"] in TRACKED}
        team_tracked.append(tracked_in_team)

    teams_with_tracked = [t for t in team_tracked if t]
    if len(teams_with_tracked) < 2:
        excluded_no_opposition += 1
        continue

    duration_s = parse_duration(m["duration"])

    if duration_s is None or duration_s < AI_SHORT_GAME_THRESHOLD_SECONDS:
        excluded_short_game.append(m["match_id"])
        continue

    qualifying.append(m)

print(f"Qualifying matches (2+ tracked players on opposing teams): {len(qualifying)}")
print(f"Excluded (only one side has tracked players / no real opposition): {excluded_no_opposition}")
print(f"Excluded (game duration < 15min, regardless of AI presence): {len(excluded_short_game)}")


# ---- AI-match result correction ---------------------------------------------
# An AI can never resign, so aoe2insights records a win for the AI's team even
# when its human team-mates all quit first. Recompute from resign data; shared
# with build_map_elo.py / build_openclosed_elo.py via match_results.py.
_res = resolve_match_results(qualifying)
ai_results_corrected = _res["ai_corrected"]
results_repaired = _res["repaired_rating_change"] + _res["repaired_resign"]
results_unresolved = set(_res["unresolved"])
results_invalid = _res["invalid"]
_drop = set(_res["excluded"])
if _drop:
    qualifying = [m for m in qualifying if m["match_id"] not in _drop]
    print(f"Excluded (unrateable {len(results_invalid)} + unresolvable {len(results_unresolved)}): {len(_drop)}")

# ---- Sort chronologically (oldest first) ----
def sort_key(m):
    dt = parse_exact_time(m["exact_time"])
    return dt if dt else datetime.min.replace(tzinfo=timezone.utc)

qualifying.sort(key=sort_key)

before_cutoff = sum(1 for m in qualifying if sort_key(m) < LADDER_START_DATE)
qualifying = [m for m in qualifying if sort_key(m) >= LADDER_START_DATE]
print(f"Excluded (before ladder start date {LADDER_START_DATE.date()}): {before_cutoff}")

if ONLY_SCHEMA is not None:
    def _rec_schema(m):
        rec = _perf_db["matches"].get(str(m["match_id"]))
        if rec is None:
            return None  # no performance record at all
        return rec.get("_meta", {}).get("schema", 1)  # no _meta -> old flat schema-1 record
    kept = [m for m in qualifying if _rec_schema(m) == ONLY_SCHEMA]
    print(f"Excluded (--only-schema {ONLY_SCHEMA}: no schema-{ONLY_SCHEMA} record): {len(qualifying) - len(kept)}")
    qualifying = kept

print(f"Matches used for ladder simulation: {len(qualifying)}")

# ---- ELO ladder simulation ----
elo = {path: STARTING_ELO for path in TRACKED}
history = {path: [] for path in TRACKED}
match_log = []

for m in qualifying:
    team_tracked_paths = []
    for team in m["teams"]:
        paths = [p["user_path"] for p in team["players"] if p["user_path"] in TRACKED]
        team_tracked_paths.append(paths)

    # Only teams that actually have tracked players matter for the ladder
    active_team_indices = [i for i, paths in enumerate(team_tracked_paths) if paths]

    if len(active_team_indices) != 2:
        # Rare FFA-style case with tracked players on 3+ sides: pool "everyone else" as the opponent per side
        pass

    match_deltas = []  # (user_path, old_elo, new_elo, delta, won, opp_avg_elo)

    for i in active_team_indices:
        own_paths = team_tracked_paths[i]
        won = bool(m["teams"][i]["won"])
        # Opponent = average elo of tracked players on all OTHER active teams (handles >2-team edge case too)
        opp_paths = [p for j in active_team_indices if j != i for p in team_tracked_paths[j]]
        if not opp_paths:
            continue
        opp_avg = sum(elo[p] for p in opp_paths) / len(opp_paths)

        for path in own_paths:
            own_rating = elo[path]
            expected = 1 / (1 + 10 ** ((opp_avg - own_rating) / 400))

            perf_ratio, perf_method = compute_performance_ratio(m["match_id"], path, opp_paths)
            if perf_ratio is not None:
                result_score = 1.0 if won else 0.0
                actual = PERF_RESULT_WEIGHT * result_score + (1.0 - PERF_RESULT_WEIGHT) * perf_ratio
            else:
                actual = 1.0 if won else 0.0

            delta = K_FACTOR * (actual - expected)
            new_rating = own_rating + delta
            match_deltas.append((path, own_rating, new_rating, delta, won, opp_avg, perf_ratio, actual, perf_method))

    if not match_deltas:
        continue

    # ---- conserve Elo within the match -----------------------------------
    # Every player is rated independently against the opponent average, so the
    # gains and losses in a match don't have to cancel - and they systematically
    # don't when the sides are uneven, which is exactly what an AI does. Measured
    # over the raw ladder: non-AI matches inflate by +1.00 each, AI matches
    # DEFLATE by -2.48 each (Elo the unrated AI earns and takes with it).
    #
    # Settling the difference on the winning side implements the intended rule
    # directly, with no AI-specific special case:
    #   AI's team wins  -> the shortfall is the Elo the AI would have taken, so
    #                      its human team-mates split it. 2 humans + AI beating 3
    #                      humans: +32 claimed vs -48 paid, +8 each to balance.
    #   AI's team loses -> the excess is Elo nobody paid for, because the AI
    #                      cannot pay, so the winners give it back. 2 humans + AI
    #                      losing to 3: -32 paid vs +48 claimed, winners scaled
    #                      to +10.67 each.
    # Applied to every match, so total ladder Elo stays fixed at 9 x 1000.
    # ELO_CONSERVATION=0 disables it.
    balance_adj = {}
    if CONSERVE_ELO:
        imbalance = sum(d[3] for d in match_deltas)
        winner_idx = [k for k, d in enumerate(match_deltas) if d[4]]
        if winner_idx and abs(imbalance) > 1e-9:
            adj = -imbalance / len(winner_idx)
            for k in winner_idx:
                path, old_r, new_r, delta, won, opp_avg, perf_ratio, actual, perf_method = match_deltas[k]
                balance_adj[path] = adj
                match_deltas[k] = (path, old_r, new_r + adj, delta + adj, won,
                                   opp_avg, perf_ratio, actual, perf_method)

    match_date = parse_exact_time(m["exact_time"])
    for path, old_r, new_r, delta, won, opp_avg, perf_ratio, actual, perf_method in match_deltas:
        elo[path] = new_r
        history[path].append({
            "match_id": m["match_id"],
            "date": match_date.isoformat() if match_date else None,
            "map": m["map"],
            "won": won,
            "elo_before": round(old_r, 1),
            "elo_after": round(new_r, 1),
            "delta": round(delta, 1),
            "opponent_avg_elo": round(opp_avg, 1),
            "performance_ratio": round(perf_ratio, 3) if perf_ratio is not None else None,
            "performance_method": perf_method,
            "actual_score": round(actual, 3),
            "balance_adj": round(balance_adj[path], 2) if path in balance_adj else None,
        })

    match_log.append({
        "match_id": m["match_id"],
        "date": match_date.isoformat() if match_date else None,
        "map": m["map"],
        "duration_seconds": parse_duration(m["duration"]),
        "had_ai": any(p["is_ai"] for team in m["teams"] for p in team["players"]),
        "teams": [
            {
                "won": team["won"],
                "players": [
                    {"name": p["name"], "user_path": p["user_path"], "civ": p["civ"], "is_ai": p["is_ai"],
                     "tracked": p["user_path"] in TRACKED}
                    for p in team["players"]
                ]
            }
            for team in m["teams"]
        ],
    })

# ---- Build final output ----
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
        "short_game_exclusion_threshold_seconds": AI_SHORT_GAME_THRESHOLD_SECONDS,
        "ladder_start_date": LADDER_START_DATE.date().isoformat(),
        "performance_matches_available": sum(1 for s in _perf_db["status"].values() if s == "ok"),
        "performance_ratio_v2": {
            "mode": "army_efficiency" if USE_ARMY_EFFICIENCY else "basic",
            "result_weight": PERF_RESULT_WEIGHT,
            "weights": PERF_WEIGHTS_V2,
            "reference_time_seconds": PERF_REF_TIME_S,
            "signals": {
                "eco": "villagers trained by the reference time vs opponent average (weight 0 - no win correlation)",
                "age": "castle-age up-time vs opponent average (weight 0 - redundant)",
                "apm": "military APM vs opponent average (macro-independent mechanical intensity)",
                "mil": (
                    "cost-weighted army value vs opponent average (aoe2_unit_data.json)"
                    if USE_ARMY_EFFICIENCY
                    else "total military units produced over the game vs opponent average"
                ),
                "tech": (
                    "upgrade coverage - researched vs relevant-and-reachable upgrades for the "
                    "army built - vs opponent average"
                    if USE_ARMY_EFFICIENCY
                    else "eco+military upgrades researched by the reference time vs opponent average"
                ),
            },
        },
        "performance_adjustment_notes": (
            "Where per-match performance data is available (match_performance.json), the score fed "
            "into the Elo update is  actual = W*result + (1-W)*performance_ratio  (W = "
            f"{PERF_RESULT_WEIGHT}), instead of flat 1.0/0.0. performance_ratio is 0.5 when on par "
            "with the tracked opponent(s). schema-2 records (recent matches, full replay analysis) "
            "use performance_ratio_v2: a weighted blend of economy (villagers by a reference "
            "time), age-up speed, total military produced, and tech tempo, each vs the opponent "
            "average. The reference time is map-type aware (closed maps 1200s, open 720s, other "
            "900s, capped at game length). Older schema-1 records fall back to the legacy "
            "eAPM/eco-APM/castle-time blend. The win/loss result always dominates (winner ends in "
            f"[{PERF_RESULT_WEIGHT}, 1.0], loser in [0.0, {round(1 - PERF_RESULT_WEIGHT, 2)}]); the "
            "macro/army comparison only nudges within that band, so a dominant win gains more than "
            "a scrappy one and a well-played loss costs less than a blowout. Matches with no "
            "performance data (most of the history - analysis is only retrievable for roughly the "
            "last 8-9 months of games) use flat win/loss. Each history entry records "
            "performance_method (v2 / legacy / null)."
        ),
        "notes": (
            "Elo computed only across unranked (ladder=0) matches where 2+ of the tracked "
            "players appear on opposing teams. Each player's expected score is computed against "
            "the average current Elo of tracked opponents on the other side(s) (untracked/random "
            "players in the match are ignored for rating purposes). This filtering (short-game "
            "and pre-start-date exclusion) only affects which matches feed the Elo calculation "
            "here — the underlying scraped datasets in unranked_raw/ are never modified. Any "
            "match under 15 minutes is excluded from the Elo calculation regardless of whether an "
            "AI was present, since it's too short to be a conclusive result. For longer matches "
            "with an AI player, the site's recorded win/loss is NOT trusted: because an AI can "
            "never resign, a game where the AI's human team-mates all quit first is still recorded "
            "as a win for the AI's team. Where schema-2 resign data exists the result is "
            "recomputed - the team whose human players have ALL resigned, earliest, is the loser. "
            "'All' rather than 'any', because in a team game one player often quits while their "
            "team-mates fight on and legitimately win. AI matches without resign data keep the "
            "recorded result (they can't be checked). See scrape_meta.ai_results_corrected."
        ),
    },
    "scrape_meta": {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_unique_unranked_matches_scanned": len(all_matches),
        "qualifying_matches_used": len(qualifying),
        "excluded_no_opposition": excluded_no_opposition,
        "excluded_short_game": len(excluded_short_game),
        "excluded_short_game_match_ids": excluded_short_game,
        "excluded_before_start_date": before_cutoff,
        "ai_results_corrected": len(ai_results_corrected),
        "ai_results_corrected_match_ids": ai_results_corrected,
        "results_repaired": len(results_repaired),
        "results_repaired_match_ids": results_repaired,
        "results_unresolved": len(results_unresolved),
        "results_unresolved_match_ids": sorted(results_unresolved),
        "structurally_unrateable": len(results_invalid),
        "structurally_unrateable_reasons": results_invalid,
    },
    "players": players_out,
    "match_log": match_log,
}

with open(OUT_PATH, "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2)

print(f"\nWrote {OUT_PATH}")
print("\nFinal Elo standings:")
for name, p in sorted(players_out.items(), key=lambda kv: kv[1]["current_elo"], reverse=True):
    print(f"  {name:20s} {p['current_elo']:8.1f}  ({p['matches_played']} matches, {p['win_rate']}% win rate)")
