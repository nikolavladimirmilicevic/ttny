"""Out-of-sample test of the club strength model: fit on the first part of the window, predict the rest."""
import json, math, sys, datetime as dt
from collections import defaultdict
import os
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..') + '/'
world = json.load(open(R + 'world.json')); fx = json.load(open(R + 'data/cache/fixtures.json'))
FIN = {'FT', 'AET', 'PEN'}
club = {c['id']: c for c in world['clubs']}
pts = {c['id']: (c.get('pts') or 0.5) for c in world['clubs']}
TODAY = dt.date.fromisoformat(world['updated']); SINCE = TODAY - dt.timedelta(days=365)
ms = sorted([f for f in fx.values() if f['s'] in FIN and f['hg'] is not None and SINCE.isoformat() <= f['d'] <= TODAY.isoformat()
             and f['h'] in club and f['a'] in club], key=lambda f: f['d'])
def pct(vals):
    s = sorted(vals); n = len(s)
    def p(v):
        lo = sum(1 for x in s if x < v); eq = sum(1 for x in s if x == v); return (lo + eq / 2) / n
    return p
def res(h, a): return 0 if h > a else 1 if h == a else 2
def P3(gf, ga): return 3 if gf > ga else 1 if gf == ga else 0
def probs(sh, sa, base, k):
    H, D, A = base; xh = k * (2 * sh - 1); xa = k * (2 * sa - 1)
    h = [H * (1 + xh), D, A * (1 - xh)]; a = [H * (1 - xa), D, A * (1 + xa)]
    th, ta = sum(h), sum(a); q = [h[i] / th * a[i] / ta / [H, D, A][i] for i in range(3)]; t = sum(q); return [x / t for x in q]

def strengths(train, mode, W=.5):
    rows = defaultdict(list)   # club -> [(opp, home, gf, ga)] newest first
    for f in reversed(train):
        rows[f['h']].append((f['a'], 1, f['hg'], f['ag'])); rows[f['a']].append((f['h'], 0, f['ag'], f['hg']))
    pool = [c for c in rows if len(rows[c]) >= 10]
    if mode == 'adjusted':
        allr = [(c, *r) for c in rows for r in rows[c]]
        ids = list(rows); att = {i: 1 for i in ids}; leak = {i: 1 for i in ids}; prt = {i: 0 for i in ids}
        mu = sum(r[3] for r in allr) / len(allr); hf = 1.1; PR = 3
        for _ in range(40):
            na = {i: PR * mu for i in ids}; da = dict(na); nl = dict(na); dl = dict(na); hn = hd = 0
            for i, j, h, gf, ga in allr:
                fct = hf if h else 1 / hf
                na[i] += gf; da[i] += mu * leak[j] * fct; nl[j] += gf; dl[j] += mu * att[i] * fct
                if h: hn += gf; hd += mu * att[i] * leak[j]
            for i in ids: att[i] = na[i] / da[i]; leak[i] = nl[i] / dl[i]
        ppm = sum(P3(r[3], r[4]) for r in allr) / len(allr)
        for _ in range(60):
            sm = {i: 0 for i in ids}; n = {i: PR for i in ids}
            for i, j, h, gf, ga in allr: sm[i] += P3(gf, ga) - ppm + prt[j]; n[i] += 1
            nx = {i: sm[i] / n[i] for i in ids}; m0 = sum(nx.values()) / len(nx)
            for i in ids: prt[i] = .5 * prt[i] + .5 * (nx[i] - m0)
        fP = lambda r: P3(r[2], r[3]) + prt[r[0]]; fG = lambda r: r[2] / leak[r[0]]; fA = lambda r: r[3] / att[r[0]]
        avg = lambda rs, f: sum(f(r) for r in rs) / len(rs) if rs else 0
    else:
        fP = lambda r: P3(r[2], r[3]); fG = lambda r: r[2]; fA = lambda r: r[3]
        def avg(rs, f):
            w = sum(pts.get(r[0], .5) for r in rs); return sum(pts.get(r[0], .5) * f(r) for r in rs) / w if w else 0
    v = {c: {'uefa': pts[c], 'ppg': avg(rows[c], fP), 'ppg5': avg(rows[c][:5], fP), 'h': avg([r for r in rows[c] if r[1]], fP),
             'a': avg([r for r in rows[c] if not r[1]], fP), 'gf': avg(rows[c], fG), 'ga': -avg(rows[c], fA)} for c in pool}
    q = {k: pct([v[c][k] for c in pool]) for k in ['uefa', 'ppg', 'ppg5', 'h', 'a', 'gf', 'ga']}
    S = {}
    for c in pool:
        Q = {k: q[k](v[c][k]) for k in q}
        if mode == 'uefa': S[c] = (Q['uefa'], Q['uefa']); continue
        rest = Q['ppg'] + Q['ppg5'] + Q['gf'] + Q['ga']
        S[c] = (W * Q['uefa'] + (1-W) * (rest + Q['h']) / 5, W * Q['uefa'] + (1-W) * (rest + Q['a']) / 5)
    return S

def run(cut_days):
    cut = (TODAY - dt.timedelta(days=cut_days)).isoformat()
    train = [f for f in ms if f['d'] < cut]; test = [f for f in ms if f['d'] >= cut]
    n = len(train); base = [sum(1 for f in train if res(f['hg'], f['ag']) == o) / n for o in range(3)]
    out = {'train': len(train), 'test': len(test)}
    def score(pfun, data):
        ll = br = acc = 0; m = 0
        for f in data:
            p = pfun(f)
            if p is None: continue
            o = res(f['hg'], f['ag']); ll -= math.log(max(1e-9, p[o])); br += sum((p[i] - (i == o)) ** 2 for i in range(3)); acc += (max(range(3), key=lambda i: p[i]) == o); m += 1
        return m, ll / m, br / m, acc / m
    out['base rates only'] = score(lambda f: base, test)
    for mode, label, W in [('uefa', 'UEFA points only', 1)] + [('adjusted', f'new, UEFA weight {w}', w) for w in (.3,.5,.6,.7,.8,.9)]:
        S = strengths(train, mode, W)
        fitset = [f for f in train if f['h'] in S and f['a'] in S]
        best = max((sum(math.log(max(1e-9, probs(S[f['h']][0], S[f['a']][1], base, k)[res(f['hg'], f['ag'])])) for f in fitset), k) for k in [i / 50 for i in range(61)])[1]
        out[label] = score(lambda f: probs(S[f['h']][0], S[f['a']][1], base, best) if f['h'] in S and f['a'] in S else None, test) + (best,)
    return out

for cd in [90, 120]:
    r = run(cd); print(f"\n== fit on matches before the last {cd} days ({r['train']} matches), test on the last {cd} days ({r['test']}) ==")
    for k, v in r.items():
        if isinstance(v, tuple): print(f"{k:32s} n={v[0]:4d}  logloss={v[1]:.4f}  brier={v[2]:.4f}  hit={v[3]:.3f}" + (f"  k={v[4]}" if len(v) > 4 else ''))
