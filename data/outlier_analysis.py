import json
import statistics
import os

_DATA_DIR = os.path.dirname(os.path.abspath(__file__))

ladder = json.load(open(os.path.join(_DATA_DIR, "unranked_ladder.json"), encoding='utf-8'))
perf = json.load(open(os.path.join(_DATA_DIR, "match_performance.json"), encoding='utf-8'))

TRACKED = {
    12047120: 'wabbit', 12676944: 'SauronSlayer', 12667372: 'zubair',
    12080589: 'l.inc', 12499000: 'toXic', 4607974: 'Strength & Honour',
    11907023: 'cheetah001', 12693189: 'NaKiyaKar', 12805097: 'neXus',
}

agg = {name: {'eapm': [], 'eco': [], 'mil': [], 'castle': []} for name in TRACKED.values()}
for match_id, players in perf['matches'].items():
    for pid, p in players.items():
        name = TRACKED.get(p.get('profile_id'))
        if not name:
            continue
        if p.get('eapm_mean') is not None:
            agg[name]['eapm'].append(p['eapm_mean'])
        if p.get('eco_apm_mean') is not None:
            agg[name]['eco'].append(p['eco_apm_mean'])
        if p.get('military_apm_mean') is not None:
            agg[name]['mil'].append(p['military_apm_mean'])
        if p.get('castle_time_ms') is not None:
            agg[name]['castle'].append(p['castle_time_ms'] / 60000)

print("Performance sample counts and averages:")
summary = {}
for name, vals in agg.items():
    n = len(vals['eapm'])
    if n == 0:
        print(f"{name:20s} no performance data yet")
        continue
    eapm_avg = statistics.mean(vals['eapm']) if vals['eapm'] else None
    eco_avg = statistics.mean(vals['eco']) if vals['eco'] else None
    mil_avg = statistics.mean(vals['mil']) if vals['mil'] else None
    castle_avg = statistics.mean(vals['castle']) if vals['castle'] else None
    summary[name] = {'n': n, 'eapm': eapm_avg, 'eco': eco_avg, 'mil': mil_avg, 'castle': castle_avg}
    print(f"{name:20s} n={n:3d} eAPM={eapm_avg:.1f} eco={eco_avg:.1f} mil={mil_avg:.1f} castle={castle_avg:.1f}min")

print()
print("Elo / win-rate summary:")
elo_summary = {}
for name, p in ladder['players'].items():
    elo_summary[name] = p
    print(f"{name:20s} elo={p['current_elo']:7.1f} matches={p['matches_played']:4d} win_rate={p['win_rate']:5.1f}%")

print()
print("Z-scores across players for each performance metric (n>=3 only):")
metrics = ['eapm', 'eco', 'mil', 'castle']
qualifying = {name: v for name, v in summary.items() if v['n'] >= 3}
for metric in metrics:
    values = [v[metric] for v in qualifying.values()]
    mean = statistics.mean(values)
    stdev = statistics.stdev(values) if len(values) > 1 else 0
    print(f"\n{metric}: mean={mean:.2f} stdev={stdev:.2f}")
    for name, v in qualifying.items():
        z = (v[metric] - mean) / stdev if stdev else 0
        flag = "  <== OUTLIER" if abs(z) >= 1.5 else ""
        print(f"  {name:20s} {v[metric]:8.2f}  z={z:+.2f}{flag}")

print()
print("Elo z-scores (all 9 players):")
elo_values = [p['current_elo'] for p in elo_summary.values()]
mean_elo = statistics.mean(elo_values)
stdev_elo = statistics.stdev(elo_values)
print(f"mean={mean_elo:.1f} stdev={stdev_elo:.1f}")
for name, p in elo_summary.items():
    z = (p['current_elo'] - mean_elo) / stdev_elo
    flag = "  <== OUTLIER" if abs(z) >= 1.5 else ""
    print(f"  {name:20s} {p['current_elo']:8.1f}  z={z:+.2f}{flag}")

print()
print("Win rate z-scores:")
wr_values = [p['win_rate'] for p in elo_summary.values()]
mean_wr = statistics.mean(wr_values)
stdev_wr = statistics.stdev(wr_values)
print(f"mean={mean_wr:.1f} stdev={stdev_wr:.1f}")
for name, p in elo_summary.items():
    z = (p['win_rate'] - mean_wr) / stdev_wr
    flag = "  <== OUTLIER" if abs(z) >= 1.5 else ""
    print(f"  {name:20s} {p['win_rate']:8.1f}  z={z:+.2f}{flag}")
