#!/usr/bin/env python3
"""
This Time Next Year: daily data update from API-Football (v3).

Builds world.json for the game:
  - the top-100 clubs (pipeline/clubs.csv, UEFA 5-year ranking order)
  - every club's last 10 results against other top-100 clubs
  - every rostered player with 10+ starts against top-100 clubs in the last 365 days,
    his last 10 such scorelines, the positions he started in, nationality and age

The script is resumable. It spends at most MAX_REQUESTS calls per run, keeps everything
it has learned in data/cache/, and picks up where it stopped on the next run.
Free plan: 100 calls a day. Pro plan: 7,500 calls a day.

Environment:
  API_FOOTBALL_KEY     required
  MAX_REQUESTS         default 90
  SLEEP                seconds between calls (default 6.5 when MAX_REQUESTS <= 100, else 0.25)
  GRID_COL1_IS_RIGHT   1 or 0, see slot_code() (default 1)
  TODAY                YYYY-MM-DD override, for testing
  API_BASE             override the API host, for testing
"""
import csv, datetime as dt, difflib, json, os, re, sys, time, unicodedata, urllib.parse, urllib.request
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, 'data', 'cache')
OUT = os.path.join(ROOT, 'world.json')
API = os.environ.get('API_BASE', 'https://v3.football.api-sports.io').rstrip('/')
KEY = os.environ.get('API_FOOTBALL_KEY', '')
BUDGET = int(os.environ.get('MAX_REQUESTS', '90'))
SLEEP = float(os.environ.get('SLEEP', '6.5' if BUDGET <= 100 else '0.25'))
GRID_COL1_IS_RIGHT = os.environ.get('GRID_COL1_IS_RIGHT', '1') == '1'
TODAY = dt.date.fromisoformat(os.environ['TODAY']) if os.environ.get('TODAY') else dt.date.today()
SINCE = TODAY - dt.timedelta(days=365)
FINISHED = {'FT', 'AET', 'PEN'}
SQUAD_MAX_AGE = 7      # days between roster refreshes
PROFILE_MAX_AGE = 30   # days between nationality/age refreshes per club

used = 0
log_lines = []


def log(*a):
    msg = ' '.join(str(x) for x in a)
    print(msg, flush=True)
    log_lines.append(msg)


class OutOfBudget(Exception):
    pass


def api(path, **params):
    """One API call. Raises OutOfBudget when the run's allowance is spent."""
    global used
    if used >= BUDGET:
        raise OutOfBudget()
    url = f'{API}/{path}?{urllib.parse.urlencode(params)}'
    req = urllib.request.Request(url, headers={'x-apisports-key': KEY})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.load(r)
        except Exception as e:  # network hiccup
            if attempt == 3:
                raise
            time.sleep(10)
            continue
        used += 1
        errs = data.get('errors') or {}
        if errs:
            text = json.dumps(errs)
            if 'rateLimit' in text or 'Too many requests' in text:
                time.sleep(65)
                continue
            if 'requests' in text.lower() and 'limit' in text.lower():
                raise OutOfBudget()
            raise RuntimeError(f'API error for {path} {params}: {text}')
        time.sleep(SLEEP)
        return data
    raise RuntimeError(f'API kept refusing {path} {params}')


# ---------------- cache ----------------
def cpath(name):
    return os.path.join(CACHE_DIR, name + '.json')


def load(name, default):
    try:
        with open(cpath(name), encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save(name, obj):
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = cpath(name) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:   # one entry per line keeps git history small
        f.write('{\n' + ',\n'.join(json.dumps(k, ensure_ascii=False) + ':' + json.dumps(obj[k], ensure_ascii=False, separators=(',', ':'), sort_keys=True)
                                      for k in sorted(obj)) + '\n}\n')
    os.replace(tmp, cpath(name))


def norm(s):
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode().lower()
    for w in ('fc ', ' fc', 'cf ', 'afc ', 'sc ', ' sk', 'fk ', 'sk ', 'ac ', 'as ', 'ssc ', 'rcd ', 'kv'):
        s = s.replace(w, ' ')
    return ' '.join(s.split())


def days_old(stamp):
    return (TODAY - dt.date.fromisoformat(stamp)).days if stamp else 10 ** 6


# ---------------- positions ----------------
def slot_code(formation, grid, pos):
    """Turn formation + grid ('row:col') into one of the game's slot codes.
    Row 1 is the goalkeeper. GRID_COL1_IS_RIGHT says whether column 1 is the right side
    of the pitch (from the team's view). Check data/cache/position_check.txt after the
    first run and flip the setting if left and right come out mirrored."""
    fallback = {'G': 'GK', 'D': 'CB', 'M': 'CM', 'F': 'ST'}.get(pos, 'CM')
    try:
        lines = [int(x) for x in formation.split('-')]
        row, col = (int(x) for x in grid.split(':'))
    except Exception:
        return fallback
    if row == 1:
        return 'GK'
    li = row - 2
    if li < 0 or li >= len(lines):
        return fallback
    n = lines[li]
    k = (n - col) if GRID_COL1_IS_RIGHT else (col - 1)   # 0 = leftmost
    k = max(0, min(n - 1, k))
    last = len(lines) - 1
    if li == 0:  # back line
        if n >= 5:
            return 'LWB' if k == 0 else 'RWB' if k == n - 1 else 'CB'
        if n == 4:
            return 'LB' if k == 0 else 'RB' if k == 3 else 'CB'
        return 'CB'
    if li == last:  # front line
        if n >= 3:
            return 'LW' if k == 0 else 'RW' if k == n - 1 else 'ST'
        return 'ST'
    mids = len(lines) - 2           # number of midfield lines
    depth = li - 1                  # 0 = deepest midfield line
    deep = mids >= 2 and depth == 0
    top = mids >= 2 and depth == mids - 1
    if n >= 5:
        return 'LWB' if k == 0 else 'RWB' if k == n - 1 else ('DM' if deep else 'CM')
    if n == 4:
        if lines[0] == 3:
            return 'LWB' if k == 0 else 'RWB' if k == 3 else ('DM' if deep else 'CM')
        return 'LM' if k == 0 else 'RM' if k == 3 else ('DM' if deep else 'CM')
    if n == 3:
        if top:
            return 'LW' if k == 0 else 'RW' if k == 2 else 'AM'
        if mids == 1 and lines[0] >= 4:
            return 'DM' if k == 1 else 'CM'
        return 'CM'
    if n == 2:
        return 'DM' if deep else 'AM' if top else 'CM'
    return 'AM' if top else 'DM'


# ---------------- UEFA ranking ----------------
UEFA_URL = os.environ.get('UEFA_URL', 'https://comp.uefa.com/v2/coefficients')
UEFA_MAX_AGE = 7   # days; the ranking only moves on European match weeks
# UEFA association code -> (game code, API-Football country name)
UEFA_COUNTRY = {
    'ENG': ('ENG', 'England'), 'ESP': ('ESP', 'Spain'), 'GER': ('GER', 'Germany'), 'ITA': ('ITA', 'Italy'),
    'FRA': ('FRA', 'France'), 'POR': ('POR', 'Portugal'), 'NED': ('NED', 'Netherlands'), 'BEL': ('BEL', 'Belgium'),
    'SCO': ('SCO', 'Scotland'), 'TUR': ('TUR', 'Turkey'), 'GRE': ('GRE', 'Greece'), 'CZE': ('CZE', 'Czech-Republic'),
    'NOR': ('NOR', 'Norway'), 'DEN': ('DEN', 'Denmark'), 'SWE': ('SWE', 'Sweden'), 'UKR': ('UKR', 'Ukraine'),
    'AUT': ('AUT', 'Austria'), 'SUI': ('SUI', 'Switzerland'), 'CRO': ('CRO', 'Croatia'), 'SRB': ('SRB', 'Serbia'),
    'AZE': ('AZE', 'Azerbaijan'), 'HUN': ('HUN', 'Hungary'), 'POL': ('POL', 'Poland'), 'ROU': ('ROU', 'Romania'),
    'SVN': ('SLO', 'Slovenia'), 'SVK': ('SVK', 'Slovakia'), 'BUL': ('BUL', 'Bulgaria'), 'CYP': ('CYP', 'Cyprus'),
    'ISR': ('ISR', 'Israel'), 'KAZ': ('KAZ', 'Kazakhstan'), 'MDA': ('MDA', 'Moldova'), 'ARM': ('ARM', 'Armenia'),
    'BLR': ('BLR', 'Belarus'), 'FIN': ('FIN', 'Finland'), 'ISL': ('ISL', 'Iceland'), 'IRL': ('IRL', 'Ireland'),
    'WAL': ('WAL', 'Wales'), 'NIR': ('NIR', 'Northern-Ireland'), 'KVX': ('KOS', 'Kosovo'), 'BIH': ('BIH', 'Bosnia'),
    'MNE': ('MNE', 'Montenegro'), 'ALB': ('ALB', 'Albania'), 'MKD': ('MKD', 'Macedonia'), 'GEO': ('GEO', 'Georgia'),
    'LVA': ('LVA', 'Latvia'), 'LTU': ('LTU', 'Lithuania'), 'EST': ('EST', 'Estonia'), 'LUX': ('LUX', 'Luxembourg'),
    'MLT': ('MLT', 'Malta'), 'GIB': ('GIB', 'Gibraltar'), 'FRO': ('FRO', 'Faroe-Islands'), 'AND': ('AND', 'Andorra'),
    'SMR': ('SMR', 'San-Marino'), 'LIE': ('LIE', 'Liechtenstein'), 'RUS': ('RUS', 'Russia'),
}


def _get_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (ttny data update)', 'Accept': 'application/json',
                                               'Origin': 'https://www.uefa.com', 'Referer': 'https://www.uefa.com/'})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.load(r)


def _find_rows(obj):
    """Find the list of ranked clubs anywhere in UEFA's response."""
    if isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj) and any(('member' in x or 'team' in x) for x in obj):
            return obj
        for x in obj:
            r = _find_rows(x)
            if r:
                return r
    elif isinstance(obj, dict):
        for v in obj.values():
            r = _find_rows(v)
            if r:
                return r
    return None


def _parse_row(x):
    m = x.get('member') or x.get('team') or {}
    pos = (x.get('overallRanking') or {}).get('position') or x.get('position') or x.get('rank')
    name = m.get('displayName') or m.get('displayOfficialName') or m.get('internationalName') or m.get('name')
    ctry = m.get('country') if isinstance(m.get('country'), dict) else {}
    code = m.get('countryCode') or m.get('associationCode') or ctry.get('code')
    if not (pos and name and code):
        return None
    game_code, api_country = UEFA_COUNTRY.get(code, (code, m.get('countryName') or code))
    return {'rank': int(pos), 'name': name, 'code': game_code, 'country': api_country, 'uefa_id': str(m.get('id') or '')}


def fetch_uefa_ranking(meta):
    """Top 100 of the UEFA 5-year club ranking, refreshed weekly. Falls back to the last good list, then clubs.csv."""
    cached = load('uefa_ranking', {})
    if cached.get('clubs') and days_old(cached.get('fetched')) < UEFA_MAX_AGE:
        return cached['clubs'], 'UEFA (cached ' + cached['fetched'] + ')'
    season_year = TODAY.year + 1 if TODAY.month >= 7 else TODAY.year   # UEFA names a season by its end year
    errors = []
    for year in (season_year, season_year - 1):
        url = f'{UEFA_URL}?' + urllib.parse.urlencode({'coefficientRange': 'OVERALL', 'coefficientType': 'MEN_CLUB',
                                                       'language': 'EN', 'page': 1, 'pagesize': 200, 'seasonYear': year})
        try:
            rows = [r for r in (_parse_row(x) for x in (_find_rows(_get_json(url)) or [])) if r]
        except Exception as e:
            errors.append(f'{year}: {e}')
            continue
        rows.sort(key=lambda r: r['rank'])
        top = [r for r in rows if r['rank'] <= 100][:100]
        if len(top) >= 95:
            save('uefa_ranking', {'fetched': TODAY.isoformat(), 'season': year, 'clubs': top})
            return top, f'UEFA ranking {year - 1}/{str(year)[2:]}'
        errors.append(f'{year}: only {len(top)} clubs parsed')
    log('  WARNING: could not read the UEFA ranking (' + '; '.join(errors) + ')')
    if cached.get('clubs'):
        return cached['clubs'], 'UEFA (last good list from ' + cached['fetched'] + ')'
    return None, None


def read_clubs(meta):
    clubs, source = fetch_uefa_ranking(meta)
    if not clubs:
        with open(os.path.join(ROOT, 'pipeline', 'clubs.csv'), encoding='utf-8') as f:
            rows = [r for r in csv.DictReader(f) if r.get('rank', '').strip()]
        rows.sort(key=lambda r: int(r['rank']))
        clubs, source = rows[:100], 'pipeline/clubs.csv (fallback)'
    log(f'  club list: {source}, {len(clubs)} clubs')
    return clubs


def manual_ids():
    """pipeline/team_ids.csv: name,api_id. Fixes for clubs the search matches wrongly."""
    out = {}
    path = os.path.join(ROOT, 'pipeline', 'team_ids.csv')
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            for r in csv.DictReader(f):
                if r.get('name') and (r.get('api_id') or '').strip():
                    out[norm(r['name'])] = int(r['api_id'])
    with open(os.path.join(ROOT, 'pipeline', 'clubs.csv'), encoding='utf-8') as f:
        for r in csv.DictReader(f):
            if (r.get('api_id') or '').strip():
                out.setdefault(norm(r['name']), int(r['api_id']))
    return out


# UEFA short names -> what API-Football calls the club
UEFA_ALIAS = {
    'man city': 'Manchester City', 'man utd': 'Manchester United', 'atleti': 'Atletico Madrid', 'b. dortmund': 'Borussia Dortmund',
    'paris': 'Paris Saint Germain', 'frankfurt': 'Eintracht Frankfurt', 'gnk dinamo': 'Dinamo Zagreb', 'olympiacos': 'Olympiakos',
    'm. tel-aviv': 'Maccabi Tel Aviv', "nott'm forest": 'Nottingham Forest', 's. bratislava': 'Slovan Bratislava',
    'union sg': 'Union St. Gilloise', 'rapid': 'Rapid Vienna', 'bod/glimt': 'Bodo', 'salzburg': 'Red Bull Salzburg',
    'leipzig': 'RB Leipzig', 'leverkusen': 'Bayer Leverkusen', 'stuttgart': 'VfB Stuttgart', 'djurgarden': 'Djurgardens IF',
    'ferencvaros': 'Ferencvarosi TC', 'viktoria plzen': 'Plzen', 'psv': 'PSV Eindhoven', 'sporting cp': 'Sporting CP',
    'm. haifa': 'Maccabi Haifa', 'h. beer-sheva': 'Hapoel Beer Sheva', 'crvena zvezda': 'Crvena Zvezda', 'gladbach': 'Borussia Monchengladbach',
    'wolves': 'Wolverhampton', 'spurs': 'Tottenham', 'inter': 'Inter', 'milan': 'AC Milan', 'roma': 'AS Roma',
}
NOT_FIRST_TEAM = re.compile(r'(\bW\b|\bU1\d\b|\bU2\d\b|\bII\b|\bB\b|women|youth|reserves|femenino|feminin|\bF\b)\s*$', re.I)


def first_team(t):
    return not NOT_FIRST_TEAM.search(t.get('name') or '')


def tkey(c):
    return norm(c['name']) + '|' + c['code']


def search_terms(c):
    terms = []
    alias = UEFA_ALIAS.get(norm(c['name'])) or UEFA_ALIAS.get(c['name'].lower())
    if alias:
        terms.append(alias)
    if c.get('search'):
        terms.append(c['search'])
    plain = unicodedata.normalize('NFKD', c['name']).encode('ascii', 'ignore').decode()
    plain = ''.join(ch if ch.isalnum() or ch == ' ' else ' ' for ch in plain)
    words = [w for w in plain.split() if w.lower() not in ('fc', 'afc', 'cf', 'sc', 'fk', 'sk', 'ac', 'as', 'ssc', 'kv', 'club', 'de', 'cd', 'rc')]
    if words:
        terms.append(' '.join(words))
        longest = max(words, key=len)
        if len(longest) >= 4:
            terms.append(longest)
    seen, out = set(), []
    terms = [' '.join(''.join(ch if ch.isalnum() or ch == ' ' else ' ' for ch in
                              unicodedata.normalize('NFKD', t).encode('ascii', 'ignore').decode()).split()) for t in terms]
    for t in terms:
        if len(t) >= 3 and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out[:2]


def resolve_teams(clubs, teams):
    """Map every ranked club to an API team id (once per club; cached by name)."""
    fixes = manual_ids()
    for c in clubs:
        key = tkey(c)
        if norm(c['name']) in fixes:
            teams[key] = {'id': fixes[norm(c['name'])], 'name': c['name'], 'how': 'manual'}
            continue
        if key in teams and ((teams[key].get('id') and first_team(teams[key])) or days_old(teams[key].get('tried')) < 7):
            continue
        best = None
        for term in search_terms(c):
            data = api('teams', search=term)
            cands = [x['team'] for x in data.get('response', []) if not x['team'].get('national') and first_team(x['team'])]
            same = [t for t in cands if norm(t.get('country')) == norm(c['country'])]
            if same:
                target = norm(term)
                best = max(same, key=lambda t: max(difflib.SequenceMatcher(None, norm(t['name']), target).ratio(),
                                                   difflib.SequenceMatcher(None, norm(t['name']), norm(c['name'])).ratio()) - (0.05 if t['id'] > 5000 else 0))
                break
        if not best:
            log(f"  could not find {c['name']} ({c['country']}); add it to pipeline/team_ids.csv")
            teams[key] = {'id': None, 'name': None, 'how': 'missing', 'tried': TODAY.isoformat()}
            continue
        teams[key] = {'id': best['id'], 'name': best['name'], 'country': best.get('country'), 'how': 'search', 'uefa': c['name']}
        log(f"  {c['rank']:>3} {c['name']} -> {best['name']} ({best.get('country')}) id {best['id']}")


def add_fixture(fx, item):
    f, t, sc = item['fixture'], item['teams'], item.get('score') or {}
    ft = sc.get('fulltime') or {}
    hg, ag = ft.get('home'), ft.get('away')
    if hg is None:
        hg, ag = item['goals'].get('home'), item['goals'].get('away')
    fx[str(f['id'])] = {'d': f['date'][:10], 's': f['status']['short'], 'h': t['home']['id'], 'a': t['away']['id'],
                        'hg': hg, 'ag': ag, 'lg': item['league'].get('name')}


def season_list():
    return sorted({TODAY.year - 1, TODAY.year, SINCE.year})


def fetch_team_seasons(top_ids, fx, meta):
    """Initial load: every fixture of every top-100 club for the seasons that touch the last 365 days."""
    done = meta.setdefault('team_seasons', {})
    for tid in top_ids:
        for season in season_list():
            key = f'{tid}:{season}'
            if key in done:
                continue
            data = api('fixtures', team=tid, season=season)
            for item in data.get('response', []):
                add_fixture(fx, item)
            done[key] = TODAY.isoformat()


def fetch_recent_days(top, fx, meta):
    """Daily: pick up the last few days of results (one call per day, all competitions)."""
    seen = meta.setdefault('dates', {})
    for back in range(4, -1, -1):
        d = (TODAY - dt.timedelta(days=back)).isoformat()
        if seen.get(d) and back >= 2:   # older days only once; the last two days are re-read
            continue
        data = api('fixtures', date=d)
        n = 0
        for item in data.get('response', []):
            if item['teams']['home']['id'] in top or item['teams']['away']['id'] in top:
                add_fixture(fx, item)
                n += 1
        seen[d] = TODAY.isoformat()
        log(f'  {d}: {n} fixtures with a top-100 club')


def fetch_lineups(top, fx, lu):
    """Starting XIs for every finished fixture in the window that involves a top-100 club."""
    need = [fid for fid, f in fx.items()
            if f['s'] in FINISHED and SINCE.isoformat() <= f['d'] <= TODAY.isoformat()
            and (f['h'] in top or f['a'] in top)
            and (fid not in lu or (not lu[fid] and days_old(f['d']) <= 3))]
    need.sort(key=lambda fid: fx[fid]['d'], reverse=True)
    log(f'  {len(need)} fixtures need line-ups')
    for i in range(0, len(need), 20):
        batch = need[i:i + 20]
        data = api('fixtures', ids='-'.join(batch))
        got = set()
        for item in data.get('response', []):
            fid = str(item['fixture']['id'])
            add_fixture(fx, item)
            sides = {}
            for side in item.get('lineups') or []:
                form = side.get('formation') or ''
                xi = []
                for e in side.get('startXI') or []:
                    p = e.get('player') or {}
                    if p.get('id'):
                        xi.append([p['id'], slot_code(form, p.get('grid') or '', p.get('pos') or ''), p.get('name')])
                if xi:
                    sides[str(side['team']['id'])] = xi
            lu[fid] = sides
            got.add(fid)
        for fid in batch:
            lu.setdefault(fid, {})


def fetch_squads(top_ids, squads, people):
    stale = sorted(top_ids, key=lambda t: squads.get(str(t), {}).get('f', ''))
    for tid in stale:
        s = squads.get(str(tid))
        if s and days_old(s['f']) < SQUAD_MAX_AGE:
            continue
        data = api('players/squads', team=tid)
        players = (data.get('response') or [{}])[0].get('players', []) if data.get('response') else []
        squads[str(tid)] = {'f': TODAY.isoformat(), 'p': [p['id'] for p in players]}
        for p in players:
            e = people.setdefault(str(p['id']), {})
            e['n'] = e.get('n') or p.get('name')
            if p.get('age'):
                e['age'] = p['age']


def current_season():
    return TODAY.year if TODAY.month >= 7 else TODAY.year - 1


def fetch_profiles(top_ids, squads, people, meta):
    """Nationality and age, one club at a time (about 2-3 calls per club)."""
    done = meta.setdefault('profiles', {})
    for tid in top_ids:
        sq = squads.get(str(tid))
        if not sq:
            continue
        missing = [pid for pid in sq['p'] if 'nat' not in people.get(str(pid), {})]
        age = days_old(done.get(str(tid)))
        if age < PROFILE_MAX_AGE or (not missing and age < 180):
            continue
        page, total = 1, 1
        while page <= total:
            data = api('players', team=tid, season=current_season(), page=page)
            total = (data.get('paging') or {}).get('total', 1)
            for x in data.get('response', []):
                p = x['player']
                e = people.setdefault(str(p['id']), {})
                first = (p.get('firstname') or '').split(' ')[0]
                last = p.get('lastname') or ''
                e['n'] = f'{first} {last}'.strip() if first and last and len(first + last) < 26 else (p.get('name') or e.get('n'))
                e['nat'] = p.get('nationality')
                if p.get('age'):
                    e['age'] = p['age']
            page += 1
        done[str(tid)] = TODAY.isoformat()


# ---------------- build ----------------
def build_world(clubs, teams, fx, lu, squads, people):
    top = {}
    for c in clubs:
        t = teams.get(tkey(c))
        if t and t.get('id'):
            top[t['id']] = c
    window = [(fid, f) for fid, f in fx.items()
              if f['s'] in FINISHED and SINCE.isoformat() <= f['d'] <= TODAY.isoformat() and f['hg'] is not None]
    club_res = defaultdict(list)
    starts = defaultdict(list)
    for fid, f in window:
        h, a = f['h'], f['a']
        if h in top and a in top:
            club_res[h].append((f['d'], f['hg'], f['ag']))
            club_res[a].append((f['d'], f['ag'], f['hg']))
        sides = lu.get(fid) or {}
        for side, opp, sc, co in ((h, a, f['hg'], f['ag']), (a, h, f['ag'], f['hg'])):
            if opp in top and str(side) in sides:
                for pid, code, *_ in sides[str(side)]:
                    starts[pid].append((f['d'], sc, co, code))
    out_clubs = []
    for tid, c in top.items():
        res = sorted(club_res[tid], reverse=True)
        out_clubs.append({'id': tid, 'name': c['name'], 'ctry': c['code'], 'rank': int(c['rank']), 'n': len(res),
                          'sc10': [r[1] for r in res[:10]], 'co10': [r[2] for r in res[:10]]})
    out_players, seen = [], set()
    for tid, c in sorted(top.items(), key=lambda x: int(x[1]['rank'])):
        for pid in (squads.get(str(tid)) or {}).get('p', []):
            if pid in seen:
                continue
            st = sorted(starts.get(pid, []), reverse=True)
            if len(st) < 10:
                continue
            seen.add(pid)
            cnt = Counter(s[3] for s in st)
            pos = [k for k, _ in cnt.most_common()]
            e = people.get(str(pid), {})
            out_players.append({'id': pid, 'name': e.get('n') or str(pid), 'nat': e.get('nat'), 'age': e.get('age'),
                                'club': tid, 'prim': pos[0], 'pos': pos, 'starts': len(st),
                                'sc': [s[1] for s in st[:10]], 'co': [s[2] for s in st[:10]]})
    return {'updated': TODAY.isoformat(), 'source': 'API-Football', 'clubs': out_clubs, 'players': out_players}


def position_check(world):
    """A few players per club with their positions, to confirm left and right are not mirrored."""
    lines = ['Check that full-backs and wingers sit on the right side.',
             f'GRID_COL1_IS_RIGHT={int(GRID_COL1_IS_RIGHT)}. If left and right are swapped, flip it.', '']
    for p in world['players']:
        if p['prim'] in ('LB', 'RB', 'LW', 'RW', 'LWB', 'RWB'):
            lines.append(f"{p['prim']:<4} {p['name']} ({', '.join(p['pos'])})")
        if len(lines) > 60:
            break
    with open(os.path.join(CACHE_DIR, 'position_check.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


def main():
    if not KEY:
        sys.exit('Set API_FOOTBALL_KEY first.')
    meta = load('meta', {})
    clubs = read_clubs(meta)
    teams = load('teams', {})
    fx = load('fixtures', {})
    lu = load('lineups', {})
    squads = load('squads', {})
    people = load('people', {})
    stopped = None
    core_done = False
    top_ids = []
    try:
        log('1/6 Matching clubs to API teams')
        resolve_teams(clubs, teams)
        top = {teams[tkey(c)]['id'] for c in clubs if teams.get(tkey(c), {}).get('id')}
        top_ids = [teams[tkey(c)]['id'] for c in clubs if teams.get(tkey(c), {}).get('id')]
        log('2/6 Season fixtures (first run only)')
        fetch_team_seasons(top_ids, fx, meta)
        log('3/6 Latest results')
        fetch_recent_days(top, fx, meta)
        log('4/6 Line-ups')
        fetch_lineups(top, fx, lu)
        core_done = True
        log('5/6 Rosters')
        fetch_squads(top_ids, squads, people)
        log('6/6 Nationality and age')
        fetch_profiles(top_ids, squads, people, meta)
    except OutOfBudget:
        stopped = f'Stopped after {used} calls (MAX_REQUESTS={BUDGET}). The next run continues from here.'
        log(stopped)
    finally:
        cutoff = (TODAY - dt.timedelta(days=400)).isoformat()
        for fid in [k for k, f in fx.items() if f['d'] < cutoff]:
            fx.pop(fid, None)
            lu.pop(fid, None)
        for name, obj in (('teams', teams), ('fixtures', fx), ('lineups', lu), ('squads', squads), ('people', people), ('meta', meta)):
            save(name, obj)
    world = build_world(clubs, teams, fx, lu, squads, people)
    world['complete'] = stopped is None
    # the game reads world.json: publish only once results, line-ups and every roster are in
    have_rosters = all(str(t) in squads for t in top_ids)
    ready = core_done and have_rosters and world['players']
    target = OUT if ready else OUT.replace('world.json', 'world.partial.json')
    with open(target, 'w', encoding='utf-8') as f:
        json.dump(world, f, ensure_ascii=False, separators=(',', ':'))
    log(f'Wrote {os.path.basename(target)}')
    position_check(world)
    eligible_clubs = Counter(p['club'] for p in world['players'])
    log(f"world.json: {len(world['clubs'])} clubs, {len(world['players'])} rated players, "
        f"{sum(1 for v in eligible_clubs.values() if v >= 11)} clubs with 11+ rated players, {used} calls used")


if __name__ == '__main__':
    main()
