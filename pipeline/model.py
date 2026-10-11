"""The rating model, built from scratch.

1. Clubs: an Elo rating over every match in the data, seeded from UEFA points, then blended with that seed
   ("prestige") so the giants are a little sticky.
2. Matches: expected goals for each side from the rating gap (Poisson, fitted), with the Dixon-Coles correction for
   low scores. The result follows from the score.
3. Players: an overall from level (the clubs he played for), role (how regularly he starts) and performance
   (the API's match ratings against his own line, adjusted for the opponent), on a 40-99 scale like eFootball.
4. Your XI: the line that maps a club's own best XI to its rating maps yours to a rating too.
5. Credibility tests and an out-of-sample backtest, written to a report.
"""
import math, random, statistics
from collections import defaultdict, Counter

FINISHED = {'FT', 'AET', 'PEN'}
PRESTIGE = 0.20          # share of the UEFA seed kept in the final club rating
K_BASE = 20              # Elo step
HFA_ELO = 60             # home advantage inside the Elo updates
SHARE = {'level': 0.55, 'role': 0.15, 'perf': 0.30}
OVR_MEAN, OVR_SD = 72, 7.5
PERF_PRIOR = 10          # average matches of shrinkage toward his line's mean match rating
EXPERIENCE_STARTS = 10   # starts for a full rating; fewer pull the score toward a squad player's
SQUAD_Z = -0.5           # the score a player with almost no starts is pulled toward (a squad player, about a D)
SECONDARY_PENALTY = 3    # overall points a player loses away from his main position (applied in the game)
LINE = {'GK': 'GK', 'CB': 'DEF', 'LB': 'DEF', 'RB': 'DEF', 'DMF': 'MID', 'CMF': 'MID', 'LMF': 'MID', 'RMF': 'MID',
        'AMF': 'MID', 'LWF': 'FWD', 'RWF': 'FWD', 'CF': 'FWD'}
ND = statistics.NormalDist()


def seed(points):
    """UEFA points -> starting Elo (130 points ~ 2030, 10 ~ 1660, 1 ~ 1400)."""
    return 1300 + 150 * math.log1p(max(0.0, points or 0))


def g_mult(gd):
    gd = abs(gd)
    return 1 if gd <= 1 else 1.5 if gd == 2 else (11 + gd) / 8


# ---------------- clubs ----------------
def run_elo(fx, top_pts, until, national=frozenset(), since=''):
    """Chronological Elo over every finished match. Returns current ratings and the pre-match ratings per fixture.
    National teams form their own pool, starting at 1500; other clubs off the UEFA list start a notch below the first
    rated side they meet (or at 1450 when neither side is rated yet)."""
    R, pre = {}, {}
    for tid, pts in top_pts.items():
        R[tid] = seed(pts)
    games = sorted((f['d'], fid, f) for fid, f in fx.items() if f['s'] in FINISHED and f['hg'] is not None and since <= f['d'] <= until)   # the last 365 days only
    for d, fid, f in games:
        h, a = f['h'], f['a']
        for t, o in ((h, a), (a, h)):
            if t not in R:
                R[t] = 1500.0 if t in national else min(1500.0, R[o] - 150) if o in R else 1450.0
        rh, ra = R[h], R[a]
        pre[fid] = (rh, ra)
        we = 1 / (10 ** (-(rh + HFA_ELO - ra) / 400) + 1)
        w = 1 if f['hg'] > f['ag'] else 0.5 if f['hg'] == f['ag'] else 0
        k = K_BASE * (0.5 if 'friendl' in (f.get('lg') or '').lower() else 1) * g_mult(f['hg'] - f['ag'])
        R[h] = rh + k * (w - we)
        R[a] = ra - k * (w - we)
    return R, pre


def blend(elo, tid, top_pts):
    return (1 - PRESTIGE) * elo + PRESTIGE * seed(top_pts[tid]) if tid in top_pts else elo


# ---------------- matches ----------------
def fit_goals(rows):
    """rows: (rating gap home-away in Elo, home goals, away goals). log lh = a + h + b*d, log la = a - b*d, d = gap/100.
    Newton's method on the Poisson likelihood; then the Dixon-Coles rho on a grid."""
    a, h, b = 0.3, 0.2, 0.15
    for _ in range(50):
        g = [0.0, 0.0, 0.0]; H = [[0.0] * 3 for _ in range(3)]
        for gap, x, y in rows:
            d = gap / 100
            lh = math.exp(a + h + b * d); la = math.exp(a - b * d)
            # gradient and Hessian of sum(x log lh - lh + y log la - la)
            for vec, lam, obs in (((1, 1, d), lh, x), ((1, 0, -d), la, y)):
                for i in range(3):
                    g[i] += vec[i] * (obs - lam)
                    for j in range(3):
                        H[i][j] -= vec[i] * vec[j] * lam
        step = solve3(H, g)
        a, h, b = a - step[0], h - step[1], b - step[2]
        if max(abs(s) for s in step) < 1e-7:
            break
    best = None
    for k in range(-25, 11):
        rho = k / 100; ll = 0
        for gap, x, y in rows:
            d = gap / 100; lh = math.exp(a + h + b * d); la = math.exp(a - b * d)
            t = tau(x, y, lh, la, rho)
            if t <= 0:
                ll = -1e18; break
            ll += math.log(t)
        if best is None or ll > best[0]:
            best = (ll, rho)
    return {'a': round(a, 5), 'h': round(h, 5), 'b': round(b, 5), 'rho': best[1]}


def solve3(A, y):
    """Solve A x = y for a 3x3 system (Cramer)."""
    def det(M):
        return (M[0][0] * (M[1][1] * M[2][2] - M[1][2] * M[2][1]) - M[0][1] * (M[1][0] * M[2][2] - M[1][2] * M[2][0])
                + M[0][2] * (M[1][0] * M[2][1] - M[1][1] * M[2][0]))
    D = det(A) or 1e-12
    out = []
    for c in range(3):
        M = [row[:] for row in A]
        for r in range(3):
            M[r][c] = y[r]
        out.append(det(M) / D)
    return out


def tau(x, y, lh, la, rho):
    if x == 0 and y == 0: return 1 - lh * la * rho
    if x == 0 and y == 1: return 1 + lh * rho
    if x == 1 and y == 0: return 1 + la * rho
    if x == 1 and y == 1: return 1 - rho
    return 1.0


def lambdas(G, gap, neutral=False):
    d = gap / 100
    return math.exp(G['a'] + (0 if neutral else G['h']) + G['b'] * d), math.exp(G['a'] - G['b'] * d)


def score_matrix(G, gap, neutral=False, n=9):
    lh, la = lambdas(G, gap, neutral)
    ph = [math.exp(-lh) * lh ** i / math.factorial(i) for i in range(n)]
    pa = [math.exp(-la) * la ** i / math.factorial(i) for i in range(n)]
    M = [[ph[i] * pa[j] * tau(i, j, lh, la, G['rho']) for j in range(n)] for i in range(n)]
    s = sum(map(sum, M))
    return [[v / s for v in row] for row in M]


def wdl(G, gap, neutral=False):
    M = score_matrix(G, gap, neutral)
    n = len(M)
    return (sum(M[i][j] for i in range(n) for j in range(n) if i > j), sum(M[i][i] for i in range(n)),
            sum(M[i][j] for i in range(n) for j in range(n) if i < j))


def draw_score(G, gap, rnd, neutral=False):
    M = score_matrix(G, gap, neutral); u = rnd.random()
    for i, row in enumerate(M):
        for j, v in enumerate(row):
            u -= v
            if u <= 0:
                return i, j
    return 1, 1


# ---------------- players ----------------
def player_ratings(world, fx, lu, evs, pst, rating_now, since, until, national=frozenset(), club_cap=None):
    """Overall and the four shown parts for every rated player."""
    players = world['players']
    ids = {p['id'] for p in players}
    starts = defaultdict(list)       # pid -> [(date, team, opp, gf, ga, minutes, api rating, g+a, clean sheet in a defensive slot)]
    team_games = defaultdict(list)   # team -> dates of its matches with a line-up
    for fid, f in fx.items():
        if f['s'] not in FINISHED or f['hg'] is None or not (since <= f['d'] <= until):
            continue
        sides = lu.get(fid) or {}
        ps = pst.get(fid) or {}
        ga_by = Counter(); as_by = Counter()
        for e in evs.get(fid) or []:
            if e[3] != 'o':
                if e[1]: ga_by[e[1]] += 1
                if e[2]: as_by[e[2]] += 1
        for side, opp, gf, gc in ((f['h'], f['a'], f['hg'], f['ag']), (f['a'], f['h'], f['ag'], f['hg'])):
            l = sides.get(str(side))
            if not isinstance(l, dict) or not l.get('xi'):
                continue
            team_games[side].append(f['d'])
            for e in l['xi']:
                pid = e[0]
                if pid not in ids:
                    continue
                st = ps.get(str(pid)) or [90, None, side]
                starts[pid].append((f['d'], side, opp, gf, gc, st[0] or 90, st[1], ga_by[pid] + as_by[pid]))
    R = lambda t: rating_now.get(t, 1400.0)
    nat_vals = [rating_now[t] for t in national if t in rating_now]
    nat_mean = statistics.mean(nat_vals) if nat_vals else 1500.0
    # an opponent's strength on one scale: national teams measured from their own pool's average
    OPP = lambda t: (R(t) - nat_mean) / 100 if t in national else (R(t) - 1700) / 100
    ranked = set(world.get('_ranked', []))
    # a club off the UEFA list counts at most as a lower-quarter UEFA club for a player's level
    LVL = lambda t: R(t) if t in ranked or club_cap is None else min(R(t), club_cap)

    # opponent effect on the API rating: slope of rating on opponent strength, pooled over all starts
    xs, ys = [], []
    for pid, ss in starts.items():
        for s in ss:
            if s[6] is not None and s[5] >= 30:
                xs.append(OPP(s[2])); ys.append(s[6])
    slope = 0.0
    if len(xs) > 500:
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / max(1e-9, sum((x - mx) ** 2 for x in xs))

    raw = {}
    for p in players:
        ss = sorted(starts.get(p['id'], []), reverse=True)
        if not ss:
            raw[p['id']] = None; continue
        cl = [s for s in ss if s[1] not in national]   # club starts only: a national team says nothing about his level
        level = sum(LVL(s[1]) for s in cl) / len(cl) if cl else None
        # role: starts for each club over that club's matches while he was there, weighted by starts
        by_team = defaultdict(list)
        for s in cl:
            by_team[s[1]].append(s[0])
        # a stint runs from his first start for a club until his first start for another club after it (or today),
        # so a long injury or a place on the bench counts against him
        firsts = sorted((min(ds), t) for t, ds in by_team.items())
        role = 0
        for k, (lo, t) in enumerate(firsts):
            lo = since if k == 0 else lo   # his first club counts from the start of the window: time out injured counts too
            hi = firsts[k + 1][0] if k + 1 < len(firsts) else until
            n = sum(1 for d in team_games[t] if lo <= d <= hi) or len(by_team[t])
            role += len(by_team[t]) * min(1.0, len(by_team[t]) / n)
        role = role / len(cl) if cl else 0.0
        rated = [(s[6] - slope * OPP(s[2])) for s in ss if s[6] is not None and s[5] >= 30]
        form = [s[6] for s in ss[:5] if s[6] is not None]
        ga90 = sum(s[7] for s in ss) / sum(max(30, s[5]) for s in ss) * 90
        cs = sum(1 for s in ss if s[4] == 0) / len(ss)
        conc = sum(s[4] / max(0.3, math.exp(0)) for s in ss) / len(ss)
        raw[p['id']] = {'level': level, 'role': role, 'perf': (sum(rated), len(rated)), 'form': form, 'ga90': ga90,
                        'cs': cs, 'conc': conc, 'line': LINE.get(p['prim'], 'MID')}

    # a player seen only with his national team: his current club stands in for his level
    for p in players:
        r = raw.get(p['id'])
        if r and r['level'] is None:
            r['level'] = LVL(p['club'])
    ok = [p for p in players if raw.get(p['id'])]
    # performance: shrunk mean API rating, as a z-score within his line
    by_line = defaultdict(list)
    for p in ok:
        by_line[raw[p['id']]['line']].append(p['id'])
    perf_z, form_z = {}, {}
    for line, pids in by_line.items():
        rated = [raw[i]['perf'] for i in pids if raw[i]['perf'][1] >= 5]
        m0 = sum(s for s, n in rated) / max(1, sum(n for s, n in rated)) if rated else 6.8
        vals = {i: (raw[i]['perf'][0] + PERF_PRIOR * m0) / (raw[i]['perf'][1] + PERF_PRIOR) for i in pids}
        mu, sd = statistics.mean(vals.values()), statistics.pstdev(vals.values()) or 1
        for i in pids:
            perf_z[i] = (vals[i] - mu) / sd
        fv = {i: (sum(raw[i]['form']) + 2 * m0) / (len(raw[i]['form']) + 2) for i in pids}
        fm, fs = statistics.mean(fv.values()), statistics.pstdev(fv.values()) or 1
        for i in pids:
            form_z[i] = (fv[i] - fm) / fs
    z = lambda key: zscores({p['id']: raw[p['id']][key] for p in ok})
    lz, rz = z('level'), z('role')
    starts_of = {p['id']: len(starts.get(p['id'], [])) for p in ok}
    def blended(i):
        sc = SHARE['level'] * lz[i] + SHARE['role'] * rz[i] + SHARE['perf'] * perf_z[i]
        e = min(1.0, starts_of[i] / EXPERIENCE_STARTS)   # 1 start = 10% of his own score, 10+ starts = all of it
        return e * sc + (1 - e) * SQUAD_Z
    score = {i: blended(i) for i in lz}
    pct = pct_ranks(score)
    # the parts shown on the card, as percentiles: level among all; attack, defence and form within the line
    att, dfn = {}, {}
    for line, pids in by_line.items():
        a = pct_ranks({i: raw[i]['ga90'] for i in pids})
        dval = {i: raw[i]['cs'] - 0.25 * raw[i]['conc'] for i in pids}
        d_ = pct_ranks(dval)
        att.update(a); dfn.update(d_)
    lvl_p, frm_p = pct_ranks({i: raw[i]['level'] for i in lz}), pct_ranks(form_z)
    out = {}
    for i in score:
        ovr = OVR_MEAN + OVR_SD * ND.inv_cdf(min(0.9995, max(0.0005, pct[i])))
        out[i] = {'ovr': round(max(45, min(97, ovr)), 1), 'lvl': round(lvl_p[i], 3), 'att': round(att[i], 3),
                  'def': round(dfn[i], 3), 'frm': round(frm_p[i], 3)}
    return out, {'slope': round(slope, 4), 'rated_starts': len(xs)}


def zscores(d):
    v = list(d.values()); m = statistics.mean(v); s = statistics.pstdev(v) or 1
    return {k: (x - m) / s for k, x in d.items()}


def pct_ranks(d):
    items = sorted(d.items(), key=lambda kv: kv[1]); n = len(items); out = {}; i = 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        for k in range(i, j + 1):
            out[items[k][0]] = (i + (j - i + 1) / 2) / n
        i = j + 1
    return out


# ---------------- the whole thing ----------------
def build(world, fx, lu, evs, pst, top_clubs, since, until, log=print, national=frozenset()):
    """top_clubs: {api id: UEFA club dict with 'points' and 'rank'}. Adds ratings to world in place; returns a report."""
    top_pts = {tid: (c.get('points') or 0.5) for tid, c in top_clubs.items()}
    elo, pre = run_elo(fx, top_pts, until, national, since)
    rating = {t: blend(e, t, top_pts) for t, e in elo.items()}
    for c in world['clubs']:
        c['elo'] = round(rating.get(c['id'], seed(top_pts.get(c['id'], 0.5))), 1)
        c['seed'] = round(seed(top_pts.get(c['id'], 0.5)), 1)

    # goals model on matches between ranked clubs in the window, from blended pre-match ratings
    def gap_of(fid, f):
        rh, ra = pre[fid]
        return blend(rh, f['h'], top_pts) - blend(ra, f['a'], top_pts)
    rows = [(gap_of(fid, f), f['hg'], f['ag']) for fid, f in fx.items()
            if fid in pre and f['s'] in FINISHED and since <= f['d'] <= until and f['h'] in top_pts and f['a'] in top_pts]
    G = fit_goals(rows)
    log(f"  goals model on {len(rows)} matches: {G}")

    ranked_r = sorted(rating[t] for t in top_pts if t in rating)
    cap = ranked_r[len(ranked_r) // 4] if ranked_r else None
    world['_ranked'] = list(top_pts)
    ratings, pinfo = player_ratings(world, fx, lu, evs, pst, rating, since, until, national, cap)
    world.pop('_ranked', None)
    pinfo['foreign_club_cap'] = round(cap or 0)
    for p in world['players']:
        r = ratings.get(p['id'])
        if r:
            p.update(r)
    # your XI: a club's own XI (its 11 most-used rated players) -> its rating, as a straight line
    byclub = defaultdict(list)
    for p in world['players']:
        if 'ovr' in p:
            byclub[p['club']].append(p)
    pts = []
    for c in world['clubs']:
        ps = sorted(byclub.get(c['id'], []), key=lambda p: -p['starts'])[:11]
        if len(ps) == 11:
            pts.append((sum(p['ovr'] for p in ps) / 11, c['elo']))
    mx = sum(x for x, _ in pts) / len(pts); my = sum(y for _, y in pts) / len(pts)
    beta = sum((x - mx) * (y - my) for x, y in pts) / max(1e-9, sum((x - mx) ** 2 for x, _ in pts))
    alpha = my - beta * mx
    corr = beta * statistics.pstdev([x for x, _ in pts]) / (statistics.pstdev([y for _, y in pts]) or 1)
    world['model'] = {'goals': G, 'xi': {'alpha': round(alpha, 3), 'beta': round(beta, 3), 'clubs': len(pts), 'r': round(corr, 3)},
                      'prestige': PRESTIGE, 'secondary_penalty': SECONDARY_PENALTY, 'player': pinfo}
    report = credibility(world, G, top_clubs)
    report['backtest'] = backtest(fx, pre, top_pts, until)
    world['model']['tests'] = report
    return report


def credibility(world, G, top_clubs, sims=2000, seed_=7):
    clubs = [c for c in world['clubs'] if c.get('n', 0) >= 10]
    by_pts = sorted(clubs, key=lambda c: -(c.get('pts') or 0))
    by_elo = sorted(clubs, key=lambda c: -c['elo'])
    top12 = {c['id'] for c in by_pts[:12]}; top15 = {c['id'] for c in by_elo[:15]}
    rep = {'big12_in_top15': len(top12 & top15)}
    players = sorted([p for p in world['players'] if 'ovr' in p], key=lambda p: -p['ovr'])
    top10c = {c['id'] for c in by_elo[:10]}
    rep['top25_players_from_top10_clubs'] = sum(1 for p in players[:25] if p['club'] in top10c)
    fwd = next((i for i, p in enumerate(players) if LINE.get(p['prim']) == 'FWD'), 999) + 1
    rep['best_forward_rank'] = fwd
    share_all = Counter(LINE.get(p['prim'], 'MID') for p in players); share_top = Counter(LINE.get(p['prim'], 'MID') for p in players[:300])   # 300, not 100: a top 100 swings by ±5 points on chance alone
    rep['line_share_gap'] = round(max(abs(share_top[l] / 300 - share_all[l] / len(players)) for l in share_all), 4)
    rep['top10_players'] = [f"{p['name']} {p['ovr']}" for p in players[:10]]
    rep['top10_clubs'] = [f"{c['name']} {round(c['elo'])}" for c in by_elo[:10]]
    # the three competitions, many times, exactly as the game builds them: who gets in (comp_fields), the formats
    # (Champions and Europa League 8 matches, Conference League 6), then the knockouts
    rnd = random.Random(seed_)
    rank_of = {c['id']: i + 1 for i, c in enumerate(by_elo)}
    name = {c['id']: c['name'] for c in clubs}
    rep['competitions'] = {}
    for comp in ('cl', 'el', 'ecl'):
        titles = Counter(); top8 = bottom = 0
        n = sims if comp == 'cl' else sims // 2
        for _ in range(n):
            field = comp_fields(clubs, rnd)[comp]
            champ = sim_cup(field, G, rnd, six=(comp == 'ecl'))
            titles[champ['id']] += 1
            order = [c['id'] for c in sorted(field, key=lambda c: -c['elo'])]
            top8 += champ['id'] in order[:8]; bottom += champ['id'] in order[18:]
        rep['competitions'][comp] = {'title_share_top8_of_field': round(top8 / n, 3), 'title_share_bottom_half': round(bottom / n, 3),
                                     'favourites': [f"{name[k]} {v / n:.1%}" for k, v in titles.most_common(6)]}
        if comp == 'cl':
            rep['title_share_top8_of_field'] = round(top8 / n, 3)
            rep['max_title_share_outside_top30'] = round(max([v / n for k, v in titles.items() if rank_of[k] > 30] or [0]), 4)
            rep['title_favourites'] = rep['competitions'][comp]['favourites']
    C3 = rep['competitions']
    rep['pass'] = {'big12_in_top15': rep['big12_in_top15'] >= 9, 'top25': rep['top25_players_from_top10_clubs'] >= 15,
                   'best_forward': fwd <= 5, 'line_share': rep['line_share_gap'] <= 0.05,
                   'top8_titles': rep['title_share_top8_of_field'] >= 0.70, 'outsiders': rep['max_title_share_outside_top30'] <= 0.015,
                   # Europa and Conference League: still mostly the field's best, a little more open
                   'el_top8': C3['el']['title_share_top8_of_field'] >= 0.55, 'ecl_top8': C3['ecl']['title_share_top8_of_field'] >= 0.55,
                   'bottom_half': max(C3[c]['title_share_bottom_half'] for c in C3) <= 0.15}
    return rep


FIELD_SIGMA, COUNTRY_CAP = 0.85, {'cl': 5, 'el': 4, 'ecl': 3}


def comp_fields(clubs, rnd, size=36):
    """Who plays where, as in the game: UEFA rank and rating rank, half each on a log scale, plus luck, then the
    Champions League, Europa League and Conference League in that order with a cap per country."""
    erank = {c['id']: i + 1 for i, c in enumerate(sorted(clubs, key=lambda c: -c['elo']))}
    key = {c['id']: 0.5 * math.log(c['rank']) + 0.5 * math.log(erank[c['id']]) + FIELD_SIGMA * rnd.gauss(0, 1) for c in clubs}
    fields = {k: [] for k in COUNTRY_CAP}; cnt = {k: Counter() for k in COUNTRY_CAP}
    for c in sorted(clubs, key=lambda c: key[c['id']]):
        for k in ('cl', 'el', 'ecl'):
            if len(fields[k]) >= size or cnt[k][c['ctry']] >= COUNTRY_CAP[k]:
                continue
            fields[k].append(c); cnt[k][c['ctry']] += 1
            break
    return fields


def pps(clubs, n, rnd):
    """n clubs, each with a chance exactly proportional to its UEFA points (systematic sampling, capped at 1)."""
    pool = clubs[:]; rnd.shuffle(pool)
    w = {c['id']: (c.get('pts') or 0.5) for c in pool}; sure = set(); pi = {}
    changed = True
    while changed:
        changed = False
        rest = [c for c in pool if c['id'] not in sure]; tot = sum(w[c['id']] for c in rest); left = n - len(sure)
        for c in rest:
            p = left * w[c['id']] / tot
            if p >= 1: sure.add(c['id']); changed = True
            else: pi[c['id']] = p
    out = [c for c in pool if c['id'] in sure]; u = rnd.random(); cum = 0; nxt = u
    for c in pool:
        if c['id'] in sure: continue
        cum += pi[c['id']]
        while cum > nxt and len(out) < n:
            out.append(c); nxt += 1
    return out


def play(G, a, b, rnd, home=None, et=False):
    neutral = home is None
    h, w = (a, b) if neutral or home is a else (b, a)
    x, y = draw_score(G, h['elo'] - w['elo'], rnd, neutral)
    if et:
        x = sum(1 for _ in range(x) if rnd.random() < 1 / 3); y = sum(1 for _ in range(y) if rnd.random() < 1 / 3)
    return (x, y) if h is a else (y, x)


def tie(G, a, b, rnd, legs=2):
    if legs == 1:
        x, y = play(G, a, b, rnd)
    else:
        x1, y1 = play(G, a, b, rnd, home=b); x2, y2 = play(G, a, b, rnd, home=a); x, y = x1 + x2, y1 + y2
    if x == y:
        ex, ey = play(G, a, b, rnd, home=None if legs == 1 else a, et=True); x += ex; y += ey
    if x == y:
        return a if rnd.random() < 0.5 else b
    return a if x > y else b


def sim_cup(field, G, rnd, six=False):
    teams = sorted(field, key=lambda c: -c['elo'])
    pts = {c['id']: 0 for c in teams}; gd = {c['id']: 0 for c in teams}
    def result(h, w):
        x, y = play(G, h, w, rnd, home=h)
        pts[h['id']] += 3 if x > y else 1 if x == y else 0; pts[w['id']] += 3 if y > x else 1 if x == y else 0
        gd[h['id']] += x - y; gd[w['id']] += y - x
    if six:   # Conference League: 6 pots of 6, one opponent from each, home side by a coin
        pots6 = [teams[i * 6:(i + 1) * 6] for i in range(6)]
        for i in range(6):
            for j in range(i, 6):
                A = pots6[i][:]; rnd.shuffle(A)
                pairs = [(A[k], A[k + 1]) for k in (0, 2, 4)] if i == j else list(zip(A, rnd.sample(pots6[j], 6)))
                for h, w in pairs:
                    result(*((h, w) if rnd.random() < 0.5 else (w, h)))
    pots = [teams[i * 9:(i + 1) * 9] for i in range(4)]
    for i in range(4 if not six else 0):
        for j in range(i, 4):
            A = pots[i][:]; rnd.shuffle(A); B = A if i == j else pots[j][:]
            if i != j: rnd.shuffle(B)
            for k in range(9):
                pairs = [(A[k], A[(k + 1) % 9])] if i == j else [(A[k], B[k]), (B[(k + 1) % 9], A[k])]
                for h, w in pairs:
                    result(h, w)
    seed = sorted(teams, key=lambda c: (-pts[c['id']], -gd[c['id']], rnd.random()))
    po = [tie(G, seed[8 + i], seed[23 - i], rnd) for i in range(8)]
    r16 = [tie(G, seed[k], po[7 - k], rnd) for k in [0, 7, 3, 4, 1, 6, 2, 5]]
    qf = [tie(G, r16[i], r16[i + 1], rnd) for i in (0, 2, 4, 6)]
    sf = [tie(G, qf[i], qf[i + 1], rnd) for i in (0, 2)]
    return tie(G, sf[0], sf[1], rnd, legs=1)


def backtest(fx, pre, top_pts, until, days=90):
    """Fit the goals model on matches before the cut-off, predict every later match between ranked clubs from the
    ratings each side had before kick-off. Compared with UEFA points alone and with the base rates."""
    import datetime as dt
    cut = (dt.date.fromisoformat(until) - dt.timedelta(days=days)).isoformat()
    start = (dt.date.fromisoformat(until) - dt.timedelta(days=365)).isoformat()
    ms = [(fid, f) for fid, f in fx.items() if fid in pre and f['s'] in FINISHED and start <= f['d'] <= until
          and f['h'] in top_pts and f['a'] in top_pts]
    train = [(fid, f) for fid, f in ms if f['d'] < cut]; test = [(fid, f) for fid, f in ms if f['d'] >= cut]
    res = lambda f: 0 if f['hg'] > f['ag'] else 1 if f['hg'] == f['ag'] else 2
    out = {'train': len(train), 'test': len(test)}
    base = [sum(1 for _, f in train if res(f) == o) / len(train) for o in range(3)]
    def score(pf):
        ll = br = hit = 0
        for fid, f in test:
            p = pf(fid, f); o = res(f)
            ll -= math.log(max(1e-9, p[o])); br += sum((p[i] - (i == o)) ** 2 for i in range(3)); hit += max(range(3), key=lambda i: p[i]) == o
        n = len(test)
        return {'logloss': round(ll / n, 4), 'brier': round(br / n, 4), 'hit': round(hit / n, 3)}
    out['base_rates'] = score(lambda fid, f: base)
    gu = fit_goals([(seed(top_pts[f['h']]) - seed(top_pts[f['a']]), f['hg'], f['ag']) for _, f in train])
    out['uefa_only'] = score(lambda fid, f: wdl(gu, seed(top_pts[f['h']]) - seed(top_pts[f['a']])))
    gap = lambda fid, f: blend(pre[fid][0], f['h'], top_pts) - blend(pre[fid][1], f['a'], top_pts)
    gm = fit_goals([(gap(fid, f), f['hg'], f['ag']) for fid, f in train])
    out['model'] = score(lambda fid, f: wdl(gm, gap(fid, f)))
    return out
