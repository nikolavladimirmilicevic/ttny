"""The manager game's data (cm.json): one league's clubs, squads and fixture list, and for every player a set of
attributes on a 40-99 scale in the manner of PES 2021 (the kinds of attributes and the positions, not its values).

Each attribute starts from the player's overall (the draft game's, where he has one; otherwise estimated from his
season), moved by his position's profile (a centre-forward finishes better than he tackles), then by what his season
says (goals, passes, dribbles, tackles, duels, saves, each per 90 minutes and compared with players in his position,
shrunk toward the middle when he has played little), by his height and weight (heading, jumping, physical contact,
balance) and by his age (pace fades after 28, stamina after 31), with a small fixed draw per player so no two are alike.
"""
import datetime, json, math, os, random, re, statistics
from statistics import NormalDist

ATTRS = [
    ('oa', 'Offensive awareness'), ('bc', 'Ball control'), ('dr', 'Dribbling'), ('tp', 'Tight possession'),
    ('lp', 'Low pass'), ('lf', 'Lofted pass'), ('fi', 'Finishing'), ('he', 'Heading'), ('pk', 'Place kicking'),
    ('cu', 'Curl'), ('sp', 'Speed'), ('ac', 'Acceleration'), ('kp', 'Kicking power'), ('ju', 'Jump'),
    ('pc', 'Physical contact'), ('ba', 'Balance'), ('st', 'Stamina'), ('da', 'Defensive awareness'),
    ('bw', 'Ball winning'), ('ag', 'Aggression'), ('ga', 'GK awareness'), ('gc', 'GK catching'),
    ('gl', 'GK clearing'), ('gr', 'GK reflexes'), ('gh', 'GK reach')]
KEYS = [k for k, _ in ATTRS]
OUT = KEYS[:20]
GKA = KEYS[20:]

# each position's profile: how far each outfield attribute sits from the overall
_P = {
    #       oa  bc  dr  tp  lp  lf  fi  he  pk  cu  sp  ac  kp  ju  pc  ba  st  da   bw   ag
    'CF':  (6,  0,  -2, -2, -10, -14, 6,  0, -12, -8, -1, -1,  2, -2,  2, -4, -8, -35, -38, -15),
    'SS':  (4,  3,  2,  2,  -4, -8,  2, -10, -6, -2,  0,  2, -1, -12, -8, 0, -4, -33, -35, -18),
    'WF':  (2,  2,  5,  2,  -5, -7, -3, -16, -8,  0,  5,  6, -3, -14, -12, 2, -3, -32, -34, -20),
    'AMF': (2,  3,  1,  3,   3, -1, -4, -18, -4,  0, -4, -1, -3, -16, -12, 1, -3, -26, -28, -18),
    'WM':  (-2, 0,  2, -1,   0,  0, -8, -16, -6,  0,  3,  3, -4, -12, -10, 0,  4, -18, -18, -12),
    'CMF': (-4, 0, -3,  1,   4,  2, -10, -12, -6, -4, -6, -4, -2, -8, -4, -2,  6, -6, -4, -6),
    'DMF': (-14, -4, -8, -2,  2,  0, -20, -6, -12, -12, -8, -8, -4, -4,  2, -6,  4,  3,  4,  0),
    'CB':  (-26, -12, -18, -10, -6, -8, -30, 2, -20, -20, -8, -10, -6,  2,  4, -12, -4,  5,  4,  0),
    'FB':  (-14, -6, -6, -6,  -4, -2, -24, -8, -14, -8,  2,  1, -8, -6, -6, -4,  5,  0,  0, -4),
}
_GROUP = {'CF': 'CF', 'SS': 'SS', 'LWF': 'WF', 'RWF': 'WF', 'AMF': 'AMF', 'LMF': 'WM', 'RMF': 'WM', 'CMF': 'CMF',
          'DMF': 'DMF', 'CB': 'CB', 'LB': 'FB', 'RB': 'FB', 'GK': 'GK'}
# what a position's rating weighs (PES-like): the attributes that matter most there
_W = {
    'CF': {'oa': 3, 'fi': 3, 'bc': 2, 'he': 1.5, 'kp': 1, 'pc': 1, 'sp': 1, 'ac': 1, 'dr': 1},
    'SS': {'oa': 3, 'fi': 2, 'bc': 2, 'dr': 2, 'tp': 1, 'lp': 1, 'ac': 1, 'ba': 1},
    'WF': {'dr': 3, 'sp': 2, 'ac': 2, 'oa': 2, 'bc': 2, 'fi': 1, 'cu': 1, 'lp': 1},
    'AMF': {'lp': 3, 'bc': 2, 'tp': 2, 'oa': 2, 'dr': 2, 'lf': 1, 'fi': 1, 'cu': 1},
    'WM': {'lp': 2, 'lf': 2, 'dr': 2, 'sp': 2, 'st': 2, 'bc': 1, 'ac': 1, 'cu': 1},
    'CMF': {'lp': 3, 'lf': 2, 'bc': 2, 'tp': 1, 'st': 2, 'bw': 1, 'da': 1, 'oa': 1},
    'DMF': {'da': 3, 'bw': 3, 'lp': 2, 'pc': 1, 'st': 1, 'ag': 1, 'bc': 1, 'he': 1},
    'CB': {'da': 3, 'bw': 3, 'he': 2, 'pc': 2, 'ju': 1, 'sp': 1, 'ag': 1},
    'FB': {'da': 2, 'bw': 2, 'sp': 2, 'st': 2, 'lp': 1, 'lf': 1, 'ac': 1, 'dr': 1},
    'GK': {'ga': 3, 'gr': 3, 'gc': 2, 'gh': 2, 'gl': 1},
}
POSITIONS = ['GK', 'CB', 'LB', 'RB', 'DMF', 'CMF', 'LMF', 'RMF', 'AMF', 'LWF', 'RWF', 'SS', 'CF']
ST = ['apps', 'xi', 'min', 'rt', 'g', 'a', 'sh', 'sot', 'pas', 'kp', 'acc', 'tk', 'blk', 'int', 'du', 'duw', 'dra', 'drs',
      'drp', 'fd', 'fc', 'yc', 'rc', 'sv', 'gc', 'pks', 'pkm']
IX = {k: i for i, k in enumerate(ST)}


def ability(w):
    """A player's standing on a PES-like scale from the draft game's long-run percentiles (level and performance among
    every rated player in Europe, as normal scores), with a little of its overall (which carries his recent form), so an
    injury or a bad month does not make a star ordinary."""
    z = lambda q: NormalDist().inv_cdf(min(.995, max(.01, q if q is not None else .5)))
    a = 74 + 6 * (.6 * z(w.get('lvl')) + .4 * z(w.get('prf')))
    return .85 * a + .15 * (.9 * w['ovr'] + 3)


def clip(v, lo=40, hi=99):
    return max(lo, min(hi, v))


def pos_rating(a, pos):
    if PROF:
        f = PROF['rating'][pos]
        return sum(a[k] * w for k, w in zip(PROF['keys'], f['w'])) + f['c']
    w = _W[_GROUP[pos]]
    return sum(a[k] * x for k, x in w.items()) / sum(w.values())


# group-level patterns learned from a PES 2021 database (pipeline/cm_profiles.json: per position, the mean shape of each
# playing style over 30 or more players, how shape moves with height, weight, age and overall, the spread around it, and
# each position's rating formula). No individual player's values are in it or taken from it.
try:
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cm_profiles.json'), encoding='utf-8') as _f:
        PROF = json.load(_f)
except Exception:
    PROF = None

# how a style shows in a season's numbers (z-scores per 90 against players in his position): a nudge to the weights
STYLE_STATS = {
    'Goal Poacher': {'g90': 1, 'drs90': -.5, 'kp90': -.5}, 'Fox in the Box': {'g90': .7, 'drs90': .5}, 'Target Man': {'duw': 1, 'drs90': -.5},
    'Dummy Runner': {'a90': .5, 'g90': .3}, 'Creative Playmaker': {'kp90': 1, 'a90': .5, 'drs90': .3}, 'Prolific Winger': {'g90': .7, 'drs90': .7},
    'Roaming Flank': {'drs90': .7, 'kp90': .5}, 'Cross Specialist': {'kp90': .7, 'a90': .5, 'drs90': -.3}, 'Classic No. 10': {'kp90': 1, 'acc': .5},
    'Hole Player': {'g90': .8, 'kp90': .3}, 'Box-to-Box': {'def90': .6, 'g90': .3}, 'Orchestrator': {'acc': 1, 'kp90': .5, 'def90': -.3},
    'Anchor Man': {'def90': .6, 'int90': .6, 'g90': -.5}, 'The Destroyer': {'def90': .8, 'fc90': .6, 'acc': -.3}, 'Build Up': {'acc': 1, 'def90': -.2},
    'Extra Frontman': {'g90': .8, 'kp90': .3}, 'Offensive Full-back': {'kp90': .7, 'a90': .5, 'drs90': .4}, 'Defensive Full-back': {'def90': .8, 'kp90': -.5},
    'Full-back Finisher': {'g90': 1}, 'Defensive Goalkeeper': {'sv90': .3},
}


def style_weights(prof, pos, f, z):
    """The playing styles a player may have, weighted: how common each is in his position, how close his height,
    weight, age and overall are to that style's average, and what his season's numbers say."""
    P = prof['pos'][pos]
    ws = {}
    tot_n = sum(g['n'] for g in P['styles'].values())
    for st, g in P['styles'].items():
        ll = 0.0
        for v, mu, sd in zip(f, g['f'], P['f_sd']):
            if v is not None and sd:
                ll -= ((v - mu) / sd) ** 2 / 2
        aff = sum(w * z(m) for m, w in STYLE_STATS.get(st, {}).items())
        ws[st] = (g['n'] / tot_n) * math.exp(.5 * ll + .8 * aff)
    t = sum(ws.values()) or 1
    return {k: v / t for k, v in ws.items()}


def _shape(prof, pos, o, ht, wt, age, z, rnd):
    """Attributes on his overall: the weighted mean shape of his likely styles, moved by the learned slopes for his
    height, weight, age and overall, plus a fixed draw as wide as the real spread around those means."""
    P = prof['pos'][pos]
    f = [ht, wt, age, o]
    ws = style_weights(prof, pos, f, z)
    K = prof['keys']
    shape = [0.0] * len(K)
    fmix = [0.0] * 4
    for st, w in ws.items():
        g = P['styles'][st]
        for i in range(len(K)): shape[i] += w * g['shape'][i]
        for j in range(4): fmix[j] += w * g['f'][j]
    for j in range(4):
        v = f[j] if f[j] is not None else fmix[j]
        d = v - fmix[j]
        for i in range(len(K)):
            shape[i] += P['slope'][j][i] * d
    a = {k: clip(o + shape[i] + rnd.gauss(0, .6 * P['resid_sd'][i])) for i, k in enumerate(K)}
    a['_style'] = max(ws, key=ws.get) if ws else None
    return a


def _stats(entry, season, tid):
    ss = (entry or {}).get('ss') or {}
    s = ss.get(f'{season}:{tid}')
    if not s:   # this season with another club, or last season: still him
        for k in sorted(ss, key=lambda k: -int(k.split(':')[0])):
            s = ss[k]; break
    return s


def _per90(s):
    m = s[IX['min']] if s else 0
    if not s or m < 1:
        return None
    f = 90.0 / m
    pas = s[IX['pas']]
    acc = s[IX['acc']]
    if acc > 100 and pas:      # some seasons give accurate passes as a count
        acc = 100.0 * acc / pas
    return {
        'min': m, 'rt': s[IX['rt']] or None,
        'g90': (s[IX['g']] - s[IX['pks']]) * f, 'a90': s[IX['a']] * f, 'sh90': s[IX['sh']] * f,
        'sot': s[IX['sot']] / s[IX['sh']] if s[IX['sh']] >= 5 else None,
        'kp90': s[IX['kp']] * f, 'acc': acc if pas >= 50 and 30 <= acc <= 100 else None,
        'def90': (s[IX['tk']] + s[IX['int']]) * f, 'int90': s[IX['int']] * f, 'past90': s[IX['drp']] * f,
        'drs90': s[IX['drs']] * f, 'drpct': s[IX['drs']] / s[IX['dra']] if s[IX['dra']] >= 8 else None,
        'duw': s[IX['duw']] / s[IX['du']] if s[IX['du']] >= 20 else None,
        'fc90': s[IX['fc']] * f, 'cards90': (s[IX['yc']] + 3 * s[IX['rc']]) * f,
        'sv90': s[IX['sv']] * f, 'gc90': s[IX['gc']] * f, 'share': s[IX['xi']] / max(1, s[IX['apps']]),
    }


def _rough_pos(sp, p90, ht, no):
    """A position for a player the draft game does not know, from the squad list's rough one and his season."""
    if sp == 'G':
        return 'GK'
    if sp == 'D':
        if ht and ht >= 183 or (p90 and p90['def90'] > 4.5 and p90['kp90'] < .6):
            return 'CB'
        return 'LB' if no in (3, 13, 23, 33) else 'RB' if no in (2, 12, 22, 32) else 'CB'
    if sp == 'M':
        if p90 and p90['def90'] > 3.8:
            return 'DMF'
        if p90 and (p90['drs90'] > 1.4 or p90['kp90'] > 1.8):
            return 'AMF'
        return 'CMF'
    if sp == 'F':
        if p90 and p90['drs90'] > 1.6 and (ht or 180) < 182:
            return 'LWF' if (no or 0) % 2 else 'RWF'
        return 'CF'
    return 'CMF'


def _shape_old(pos, g, o, ht, wt, age, z, rnd, p):
    """The first model (used only without cm_profiles.json): position profiles of my own."""
    a = {}
    if g == 'GK':
        for k in OUT: a[k] = clip(42 + rnd.uniform(0, 8) + (o - 70) * .15)
        for k, off in (('ga', 2), ('gc', 0), ('gl', -4), ('gr', 1), ('gh', 0)): a[k] = o + off + rnd.uniform(-3, 3)
    else:
        for i, k in enumerate(OUT): a[k] = o + _P[g][i] + rnd.uniform(-3, 3)
        for k in GKA: a[k] = clip(40 + rnd.uniform(0, 6))
    if ht:
        d = max(-12, min(12, ht - 182)); a['he'] += d * .6; a['ju'] += d * .5; a['ba'] -= d * .35
    fade = max(0, age - 28); a['sp'] -= fade * 1.6; a['ac'] -= fade * 1.8
    return a


def _style(a, pos):
    g = _GROUP[pos]
    if g == 'GK':
        return 'Offensive goalkeeper' if a['gl'] >= a['gc'] + 3 else 'Defensive goalkeeper'
    if g == 'CF':
        if a['he'] >= 80 and a['pc'] >= 80: return 'Target man'
        if a['oa'] >= a['bc'] + 4: return 'Goal poacher'
        if a['dr'] >= 80: return 'Fox in the box'
        return 'Deep-lying forward'
    if g == 'SS': return 'Creative playmaker' if a['lp'] >= a['fi'] else 'Dummy runner'
    if g == 'WF': return 'Prolific winger' if a['fi'] >= a['lp'] else 'Roaming flank'
    if g == 'AMF': return 'Hole player' if a['fi'] >= a['lp'] - 2 else 'Classic No. 10'
    if g == 'WM': return 'Cross specialist' if a['lf'] >= a['dr'] else 'Roaming flank'
    if g == 'CMF':
        if a['st'] >= 85 and a['bw'] >= 75: return 'Box-to-box'
        return 'Orchestrator' if a['lp'] >= 82 else 'Box-to-box'
    if g == 'DMF': return 'Anchor man' if a['bw'] >= a['lp'] else 'Orchestrator'
    if g == 'CB': return 'Build up' if a['lp'] >= 72 else 'Destroyer' if a['ag'] >= 75 else 'Extra frontman' if a['oa'] >= 62 else 'Build up'
    if g == 'FB': return 'Offensive full-back' if a['oa'] >= 68 else 'Defensive full-back'
    return ''


def display(e, pid):
    """The name he goes by, as in the draft game: 'L. Díaz' + first names 'Luis Fernando' -> 'Luis Díaz'; 'Evanilson'
    and 'Rayan' stay as they are; the full name from the API only when there is nothing else."""
    sq, firsts = e.get('sq') or '', (e.get('fn') or '').split()
    m = re.match(r'^([^\W\d_][a-z]?)\. ', sq)
    if m and firsts:
        first = next((f for f in firsts if f.startswith(m.group(1))), firsts[0])
        return first + ' ' + sq.split('. ', 1)[1]
    return sq or e.get('n') or str(pid)


def age_of(e, today):
    """Age today from his date of birth; the API's age (counted at the season's start) when we do not have it."""
    bd = e.get('bd')
    if bd:
        b, t = datetime.date.fromisoformat(bd[:10]), datetime.date.fromisoformat(today)
        return t.year - b.year - ((t.month, t.day) < (b.month, b.day))
    a = e.get('age')
    return a if a and 14 <= a <= 45 else 25   # the API now and then gives a year in place of an age


def youth_ovr(age, base):
    """A young player with little senior football yet: rated by his age, a little above or below with the club."""
    return min(64, 50 + 2.5 * max(0, age - 16) + (base - 80) * .2)


VALUE_COEF = [2.7021, 0.135, -0.035, 0.1748, -0.127, -0.235, -0.0682, -0.062, -0.6654, 0.208, 0.2556, -0.13, -0.0518]


def inj_level(cm, pid, lastseen):
    """Injury proneness from the league's injury history (update.py: fixtures missed hurt over three seasons):
    1 rarely injured (0-3), 2 average (4-15), 3 injury prone (16+). None until that history is in, or for a player
    with no record who has not started in the league lately (new to it: not known)."""
    n = (cm or {}).get('injn')
    if not n:
        return None
    k = n.get(str(pid), 0)
    if not k and str(pid) not in {str(x) for x in (lastseen or {})}:
        return None
    return 1 if k <= 3 else 2 if k <= 15 else 3


def _shares(w, pos):
    m = dict(zip(w.get('pos') or [], w.get('pc') or []))
    v = [float(m.get(q, 0) or 0) for q in pos]
    t = sum(v)
    return [round(x / t, 2) for x in v] if t else None


def grid_pos(xi, grid):
    """A position from a lineup's grid ("row:column", row 1 the keeper, the last row the forwards, column 1 on the left)."""
    try:
        r, c = (int(x) for x in str(grid).split(':'))
        rows = [int(str(x[1]).split(':')[0]) for x in xi if x and x[1]]
        n = sum(1 for x in xi if x and str(x[1]).startswith(f'{r}:'))
    except (ValueError, IndexError):
        return None
    last = max(rows)
    if r == 1:
        return 'GK'
    wide = n >= 3 and c in (1, n)
    side = 'L' if c == 1 else 'R'
    if r == 2:
        return side + 'B' if n >= 4 and wide else 'CB'
    if r == last:
        return side + 'WF' if wide else 'CF'
    if r == last - 1 and last >= 5:
        return side + 'MF' if wide else 'AMF'
    return side + 'MF' if n >= 4 and wide else 'DMF' if r == 3 and last >= 5 else 'CMF'


LONG_OUT = 270   # days: a long-term injury's usual lay-off (a ruptured Achilles or cruciate), counted from his last start
LOOK_BACK = 400  # days: how far back a last start may be for a missing player to be looked into


def _listed(squads):
    out = set()
    for v in squads.values():
        if isinstance(v, dict):
            out.update(v.get('p') or [])
    return out


def absent_candidates(people, squads, lastseen, team_ids, season, today):
    """Players worth asking the API about: 5 or more appearances (or starts in our lineups) last season or this one with a club of
    this league, whose last start was for it within LOOK_BACK days, and who are in no club's squad list now."""
    listed, t0, out = _listed(squads), datetime.date.fromisoformat(today), []
    for pid, (d, tid, *_) in (lastseen or {}).items():
        if tid not in team_ids or pid in listed or not d:
            continue
        if (t0 - datetime.date.fromisoformat(d)).days > LOOK_BACK:
            continue
        ss = (people.get(str(pid)) or {}).get('ss') or {}
        apps = max(((ss.get(f'{y}:{tid}') or [0])[0] or 0) for y in (season - 1, season))
        starts = sum((lastseen[pid][2] or {}).values()) if len(lastseen[pid]) > 2 else 0
        if apps >= 5 or starts >= 5:
            out.append(pid)
    return out


def long_term_out(people, squads, lastseen, team_ids, season, today):
    """Players the squad lists leave out while they are long-term injured. Returns {player id: (club id, return date)}.
    With the API's word on them (transfers and injuries, see absent_candidates): still the club's (no move away since
    his last start for it) and an injury not yet over; back on its end date, or LONG_OUT days after it began.
    Without it: a regular last season, a start within LONG_OUT days but not in the season's final weeks, no match
    this season, and back LONG_OUT days after his last start."""
    listed, t0, out = _listed(squads), datetime.date.fromisoformat(today), {}
    cands = set(absent_candidates(people, squads, lastseen, team_ids, season, today))
    for pid in cands:
        d, tid = lastseen[pid][0], lastseen[pid][1]
        last = datetime.date.fromisoformat(d)
        e = people.get(str(pid)) or {}
        ss = e.get('ss') or {}
        if 'chk' in e:
            moved = [t for t in e.get('tr') or [] if t[0] >= d[:7] and t[1] != tid]
            if moved:   # transferred, loaned out or released since his last start
                continue
            hurt = [y for y in e.get('sl') or [] if y[1] and y[1] >= d and 'suspen' not in (y[0] or '').lower()
                    and (not y[2] or y[2] >= today)]
            if not hurt:
                continue
            y = hurt[-1]   # the injury that began after his last start
            start = datetime.date.fromisoformat(y[1][:10])
            back = datetime.date.fromisoformat(y[2][:10]) if y[2] else start + datetime.timedelta(days=LONG_OUT)
            if back <= t0:
                back = t0 + datetime.timedelta(days=21)   # past the usual lay-off, still not back: a few more weeks
            out[pid] = (tid, back.isoformat(), y[0])
            continue
        if (t0 - last).days > LONG_OUT or (t0 - last).days < 21 or d > f'{season}-05-10':
            continue
        if any(int(k.split(':')[0]) >= season for k in ss):
            continue
        if ((ss.get(f'{season - 1}:{tid}') or [0])[0] or 0) < 10:   # (our lineups' starts count other clubs too: not here)
            continue
        back = last + datetime.timedelta(days=LONG_OUT)
        if back > t0:
            out[pid] = (tid, back.isoformat(), None)
    return out


def _fold(s):
    import unicodedata
    return re.sub(r'[^a-z ]', '', unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode().lower().replace('-', ' ')).strip()


TM_POS = {'goalkeeper': 'GK', 'centre-back': 'CB', 'left-back': 'LB', 'right-back': 'RB', 'defensive midfield': 'DMF',
          'central midfield': 'CMF', 'attacking midfield': 'AMF', 'left midfield': 'LMF', 'right midfield': 'RMF',
          'left winger': 'LWF', 'right winger': 'RWF', 'second striker': 'SS', 'centre-forward': 'CF'}


def load_handbook():
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pl_handbook.json'), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def load_fixes():
    try:
        return json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cm_fixes.json'), encoding='utf-8')).get('fixes') or []
    except (OSError, ValueError) as ex:
        print(f'  cm_fixes.json: {ex}')
        return []


def apply_fixes(rows, fixes, teams, wc, people, wp, season):
    """cm_fixes.json: put a player in a club (moving him from wherever the API has him), make him injured until a date,
    or take him out. A player the API does not know is made from what the entry gives (name, position, birth date)."""
    if not fixes:
        return
    def club_id(name):
        n = _fold(name)
        for t in teams:
            for cand in (t.get('name'), (wc.get(t['id']) or {}).get('name')):
                c = _fold(cand)
                if c and (c == n or n in c or c in n):
                    return t['id']
        return None
    def person(fx):
        if fx.get('id') and str(fx['id']) in people:
            return int(fx['id'])
        n = _fold(fx.get('player'))
        for pid, e in people.items():
            if n and n in (_fold(e.get('n')), _fold(e.get('sq')), _fold((e.get('fn') or '').split(' ')[0] + ' ' + (e.get('n') or '').split(' ')[-1])):
                return int(pid)
        return None
    for fx in fixes:
        pid = person(fx)
        if fx.get('remove'):
            rows[:] = [r for r in rows if r['pid'] != pid]
            continue
        tid = club_id(fx.get('club'))
        if not tid:
            print(f"  cm_fixes: no club {fx.get('club')!r} for {fx.get('player')!r}")
            continue
        pos = fx.get('pos'); pos = TM_POS.get(str(pos).lower(), pos) if pos else None
        hit = next((r for r in rows if pid is not None and r['pid'] == pid), None)
        if hit:
            hit['tid'] = tid
        else:
            if pid is None:   # not in the API at all: a made-up id that stays the same from run to run
                pid = -(int.from_bytes(_fold(fx.get('player')).encode()[:6].ljust(6, b'.'), 'big') % 10 ** 9)
                e = {'n': fx.get('player'), 'sq': fx.get('player'), 'nat': fx.get('nat'), 'bd': fx.get('dob'), 'no': fx.get('no')}
            else:
                e = dict(people.get(str(pid)) or {})
            s = _stats(e, season, tid)
            hit = {'pid': pid, 'tid': tid, 'e': e, 'w': wp.get(pid), 'p90': _per90(s), 'st': s}
            rows.append(hit)
        if pos:
            hit['gp'] = pos
            if hit['w'] and hit['w'].get('prim') != pos:
                old = dict(zip(hit['w'].get('pos') or [], hit['w'].get('pc') or []))
                npos = [pos] + [q for q in (hit['w'].get('pos') or []) if q != pos]
                hit['w'] = dict(hit['w'], prim=pos, pos=npos, pc=[old.get(q, 0) for q in npos])
        if fx.get('no') is not None:
            hit['e'] = dict(hit['e'], no=fx['no'])
        if fx.get('name'):   # the name he goes by, where the API's differs
            hit['e'] = dict(hit['e'], sq=fx['name'], n=fx['name'], fn='')
        if fx.get('ovr'):
            hit['fix_ovr'] = float(fx['ovr'])
        if fx.get('out'):
            hit['back'] = fx['out']; hit['why'] = fx.get('why')


def build(world, people, squads, cm, cm_fx, kits, alias, season, today, lastseen=None, fixes=None):
    """cm.json as a dict, or None while the league's clubs are not all in."""
    teams = cm.get('teams') or []
    if not teams:
        return None
    wp = {p['id']: p for p in world.get('players', [])}
    wc = {c['id']: c for c in world.get('clubs', [])}
    rows = []
    for t in teams:
        sq = squads.get(str(t['id']))
        if not sq:
            continue
        for pid in sq['p']:
            e = people.get(str(pid)) or {}
            w = wp.get(pid)
            s = _stats(e, season, t['id'])
            ls = (lastseen or {}).get(pid) or (lastseen or {}).get(str(pid))
            gc = (ls[2] if ls and len(ls) > 2 else None) or {}
            rows.append({'pid': pid, 'tid': t['id'], 'e': e, 'w': w, 'p90': _per90(s), 'st': s, 'gc': gc,
                         **({'gp': max(gc.items(), key=lambda kv: kv[1])[0]} if gc else {})})
    longs = long_term_out(people, squads, lastseen, {t['id'] for t in teams if squads.get(str(t['id']))}, season, today)
    for pid, (tid, back, why) in longs.items():
        e = people.get(str(pid)) or {}
        s = _stats(e, season, tid)
        rows.append({'pid': pid, 'tid': tid, 'e': e, 'w': wp.get(pid), 'p90': _per90(s), 'st': s, 'back': back, 'why': why, 'gp': max((lastseen[pid][2] or {'CMF': 0}).items(), key=lambda kv: kv[1])[0], 'gc': lastseen[pid][2] or {}})
    apply_fixes(rows, fixes, teams, wc, people, wp, season)
    if not rows:
        return None
    # an overall for those the draft game does not rate: from the season's rating, starts and club, fitted on those it does
    fit = [(ability(r['w']), r['p90']['rt'], r['p90']['share'], math.log1p(r['p90']['min'])) for r in rows
           if r['w'] and r['w'].get('ovr') and r['p90'] and r['p90']['rt'] and r['p90']['min'] >= 300]
    coef = None
    if len(fit) >= 40:
        # least squares on [1, rating, share, log minutes]
        X = [[1, f[1], f[2], f[3]] for f in fit]; y = [f[0] for f in fit]
        n = 4
        A = [[sum(X[k][i] * X[k][j] for k in range(len(X))) for j in range(n)] for i in range(n)]
        b = [sum(X[k][i] * y[k] for k in range(len(X))) for i in range(n)]
        for i in range(n):   # Gauss-Jordan
            piv = A[i][i] or 1e-9
            for j in range(n): A[i][j] /= piv
            b[i] /= piv
            for r2 in range(n):
                if r2 != i:
                    fct = A[r2][i]
                    for j in range(n): A[r2][j] -= fct * A[i][j]
                    b[r2] -= fct * b[i]
        coef = b
    club_ovr = {}
    for t in teams:
        known = sorted((ability(r['w']) for r in rows if r['tid'] == t['id'] and r['w'] and r['w'].get('ovr')), reverse=True)[:14]
        club_ovr[t['id']] = statistics.mean(known) if len(known) >= 8 else None
    league_mid = statistics.median([v for v in club_ovr.values() if v] or [72])
    for r in rows:
        if r['w'] and r['w'].get('ovr'):
            r['ovr'] = ability(r['w']); continue
        base = (club_ovr.get(r['tid']) or league_mid - 4)
        p = r['p90']
        if coef and p and p['rt'] and p['min'] >= 200:
            est = coef[0] + coef[1] * p['rt'] + coef[2] * p['share'] + coef[3] * math.log1p(p['min'])
            k = min(1, p['min'] / 1200)
            age = age_of(r['e'], today)
            # what to expect of him before his minutes speak: a young player is rated by his age, not by his club
            prior = youth_ovr(age, base) if age <= 21 else base - 6
            r['ovr'] = k * est + (1 - k) * prior
        elif age_of(r['e'], today) <= 21 and (not p or p['min'] < 200):
            r['ovr'] = youth_ovr(age_of(r['e'], today), base)   # an academy player with (almost) no senior football
        else:
            r['ovr'] = base - 8 - (0 if p else 4)
    for r in rows:   # an overall set by hand in cm_fixes.json wins
        if r.get('fix_ovr'):
            r['ovr'] = r['fix_ovr']
    # a teenager rated on a handful of starts: weighed toward what his age promises until he has ten
    for r in rows:
        age = age_of(r['e'], today)
        if age <= 20:
            ls = (lastseen or {}).get(r['pid'])
            n = sum((ls[2] or {}).values()) if ls and len(ls) > 2 else 0
            if n < 10:
                k = n / 10
                r['ovr'] = k * r['ovr'] + (1 - k) * min(r['ovr'], max(youth_ovr(age, club_ovr.get(r['tid']) or league_mid - 4), r['ovr'] - 10))
    # positions
    for r in rows:
        w, e = r['w'], r['e']
        if w and w.get('prim'):
            r['pos'] = list(w.get('pos') or [w['prim']]); r['prim'] = w['prim']
        else:
            r['prim'] = r.get('gp') or _rough_pos(e.get('sp'), r['p90'], e.get('ht'), e.get('no')); r['pos'] = [r['prim']]
            # a club outside the ranked list (no Draft data): his positions from this league's line-ups, as the Draft
            # reads them: every position he started in at least twice, most starts first
            gc = r.get('gc') or {}
            if gc:
                order = sorted(gc, key=lambda k: -gc[k])
                r['pos'] = [order[0]] + [k for k in order[1:] if gc[k] >= 2]
                r['prim'] = order[0]
                r['gw'] = {'pos': r['pos'], 'pc': [gc[k] for k in r['pos']]}
    # z-scores of season numbers within each position group, shrunk toward 0 by minutes
    metrics = ['g90', 'a90', 'sot', 'kp90', 'acc', 'def90', 'int90', 'past90', 'drs90', 'drpct', 'duw', 'fc90', 'cards90', 'sv90', 'gc90']
    groups = {}
    for r in rows:
        if r['p90'] and r['p90']['min'] >= 270:
            groups.setdefault(_GROUP[r['prim']], []).append(r)
    norm = {}
    for g, L in groups.items():
        for m in metrics:
            vals = [x['p90'][m] for x in L if x['p90'].get(m) is not None]
            if len(vals) >= 6:
                mu = statistics.mean(vals); sd = statistics.pstdev(vals) or 1
                norm[(g, m)] = (mu, sd)
    out_players = []
    for r in rows:
        e, p, pos = r['e'], r['p90'], r['prim']
        g = _GROUP[pos]
        rnd = random.Random(f"cm-{r['pid']}")
        o = r['ovr']   # already on the PES-like scale
        def z(m):
            if not p or p.get(m) is None or (g, m) not in norm:
                return 0.0
            mu, sd = norm[(g, m)]
            return max(-2.5, min(2.5, (p[m] - mu) / sd)) * min(1, p['min'] / 1500)
        ht, wt, age = e.get('ht'), e.get('wt'), age_of(e, today)
        a = _shape(PROF, pos, o, ht, wt, age, z, rnd) if PROF else _shape_old(pos, g, o, ht, wt, age, z, rnd, p)
        style_pick = a.pop('_style', None)
        # his season moves attributes against each other (the shift below keeps his position's rating on his overall)
        if g == 'GK':
            a['gr'] += 2 * z('sv90'); a['ga'] -= 1.5 * z('gc90'); a['gc'] += 1 * z('sv90')
        else:
            a['fi'] += 2 * z('g90') + 1 * z('sot'); a['oa'] += 1.5 * z('g90') + 1 * z('a90')
            a['lp'] += 2 * z('acc') + 1 * z('kp90'); a['lf'] += 1.5 * z('kp90'); a['tp'] += 1 * z('acc')
            a['dr'] += 2 * z('drs90') + 1 * z('drpct'); a['ba'] += 1 * z('drpct')
            a['bw'] += 2 * z('def90'); a['da'] += 1.5 * z('int90') - 1.5 * z('past90')
            a['pc'] += 2 * z('duw'); a['ag'] += 1.5 * z('fc90') + 1 * z('cards90')
            a['cu'] += 1 * z('a90')
        own = GKA if g == 'GK' else OUT
        if not PROF:   # the old profiles: shift so his position's rating lands on his overall
            shift = o - pos_rating(a, pos)
            for k in own:
                a[k] = clip(a[k] + shift)
        # (with the learned shapes, attributes already sit around his overall as they do at that level; the rating
        # formula, linear, runs a little low for the very best, so other positions are rated against his own)
        base = pos_rating({k: clip(v) for k, v in a.items()}, pos)
        a = {k: int(round(clip(a[k]))) for k in KEYS}
        ratings = {q: int(round(clip(o + pos_rating(a, q) - base + (0 if q in r['pos'] else -3 if _GROUP[q] == g else -6)))) for q in POSITIONS}
        ratings[pos] = int(round(o))
        rating = ratings[pos]
        val = round(max(.2, (math.exp((rating - 60) / 7.5) * .9) * (1.25 if age <= 23 else 1 if age <= 28 else max(.25, 1 - (age - 28) * .12))), 1)
        w = r['w'] or {}
        st = r['st']
        style = (style_pick if style_pick and style_pick != '---' else None) or _style(a, pos)
        out_players.append({'id': r['pid'], 'n': display(e, r['pid']) if (e.get('sq') or e.get('n')) else (w.get('name') or str(r['pid'])), 'nat': e.get('nat') or w.get('nat'),
                            'age': age, **({'bd': e['bd'][:10]} if e.get('bd') else {}), 'no': e.get('no'), 'club': r['tid'], 'prim': pos, 'pos': r['pos'], 'ht': ht, 'wt': wt,
                            'ovr': rating, 'r': ratings, 'a': a, 'style': style, 'val': val,
                            'frm': w.get('frm'), **({'inj': inj_level(cm, r['pid'], lastseen)} if inj_level(cm, r['pid'], lastseen) else {}),
                            # how his starts split over his registered positions (shares, same order as 'pos')
                            **({'pc': _shares(w if w.get('pc') else (r.get('gw') or {}), r['pos'])} if len(r['pos']) > 1 else {}),
                            's': [st[IX['apps']], st[IX['g']], st[IX['a']], st[IX['rt']]] if st else None,
                            **({'out': r['back']} if r.get('back') else {}), **({'outw': r['why']} if r.get('why') else {})})
    # market values: a log-linear fit of the Transfermarkt squad values Nikola supplied for the 20 clubs (Nov 2026,
    # 484 players, € at 0.855 to the £) on our own numbers only: rating, age, club strength, position, national-team
    # starts, goals + assists per start. Only these coefficients are kept, no single player's value; +13% for the
    # fit's pull to the middle (the league's total matches Transfermarkt's)
    _wof = {r['pid']: (r['w'] or {}) for r in rows}
    _cq = {}
    for x in out_players: _cq.setdefault(x['club'], []).append(x['ovr'])
    _cq = {c: statistics.mean(sorted(v, reverse=True)[:14]) for c, v in _cq.items()}
    for x in out_players:
        o, ag, w = x['ovr'], x['age'] or 26, _wof.get(x['id'], {})
        nt = min(w.get('nt', 0) or 0, 20); ga = ((w.get('ga', 0) or 0) + (w.get('aa', 0) or 0)) / max(1, w.get('starts', 1) or 1)
        f = [1, o - 75, max(0, o - 80), max(0, o - 86), ag - 26, max(0, ag - 29), max(0, 23 - ag), _cq[x['club']] - 75,
             x['prim'] == 'GK', x['prim'] in ('CF', 'SS', 'LWF', 'RWF', 'AMF'), math.log1p(nt), min(ga, 1.2), bool(w)]
        v = 1.13 * math.exp(sum(c * float(z) for c, z in zip(VALUE_COEF, f)))
        x['val'] = round(v, 1) if v >= 1 else round(max(.05, v), 2)
    # shirt numbers: the API's lists can be out of date, two players with one number; it stays with the one who has
    # played more for the club these two seasons, the other goes without
    by = {}
    for x in out_players:
        if x['no'] is not None:
            by.setdefault((x['club'], x['no']), []).append(x)
    for L in by.values():
        if len(L) > 1:
            apps = lambda x: sum(((people.get(str(x['id'])) or {}).get('ss') or {}).get(f'{y}:{x["club"]}', [0])[0] or 0 for y in (season - 1, season))
            L.sort(key=apps, reverse=True)
            for x in L[1:]:
                x['no'] = None
    clubs = []
    for t in teams:
        mine = sorted((p['ovr'] for p in out_players if p['club'] == t['id']), reverse=True)[:16]
        if not mine:
            continue
        c = wc.get(t['id']) or {}
        nm = c.get('name') or t['name']
        kit = kits.get(nm) or kits.get(alias.get(nm, '')) or kits.get(t['name']) or kits.get(alias.get(t['name'], ''))
        clubs.append({'id': t['id'], 'name': nm, 'code': t.get('code'), 'city': t.get('city'), 'venue': t.get('venue'), 'cap': t.get('cap'),
                      'kit': kit, 'elo': c.get('elo'), 'q': round(statistics.mean(mine), 1),
                      **({'coach': ((cm or {}).get('coach') or {})[str(t['id'])][0]} if (((cm or {}).get('coach') or {}).get(str(t['id'])) or [None])[0] else {})})
    clubs.sort(key=lambda c: -c['q'])
    have = {c['id'] for c in clubs}
    fixtures = [[m[1], m[2], m[3], m[4]] for m in (cm_fx or {}).get('m', []) if m[3] in have and m[4] in have]
    # the league's own handbook (pipeline/pl_handbook.json, facts only): head coach where the API has none, ground
    # capacity, shirt sponsor and kit maker, last season's place, kick-off times and TV picks, the FA Cup's dates
    hb = load_handbook()
    if hb and hb.get('season') == season:
        for c in clubs:
            h = (hb.get('clubs') or {}).get(str(c['id'])) or {}
            if h.get('coach'):   # the handbook (Oct 2026) is fresher than the API's coach careers (still Guardiola, Slot)
                c['coach'] = h['coach']
            for k in ('cap', 'sponsor', 'kitm', 'last', 'att', 'full'):
                if h.get(k):
                    c[k] = h[k]
        ko = {(f[0], f[1], f[2]): f[3:] for f in hb.get('fixtures') or []}
        fixtures = [f + ([ko[(f[0], f[2], f[3])][0], ko[(f[0], f[2], f[3])][1]] if (f[0], f[2], f[3]) in ko else []) for f in fixtures]
    extra = {k: hb[k] for k in ('cup', 'rules', 'last_season') if hb and hb.get('season') == season and hb.get(k)}
    return {'season': season, 'updated': today, 'league': 'Premier League', 'complete': len(clubs) == len(teams) and len(teams) >= 18,
            'attrs': ATTRS, 'clubs': clubs, 'players': out_players, 'fixtures': fixtures, **extra}
