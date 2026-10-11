import json, math, random
from collections import defaultdict, Counter
import os
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..') + '/'
fx = json.load(open(R + 'data/cache/fixtures.json')); w = json.load(open(R + 'world.json'))
C = {c['id']: c for c in w['clubs']}
names = {'UEFA Champions League': 'cl', 'UEFA Europa League': 'el', 'UEFA Europa Conference League': 'ecl'}
part = {s: defaultdict(set) for s in ('25', '26')}
for f in fx.values():
    k = names.get(f.get('lg'))
    if not k: continue
    s = '25' if '2025-09-01' <= f['d'] <= '2026-01-31' else '26' if '2026-09-01' <= f['d'] <= '2027-01-31' else None
    if s: part[s][k].update([f['h'], f['a']])
pool = [c['id'] for c in w['clubs'] if c.get('n', 0) >= 10]
pset = set(pool)
elo_rank = {t: i + 1 for i, t in enumerate(sorted(pool, key=lambda t: -C[t]['elo']))}
for s in part:
    for k in ('cl', 'el', 'ecl'):
        p = part[s][k]; inpool = [t for t in p if t in pset]
        cc = Counter(C[t]['ctry'] for t in inpool)
        print(s, k, len(p), 'in pool', len(inpool), 'max per country', cc.most_common(3))
BINS = [(1, 20), (21, 40), (41, 80), (81, 150), (151, 999)]
def shares(ids):
    r = [C[t]['rank'] for t in ids]; n = len(r) or 1
    return [sum(1 for x in r if lo <= x <= hi) / n for lo, hi in BINS]
real = {k: shares([t for t in part['26'][k] if t in pset]) for k in ('cl', 'el', 'ecl')}
print('real 2026/27 bin shares', {k: [round(x, 2) for x in v] for k, v in real.items()})
CAP = {'cl': 5, 'el': 4, 'ecl': 3}
def sim(wu, sig, caps=CAP, n=200, seed=1):
    rnd = random.Random(seed); acc = {k: [0] * len(BINS) for k in CAP}
    for _ in range(n):
        key = {t: wu * math.log(C[t]['rank']) + (1 - wu) * math.log(elo_rank[t]) + sig * rnd.gauss(0, 1) for t in pool}
        order = sorted(pool, key=key.get); fields = {k: [] for k in CAP}; cnt = {k: Counter() for k in CAP}
        for t in order:
            for k in ('cl', 'el', 'ecl'):
                if len(fields[k]) >= 35 or cnt[k][C[t]['ctry']] >= caps[k]: continue
                fields[k].append(t); cnt[k][C[t]['ctry']] += 1; break
        for k in CAP:
            sh = shares(fields[k]); acc[k] = [a + b for a, b in zip(acc[k], sh)]
    return {k: [x / n for x in v] for k, v in acc.items()}
best = []
for wu in [0, .25, .5, .75, 1]:
    for sig in [.4, .55, .7, .85, 1.0, 1.2]:
        s_ = sim(wu, sig)
        err = sum((a - b) ** 2 for k in CAP for a, b in zip(s_[k], real[k]))
        best.append((err, wu, sig, s_))
best.sort(key=lambda x: x[0])
for e, wu, sig, s_ in best[:5]:
    print(round(e, 4), 'uefa weight', wu, 'sigma', sig, {k: [round(x, 2) for x in v] for k, v in s_.items()})
cur = [b for b in best if b[1] == .5 and b[2] == .55]
print('current (0.5, ~0.65):', [round(x[0], 4) for x in cur])
print('--- caps cl5 el3 ecl4')
caps2 = {'cl': 5, 'el': 3, 'ecl': 4}
for wu, sig in [(0, .7), (.5, .85), (.5, .75), (.5, .95), (.25, .8)]:
    s_ = sim(wu, sig, caps2, n=300, seed=3)
    err = sum((a - b) ** 2 for k in CAP for a, b in zip(s_[k], real[k]))
    real25 = {k: shares([t for t in part['25'][k] if t in pset]) for k in CAP}
    e25 = sum((a - b) ** 2 for k in CAP for a, b in zip(s_[k], real25[k]))
    print(wu, sig, 'err 26/27', round(err, 4), 'err 25/26', round(e25, 4), {k: [round(x, 2) for x in v] for k, v in s_.items()})
