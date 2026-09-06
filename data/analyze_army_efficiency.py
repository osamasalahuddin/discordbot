"""Army-efficiency analysis over schema-2 performance records.

Derives, per player per match, from military_by_type + military_techs + uptimes:
  - army_resources        total food/wood/gold invested in military (relative units)
  - gold_share            fraction of that investment that is gold
  - trash_share           fraction of army pop in trash (non-gold) classes
  - army_value_ratio      own army_resources vs opponent average  (0.5 == on par)
  - upgrade_coverage      of the upgrades that matter for the army you built and
                          the age you reached, how many did you research (0..1)
  - counter_score         does your composition beat the opponent's? (-1..+1)
  - composition           class -> unit count

Standalone: prints a per-match breakdown for the most recent matches plus a
per-player summary, and writes army_efficiency.json. Nothing here touches the
ladder yet.
"""
import json
import sys
from collections import Counter, defaultdict
import os

_DATA_DIR = os.path.dirname(os.path.abspath(__file__))

DATA = _DATA_DIR
PERF_PATH = os.path.join(DATA, "match_performance.json")
UNIT_DATA_PATH = os.path.join(DATA, "aoe2_unit_data.json")
LADDER_PATH = os.path.join(DATA, "unranked_ladder.json")
OUT_PATH = os.path.join(DATA, "army_efficiency.json")

TRACKED = {
    12047120: "wabbit", 12676944: "SauronSlayer", 12667372: "zubair", 12080589: "l.inc",
    12499000: "toXic", 4607974: "Strength & Honour", 11907023: "cheetah001",
    12693189: "NaKiyaKar", 12805097: "neXus",
}
AGE_ORDER = {"dark": 0, "feudal": 1, "castle": 2, "imperial": 3}
MIN_CLASS_COUNT = 5  # a class must be built at least this many times to "count" for upgrade coverage

U = json.load(open(UNIT_DATA_PATH, encoding="utf-8"))
CLASSES = U["classes"]
UNIT_TO_CLASS = U["unit_to_class"]
UPGRADE_CATS = U["upgrade_categories"]


def reached_age(rec):
    if rec.get("imperial_time_ms"):
        return "imperial"
    if rec.get("castle_time_ms"):
        return "castle"
    if rec.get("feudal_time_ms"):
        return "feudal"
    return "dark"


def classify(mil_by_type):
    out = Counter()
    unknown = Counter()
    for name, n in (mil_by_type or {}).items():
        cls = UNIT_TO_CLASS.get(name)
        if cls is None:
            unknown[name] += n
            cls = "other"
        out[cls] += n
    return out, unknown


def army_resources(comp):
    f = w = g = 0
    for cls, n in comp.items():
        c = CLASSES.get(cls, CLASSES["other"])["cost"]
        f += c["food"] * n
        w += c["wood"] * n
        g += c["gold"] * n
    return f, w, g


def pop_shares(comp):
    tot = sum(CLASSES.get(c, CLASSES["other"])["pop"] * n for c, n in comp.items())
    if tot <= 0:
        return {}, 0
    return {c: CLASSES.get(c, CLASSES["other"])["pop"] * n / tot for c, n in comp.items()}, tot


def upgrade_coverage(rec, comp, age):
    researched = {t for t, _ in (rec.get("military_techs") or [])}
    max_age = AGE_ORDER[age]
    got = avail = 0
    detail = {}
    for cat, meta in UPGRADE_CATS.items():
        if AGE_ORDER.get(meta["age"], 3) > max_age:
            continue
        if not any(comp.get(cls, 0) >= MIN_CLASS_COUNT for cls in meta["applies_to"]):
            continue
        techs = meta["techs"]
        g = sum(1 for t in techs if t in researched)
        got += g
        avail += len(techs)
        detail[cat] = f"{g}/{len(techs)}"
    return (got / avail if avail else None), detail


def counter_score(my_shares, opp_shares):
    """+ if my composition is strong into theirs, - if it feeds theirs."""
    s = 0.0
    for ci, si in my_shares.items():
        info = CLASSES.get(ci, CLASSES["other"])
        for cj, sj in opp_shares.items():
            if cj in info["strong_vs"]:
                s += si * sj
            elif cj in info["weak_vs"]:
                s -= si * sj
    return s


def pair_ratio(own, opp, higher_better=True):
    tot = own + opp
    if tot <= 0:
        return None
    return own / tot if higher_better else opp / tot


def analyze():
    perf = json.load(open(PERF_PATH, encoding="utf-8"))
    ladder = {m["match_id"]: m for m in json.load(open(LADDER_PATH, encoding="utf-8"))["match_log"]}

    results = {}
    unknown_units = Counter()
    per_player = defaultdict(list)

    for mid, rec in perf["matches"].items():
        if rec.get("_meta", {}).get("schema") != 2:
            continue
        players = {k: v for k, v in rec.items() if k != "_meta"}
        by_team = defaultdict(list)
        for pid, p in players.items():
            if p["profile_id"] in TRACKED:
                by_team[p["team"]].append((pid, p))
        if len(by_team) < 2:
            continue

        won_team = None
        lm = ladder.get(int(mid))
        if lm:
            for t in lm["teams"]:
                if t["won"]:
                    tracked_paths = [pl["user_path"] for pl in t["players"] if pl.get("tracked")]
                    for pid, p in players.items():
                        if f"/user/{p['profile_id']}/" in tracked_paths:
                            won_team = p["team"]

        match_out = {"map": rec["_meta"].get("map"), "duration_min": round((rec["_meta"].get("duration_ms") or 0) / 60000, 1), "players": {}}

        comps = {}
        for team, members in by_team.items():
            for pid, p in members:
                comp, unk = classify(p.get("military_by_type"))
                unknown_units.update(unk)
                comps[pid] = (team, p, comp)

        for pid, (team, p, comp) in comps.items():
            opps = [(c, pp) for opid, (t, pp, c) in comps.items() if t != team]
            if not opps:
                continue
            age = reached_age(p)
            f, w, g = army_resources(comp)
            total_res = f + w + g
            my_shares, my_pop = pop_shares(comp)
            trash = sum(s for c, s in my_shares.items() if "trash" in CLASSES.get(c, CLASSES["other"])["tags"])

            opp_res = []
            opp_share_acc = Counter()
            for c, pp in opps:
                of, ow, og = army_resources(c)
                opp_res.append(of + ow + og)
                sh, _ = pop_shares(c)
                for k, v in sh.items():
                    opp_share_acc[k] += v / len(opps)

            cov, cov_detail = upgrade_coverage(p, comp, age)
            cscore = counter_score(my_shares, dict(opp_share_acc))
            opp_avg_res = sum(opp_res) / len(opp_res) if opp_res else 0

            name = TRACKED[p["profile_id"]]
            entry = {
                "won": (won_team == team) if won_team is not None else None,
                "reached_age": age,
                "military_trained": p.get("military_trained"),
                "army_food": f, "army_wood": w, "army_gold": g,
                "army_resources": total_res,
                "gold_share": round(g / total_res, 3) if total_res else None,
                "trash_share": round(trash, 3),
                "army_value_ratio": round(pair_ratio(total_res, opp_avg_res), 3) if opp_avg_res else None,
                "upgrade_coverage": round(cov, 3) if cov is not None else None,
                "upgrade_detail": cov_detail,
                "counter_score": round(cscore, 3),
                "composition": dict(comp.most_common()),
            }
            match_out["players"][name] = entry
            per_player[name].append(entry)

        results[mid] = match_out

    json.dump(results, open(OUT_PATH, "w", encoding="utf-8"), indent=2)

    # ---- recent-match breakdown ----
    recent = sorted(results.items(), key=lambda kv: perf["matches"][kv[0]]["_meta"].get("duration_ms", 0))
    recent = sorted(results.items(), key=lambda kv: int(kv[0]))[-6:]
    for mid, mo in recent:
        print(f"\n=== match {mid}  {mo['map']}  {mo['duration_min']}min ===")
        print(f"  {'player':16s} {'age':4s} {'W/L':3s} {'mil':>5s} {'res':>7s} {'gold%':>6s} {'trash%':>7s} {'val_r':>6s} {'upg':>5s} {'ctr':>6s}")
        for name, e in sorted(mo["players"].items(), key=lambda kv: -(kv[1]["army_resources"] or 0)):
            wl = "W" if e["won"] else ("L" if e["won"] is False else "?")
            print(f"  {name:16s} {e['reached_age'][:4]:4s} {wl:3s} {e['military_trained'] or 0:5d} "
                  f"{e['army_resources']:7d} {(e['gold_share'] or 0)*100:5.0f}% {(e['trash_share'])*100:6.0f}% "
                  f"{e['army_value_ratio'] if e['army_value_ratio'] is not None else 0:6.2f} "
                  f"{e['upgrade_coverage'] if e['upgrade_coverage'] is not None else 0:5.2f} {e['counter_score']:+6.2f}")
            top = list(e["composition"].items())[:4]
            print(f"      {', '.join(f'{c} x{n}' for c, n in top)}")

    # ---- per-player averages ----
    print("\n\n================  PER-PLAYER AVERAGES (schema-2 matches)  ================")
    print(f"{'player':16s} {'n':>3s} {'gold%':>6s} {'trash%':>7s} {'val_ratio':>10s} {'upg_cov':>8s} {'counter':>8s} {'winrate':>8s}")
    def avg(xs):
        xs = [x for x in xs if x is not None]
        return sum(xs) / len(xs) if xs else float("nan")
    for name, rows in sorted(per_player.items(), key=lambda kv: -avg([r["counter_score"] for r in kv[1]])):
        wins = [r["won"] for r in rows if r["won"] is not None]
        print(f"{name:16s} {len(rows):3d} {avg([r['gold_share'] for r in rows])*100:5.0f}% "
              f"{avg([r['trash_share'] for r in rows])*100:6.0f}% {avg([r['army_value_ratio'] for r in rows]):10.3f} "
              f"{avg([r['upgrade_coverage'] for r in rows]):8.3f} {avg([r['counter_score'] for r in rows]):+8.3f} "
              f"{(sum(wins)/len(wins)*100 if wins else float('nan')):7.0f}%")

    if unknown_units:
        print(f"\nUnmapped unit names (fell back to 'other'): {dict(unknown_units.most_common(20))}")
    print(f"\nFull per-match data -> {OUT_PATH}")


if __name__ == "__main__":
    analyze()
