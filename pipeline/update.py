#!/usr/bin/env python3
"""
This Time Next Year: daily data update from API-Football (v3).

Builds world.json for the game:
  - every club on the UEFA 5-year ranking (CLUB_LIMIT caps it; pipeline/clubs.csv is the fallback)
  - every club's last 10 results against other ranked clubs
  - every rostered player with 10+ starts against ranked clubs in the last 365 days,
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
import random, bisect, statistics, csv, datetime as dt, difflib, html, json, math, os, re, sys, time, unicodedata, urllib.parse, urllib.request
from collections import Counter, defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, 'data', 'cache')
OUT = os.path.join(ROOT, 'world.json')
API = os.environ.get('API_BASE', 'https://v3.football.api-sports.io').rstrip('/')
KEY = os.environ.get('API_FOOTBALL_KEY', '')
BUDGET = int(os.environ.get('MAX_REQUESTS', '90'))
SLEEP = float(os.environ.get('SLEEP', '6.5' if BUDGET <= 100 else '0.25'))
GRID_COL1_IS_RIGHT = (os.environ.get('GRID_COL1_IS_RIGHT') or '0') == '1'   # checked on real data: column 1 is the left side
TODAY = dt.date.fromisoformat(os.environ['TODAY']) if os.environ.get('TODAY') else dt.date.today()
SINCE = TODAY - dt.timedelta(days=365)
FINISHED = {'FT', 'AET', 'PEN'}
SQUAD_MAX_AGE = 1      # days between roster refreshes: daily, so a transfer shows the next day
TRANSFER_MAX_AGE = 1   # days between transfer-list checks per ranked club (was 30)
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


TRANSLIT = str.maketrans({'ø': 'o', 'Ø': 'O', 'æ': 'ae', 'Æ': 'Ae', 'ð': 'd', 'Ð': 'D', 'þ': 'th', 'Þ': 'Th', 'ł': 'l', 'Ł': 'L',
                          'đ': 'd', 'Đ': 'D', 'ı': 'i', 'ß': 'ss', 'ə': 'a', 'Ə': 'A'})


def ascii_name(s):
    return unicodedata.normalize('NFKD', (s or '').translate(TRANSLIT)).encode('ascii', 'ignore').decode()


def norm(s):
    s = ascii_name(s).lower()
    for w in ('fc ', ' fc', 'cf ', 'afc ', 'sc ', ' sk', 'fk ', 'sk ', 'ac ', 'as ', 'ssc ', 'rcd ', 'kv'):
        s = s.replace(w, ' ')
    return ' '.join(s.split())


def days_old(stamp):
    return (TODAY - dt.date.fromisoformat(stamp)).days if stamp else 10 ** 6


# ---------------- positions ----------------
# The 14 preset formations of PES 2021 (lines from the back, each left to right).
PES_FORMATIONS = {
    '5-2-2-1': ['LB CB CB CB RB', 'DMF DMF', 'LMF RMF', 'CF'],
    '4-2-3-1': ['LB CB CB RB', 'DMF DMF', 'LMF AMF RMF', 'CF'],
    '4-1-4-1': ['LB CB CB RB', 'DMF', 'LMF AMF AMF RMF', 'CF'],
    '4-3-2-1': ['LB CB CB RB', 'CMF DMF CMF', 'AMF AMF', 'CF'],
    '4-2-2-2': ['LB CB CB RB', 'CMF CMF', 'LMF RMF', 'CF SS'],
    '4-3-1-2': ['LB CB CB RB', 'CMF DMF CMF', 'AMF', 'CF SS'],
    '4-2-1-3': ['LB CB CB RB', 'DMF DMF', 'AMF', 'LWF CF RWF'],
    '4-1-2-3': ['LB CB CB RB', 'DMF', 'AMF AMF', 'LWF CF RWF'],
    '3-2-4-1': ['CB CB CB', 'DMF DMF', 'LMF AMF AMF RMF', 'CF'],
    '3-2-3-2': ['CB CB CB', 'DMF DMF', 'LMF AMF RMF', 'CF SS'],
    '3-3-2-2': ['CB CB CB', 'LMF DMF RMF', 'CMF CMF', 'CF SS'],
    '3-2-2-3': ['CB CB CB', 'CMF CMF', 'LMF RMF', 'LWF CF RWF'],
    '5-2-1-2': ['LB CB CB CB RB', 'DMF DMF', 'AMF', 'CF SS'],
    '5-3-2':   ['LB CB CB CB RB', 'CMF DMF CMF', 'CF SS'],
}


def _video_code(formation, grid, pos):
    """PES 2021 position for one starter, from the API formation + grid ('row:col', row 1 = GK).
    A formation that is one of the 14 PES presets maps exactly to its PES positions.
    Any other real-world shape (4-3-3, 4-4-2, 3-4-2-1 ...) follows the same PES conventions.
    Returns None when the line-up came without formation data (the start still counts)."""
    try:
        lines = [int(x) for x in formation.split('-')]
        row, col = (int(x) for x in grid.split(':'))
    except Exception:
        return None
    if row == 1:
        return 'GK'
    li = row - 2
    if li < 0 or li >= len(lines) or len(lines) < 2:
        return None
    n = lines[li]
    k = (n - col) if GRID_COL1_IS_RIGHT else (col - 1)   # 0 = leftmost
    k = max(0, min(n - 1, k))
    if formation in PES_FORMATIONS:
        return PES_FORMATIONS[formation][li].split()[k]
    left, right = k == 0, k == n - 1
    last, back = len(lines) - 1, lines[0]
    wide = lambda centre: 'LMF' if left else 'RMF' if right else centre
    if li == 0:                                           # back line
        return ('LB' if left else 'RB' if right else 'CB') if n >= 4 else 'CB'
    if li == last:                                        # front line
        if n >= 3:
            return 'LWF' if left else 'RWF' if right else 'CF'
        if n == 2:
            return 'CF' if left else 'SS'                 # PES pairs a CF with an SS
        return 'CF'
    mids = last - 1
    depth = li - 1                                        # 0 = deepest midfield line
    front = lines[last]
    if mids == 1:                                         # one midfield line: 4-3-3, 4-4-2, 3-5-2, 5-4-1 ...
        if n >= 5:
            return wide('DMF' if k == n // 2 else 'CMF')
        if n == 4:
            return wide('CMF')
        if n == 3:
            return 'DMF' if k == 1 else 'CMF'
        return 'CMF' if n == 2 else 'DMF'
    nxt = lines[li + 1] if li + 1 < last else 0
    if depth == 0:                                        # deepest of two or more midfield lines
        if n == 1:
            return 'DMF'
        if n == 2:
            return 'CMF' if (nxt == 2 and front >= 2) else 'DMF'
        if n == 3:
            return wide('DMF') if back == 3 else ('DMF' if k == 1 else 'CMF')
        if n == 4:
            return wide('CMF')
        return wide('DMF' if k == n // 2 else 'CMF')
    if depth == mids - 1:                                 # most advanced midfield line
        prev = lines[li - 1]
        if n == 1:
            return 'AMF'                                  # 4-4-1-1, 4-2-1-3, 4-3-1-2 (no SS)
        if n == 2:
            if prev == 3 and back == 3:
                return 'CMF'                              # 3-3-2-2
            if (prev == 2 and front >= 2) or (front == 1 and back == 5):
                return 'LMF' if left else 'RMF'           # 4-2-2-2, 3-2-2-3, 5-2-2-1
            return 'AMF'                                  # 4-3-2-1, 3-4-2-1, 4-1-2-3
        if n == 3:
            return wide('AMF')                            # 4-2-3-1, 3-2-3-2
        return wide('AMF')                                # 4-1-4-1, 3-2-4-1
    return wide('CMF') if n >= 4 else 'CMF'               # a middle line in 4-1-2-1-2 style shapes


WIDE = {'LB', 'RB', 'LMF', 'RMF', 'LWF', 'RWF'}
POS_HALF_LIFE = 90    # days: a start this old counts half as much toward his positions (shorter predicts the next months a bit better, but makes positions jumpy)
FRIENDLY_W = 0.5      # ... and a friendly half as much again


def start_weight(day, fixture):
    try:
        age = max(0, (TODAY - dt.date.fromisoformat(day)).days)
    except (TypeError, ValueError):
        age = 0
    w = 0.5 ** (age / POS_HALF_LIFE)
    return w * (FRIENDLY_W if 'riendl' in (fixture.get('lg') or '') else 1.0)
BAND = {'GK': 0, 'CB': 1, 'LB': 1, 'RB': 1, 'DMF': 2, 'CMF': 2, 'LMF': 2, 'RMF': 2, 'AMF': 3, 'LWF': 3, 'RWF': 3, 'CF': 3}   # defence, midfield, attack


def lateral(formation, grid):
    """Where across his line a starter stood: -1 the left edge, 0 the middle, +1 the right edge (None without data)."""
    try:
        lines = [int(x) for x in formation.split('-')]
        row, col = (int(x) for x in grid.split(':'))
    except Exception:
        return None
    if row == 1 or row - 2 >= len(lines):
        return 0.0 if row == 1 else None
    n = lines[row - 2]
    if n < 2:
        return 0.0
    k = (n - col) if GRID_COL1_IS_RIGHT else (col - 1)
    return round(2 * max(0, min(n - 1, k)) / (n - 1) - 1, 3)


def slot_code(formation, grid, pos):
    """PES 2021 position: the video-based mapping above, with two changes:
    nothing is guessed across the width (all central players in one line share one position,
    the most common one in that line, CMF breaking ties), and there is no SS (it becomes CF)."""
    code = _video_code(formation, grid, pos)
    if code is None or code in WIDE or code == 'GK':
        return code
    try:
        lines = [int(x) for x in formation.split('-')]
        row = int(grid.split(':')[0])
        n = lines[row - 2]
    except Exception:
        return 'CF' if code == 'SS' else code
    line = [_video_code(formation, f'{row}:{c}', pos) for c in range(1, n + 1)]
    central = ['CF' if c == 'SS' else c for c in line if c and c not in WIDE]
    best = max(set(central), key=lambda c: (central.count(c), c == 'CMF'))
    return best


# ---------------- UEFA ranking ----------------
UEFA_URL = os.environ.get('UEFA_URL', 'https://comp.uefa.com/v2/coefficients')
CLUB_LIMIT = int(os.environ.get('CLUB_LIMIT') or 0)   # 0 = the whole UEFA list
UEFA_MAX_AGE = 8   # days; safety net if a Friday run is missed
UEFA_DAY = 4       # Friday: European matches are Tuesday to Thursday, so the ranking is fresh on Friday morning
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
    ov = x.get('overallRanking') or {}
    pts = next((v for v in (ov.get('totalValue'), ov.get('totalPoints'), ov.get('points'), ov.get('value'), x.get('totalValue'),
                            x.get('points'), (x.get('coefficient') or {}).get('value') if isinstance(x.get('coefficient'), dict) else x.get('coefficient'))
                if isinstance(v, (int, float)) or (isinstance(v, str) and v.replace('.', '', 1).isdigit())), None)
    return {'rank': int(pos), 'name': name, 'code': game_code, 'country': api_country, 'uefa_id': str(m.get('id') or ''),
            'points': float(pts) if pts is not None else None}


def fetch_uefa_ranking(meta):
    """The UEFA 5-year club ranking (all of it, or the first CLUB_LIMIT), refreshed every Friday. Falls back to the last good list, then clubs.csv."""
    cached = load('uefa_ranking', {})
    fresh = cached.get('fetched') == TODAY.isoformat() or (TODAY.weekday() != UEFA_DAY and days_old(cached.get('fetched')) < UEFA_MAX_AGE)
    # a cached list cut shorter than wanted (the old top-100 cache) is refetched
    fresh = fresh and len(cached.get('clubs') or []) >= (CLUB_LIMIT or cached.get('total', 101)) \
        and any(c.get('points') for c in cached.get('clubs') or [])   # the points are needed for the match model
    if cached.get('clubs') and fresh:
        return cached['clubs'], 'UEFA (cached ' + cached['fetched'] + ')'
    season_year = TODAY.year + 1 if TODAY.month >= 7 else TODAY.year   # UEFA names a season by its end year
    errors = []
    for year in (season_year, season_year - 1):
        rows, seen, failed = [], set(), None
        for page in range(1, 11):   # 200 a page; the list has a few hundred clubs
            url = f'{UEFA_URL}?' + urllib.parse.urlencode({'coefficientRange': 'OVERALL', 'coefficientType': 'MEN_CLUB',
                                                           'language': 'EN', 'page': page, 'pagesize': 200, 'seasonYear': year})
            try:
                got = [r for r in (_parse_row(x) for x in (_find_rows(_get_json(url)) or [])) if r]
            except Exception as e:
                failed = e
                break
            new = [r for r in got if (r['uefa_id'] or r['name']) not in seen]
            if not new:
                break
            seen.update(r['uefa_id'] or r['name'] for r in new)
            rows += new
            if len(got) < 200:
                break
        if failed and not rows:
            errors.append(f'{year}: {failed}')
            continue
        rows.sort(key=lambda r: r['rank'])
        top = rows[:CLUB_LIMIT] if CLUB_LIMIT else rows
        if len(top) >= 95:
            save('uefa_ranking', {'fetched': TODAY.isoformat(), 'season': year, 'total': len(rows), 'clubs': top})
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
        for r in rows:
            r.setdefault('points', None)
        rows.sort(key=lambda r: int(r['rank']))
        clubs, source = (rows[:CLUB_LIMIT] if CLUB_LIMIT else rows), 'pipeline/clubs.csv (fallback)'
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
    'paris': 'Paris Saint Germain', 'frankfurt': 'Eintracht Frankfurt', 'gnk dinamo': 'Dinamo Zagreb', 'olympiacos': 'Olympiakos Piraeus',
    'm. tel-aviv': 'Maccabi Tel Aviv', "nott'm forest": 'Nottingham Forest', 's. bratislava': 'Slovan Bratislava',
    'union sg': 'Gilloise', 'rakow': 'Czestochowa', 'rapid': 'Rapid Vienna', 'bod/glimt': 'Bodo', 'salzburg': 'Red Bull Salzburg',
    'leipzig': 'RB Leipzig', 'leverkusen': 'Bayer Leverkusen', 'stuttgart': 'VfB Stuttgart', 'djurgarden': 'Djurgardens IF',
    'ferencvaros': 'Ferencvarosi TC', 'viktoria plzen': 'Plzen', 'psv': 'PSV Eindhoven', 'sporting cp': 'Sporting CP',
    'm. haifa': 'Maccabi Haifa', 'h. beer-sheva': 'Hapoel Beer Sheva', 'crvena zvezda': 'Crvena Zvezda', 'gladbach': 'Borussia Monchengladbach',
    'wolves': 'Wolverhampton', 'spurs': 'Tottenham', 'inter': 'Inter', 'milan': 'AC Milan', 'roma': 'AS Roma',
    # beyond the top 100
    'sint-truidense': 'St. Truiden', 'n.e.c.': 'NEC Nijmegen', 'ofi crete': 'OFI', 'nordsjaelland': 'Nordsjaelland',
    'zire': 'Zira', 'dac 1904': 'Dunajska Streda', 'h. boltfelag': 'HB Torshavn', 'diddeleng': 'Dudelange', 'paksi': 'Paks',
    'puskas akademia': 'Puskas', 'polissya': 'Polissya', 'olexandriya': 'Oleksandria', 'araz-naxcivan': 'Araz',
    'gabala': 'Qabala', 'kr': 'KR Reykjavik', 'elimai': 'Semey', 'fcsg': 'Sfintul Gheorghe', 'brera strumica': 'Pandev',
    'pusk akademia': 'Puskas', 'd 1904': 'Dunajska Streda',   # keys as norm() leaves them
    'dynamo brest': 'Dinamo Brest', 'breidablik': 'Breidablik', 'lillestrom': 'Lillestrom', 'brondby': 'Brondby', 'sumqayit': 'Sumgayit',
}
NOT_FIRST_TEAM = re.compile(r'(\bW\b|\bU1\d\b|\bU2\d\b|\bII\b|\bIII\b|\bB\b|\b2\b|\b3\b|women|youth|reserves?|res\.?|juniors?|amateurs?|femenino|feminin|\bF\b)\s*$', re.I)


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
    plain = ascii_name(c['name'])
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


MATCH_V = 2   # bump to match every club again


def uefa_pool(meta):
    """Every team that played in the Champions, Europa or Conference League (qualifiers included) in the last six
    seasons: a club on the UEFA ranking earned its points there, so this is where its API team is. Refreshed monthly."""
    pool = meta.get('uefa_pool') or {}
    if pool.get('t') and days_old(pool.get('f')) < 30:
        return pool['t']
    t = {}
    for league in (2, 3, 848):
        for season in range(current_season() - 5, current_season() + 1):
            for x in api('teams', league=league, season=season).get('response', []):
                tm = x.get('team') or {}
                if tm.get('id') and not tm.get('national'):
                    t[str(tm['id'])] = [tm.get('name'), tm.get('country')]
    if t:
        meta['uefa_pool'] = {'f': TODAY.isoformat(), 't': t}
    return t


def _toks(s):
    s = ''.join(ch if ch.isalnum() else ' ' for ch in ascii_name(s or '').lower())
    return [w for w in s.split() if w not in ('fc', 'afc', 'cf', 'sc', 'fk', 'sk', 'ac', 'as', 'ssc', 'rcd', 'kv', 'club', 'nk', 'hnk', 'gnk', 'cd', 'sv', 'vfb', 'vfl')]


def name_score(name, c, terms):
    a = _toks(name)
    sc = max(difflib.SequenceMatcher(None, ' '.join(a), ' '.join(_toks(x))).ratio() for x in [c['name']] + terms)
    # every word of the UEFA name starting a word of the API name (Malmö in Malmö FF, Hearts in Heart of Midlothian)
    # counts as a near match
    words = [w for w in _toks(c['name']) if len(w) > 2]
    if words and all(any(v.startswith(w[:5]) or w.startswith(v) and len(v) > 3 for v in a) for w in words):
        sc = max(sc, 0.85 + 0.1 * sc)
    return sc


def resolve_teams(clubs, teams, meta=None):
    """Map every ranked club to an API team id (once per club; cached by name). First among the teams that played in
    UEFA competitions (same country, best name), then by the API's name search."""
    fixes = manual_ids()
    pool = None
    for c in clubs:
        key = tkey(c)
        if norm(c['name']) in fixes:
            teams[key] = {'id': fixes[norm(c['name'])], 'name': c['name'], 'how': 'manual'}
            continue
        if key in teams and teams[key].get('v') == MATCH_V and ((teams[key].get('id') and first_team(teams[key])) or days_old(teams[key].get('tried')) < 7):
            continue
        if pool is None:
            pool = uefa_pool(meta if meta is not None else {})
        terms = search_terms(c)
        cands = [(name_score(nm, c, terms), int(tid), nm, ct) for tid, (nm, ct) in pool.items()
                 if norm(ct) == norm(c['country']) and first_team({'name': nm})]
        if cands:
            sc, tid, nm, ct = max(cands)
            if sc >= 0.6:
                old = (teams.get(key) or {}).get('id')
                teams[key] = {'id': tid, 'name': nm, 'country': ct, 'how': 'uefa', 'uefa': c['name'], 'v': MATCH_V}
                if old != tid:
                    log(f"  {c['rank']:>3} {c['name']} -> {nm} ({ct}) id {tid}" + (f' (was {old})' if old else ''))
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
            teams[key] = {'id': None, 'name': None, 'how': 'missing', 'tried': TODAY.isoformat(), 'v': MATCH_V}
            continue
        teams[key] = {'id': best['id'], 'name': best['name'], 'country': best.get('country'), 'how': 'search', 'uefa': c['name'], 'v': MATCH_V}
        log(f"  {c['rank']:>3} {c['name']} -> {best['name']} ({best.get('country')}) id {best['id']}")


def add_fixture(fx, item):
    f, t, sc = item['fixture'], item['teams'], item.get('score') or {}
    ft = sc.get('fulltime') or {}
    hg, ag = ft.get('home'), ft.get('away')
    if hg is None:
        hg, ag = item['goals'].get('home'), item['goals'].get('away')
    fx[str(f['id'])] = {'d': f['date'][:10], 's': f['status']['short'], 'h': t['home']['id'], 'a': t['away']['id'],
                        'hg': hg, 'ag': ag, 'lg': item['league'].get('name'),
                        'hn': t['home'].get('name'), 'an': t['away'].get('name')}


def season_list():
    return sorted({TODAY.year - 1, TODAY.year, SINCE.year})


def fetch_team_seasons(top_ids, fx, meta, tolerant=False):
    """Initial load: every fixture of every ranked club for the seasons that touch the last 365 days."""
    done = meta.setdefault('team_seasons', {})
    for tid in top_ids:
        for season in season_list():
            key = f'{tid}:{season}'
            if key in done:
                continue
            try:
                data = api('fixtures', team=tid, season=season)
            except RuntimeError as e:
                if not tolerant:
                    raise
                log(f'  fixtures failed for team {tid} {season}: {e}'); done[key] = TODAY.isoformat(); continue
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
        log(f'  {d}: {n} fixtures with a ranked club')


def wanted(f, until):
    """A match of a club a player came from counts only while he was there (until: team -> (from, to))."""
    return any(t not in until or until[t][0] <= f['d'] <= until[t][1] for t in (f['h'], f['a']) if t in until or t in wanted.base)
wanted.base = set()


def fetch_lineups(top, fx, lu, evs, pst=None, first=frozenset(), until=None, subs=None, backfill=False):
    pst = {} if pst is None else pst
    subs = {} if subs is None else subs
    """Starting XIs and goals (scorer, assist) for every finished fixture in the window that involves a ranked club."""
    need = [fid for fid, f in fx.items()
            if f['s'] in FINISHED and SINCE.isoformat() <= f['d'] <= TODAY.isoformat()
            and (f['h'] in top or f['a'] in top)
            and (until is None or wanted(f, until))
            and (fid not in lu or (not lu[fid] and days_old(f['d']) <= 3) or any(isinstance(v, list) for v in lu[fid].values())
                 or (lu[fid] and fid not in evs) or fid not in pst
                 # matches without the API's player stats, fetched before substitutions were kept: who came off the bench
                 or (backfill and not pst.get(fid) and fid not in subs)
                 # line-ups without a formation, fetched before the API's rough position (G, D, M, F) was kept
                 or (backfill and any(isinstance(v, dict) and v.get('xi') and not v.get('f') and len(v['xi'][0]) < 4
                                      for v in lu[fid].values())))]
    need.sort(key=lambda fid: fx[fid]['d'], reverse=True)
    # national-team matches before the (many) matches of clubs players came from, newest first within each
    need.sort(key=lambda fid: not (fx[fid]['h'] in first or fx[fid]['a'] in first))
    # ... and the substitution backfill last, so it never holds up anything new
    need.sort(key=lambda fid: fid in lu and fid in evs and fid in pst)
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
                xi = []
                for e in side.get('startXI') or []:
                    p = e.get('player') or {}
                    if p.get('id'):
                        xi.append([p['id'], p.get('grid') or '', p.get('name'), p.get('pos') or ''])   # pos: G, D, M or F
                if xi:   # raw formation + grid, so the position model can change without refetching
                    sides[str(side['team']['id'])] = {'f': side.get('formation') or '', 'xi': xi}
            lu[fid] = sides
            goals = []
            for e in item.get('events') or []:
                if (e.get('type') or '').lower() != 'goal' or 'missed' in (e.get('detail') or '').lower():
                    continue
                kind = 'o' if 'own' in (e.get('detail') or '').lower() else 'p' if 'penalty' in (e.get('detail') or '').lower() else 'n'
                goals.append([(e.get('team') or {}).get('id'), (e.get('player') or {}).get('id'), (e.get('assist') or {}).get('id'),
                              kind, (e.get('time') or {}).get('elapsed')])
            evs[fid] = goals
            # substitutions: [team, player, other player, minute]; which one came on is decided against the XI later
            subs[fid] = [[(e.get('team') or {}).get('id'), (e.get('player') or {}).get('id'), (e.get('assist') or {}).get('id'),
                          (e.get('time') or {}).get('elapsed')] for e in item.get('events') or []
                         if (e.get('type') or '').lower() == 'subst']
            # per player: [minutes, match rating, team id] from the API's own player statistics
            ps = {}
            for side in item.get('players') or []:
                tid = (side.get('team') or {}).get('id')
                for e in side.get('players') or []:
                    pid = (e.get('player') or {}).get('id'); st = (e.get('statistics') or [{}])[0] or {}
                    g = st.get('games') or {}
                    try:
                        rt = round(float(g.get('rating')), 2) if g.get('rating') else None
                    except (TypeError, ValueError):
                        rt = None
                    if pid and (g.get('minutes') or 0) > 0:
                        ps[str(pid)] = [g.get('minutes') or 0, rt, tid]
            pst[fid] = ps
            got.add(fid)
        for fid in batch:
            lu.setdefault(fid, {})
            pst.setdefault(fid, {})


def fetch_squads(top_ids, squads, people):
    stale = sorted(top_ids, key=lambda t: squads.get(str(t), {}).get('f', ''))
    for tid in stale:
        s = squads.get(str(tid))
        if s and days_old(s['f']) < SQUAD_MAX_AGE and s.get('v') == 2:   # v2: the squad's positions are kept too
            continue
        data = api('players/squads', team=tid)
        players = (data.get('response') or [{}])[0].get('players', []) if data.get('response') else []
        squads[str(tid)] = {'f': TODAY.isoformat(), 'p': [p['id'] for p in players], 'v': 2}
        for p in players:
            e = people.setdefault(str(p['id']), {})
            e['sq'] = p.get('name')
            e['n'] = e.get('n') or p.get('name')
            if p.get('age'):
                e['age'] = p['age']
            sp = {'Goalkeeper': 'G', 'Defender': 'D', 'Midfielder': 'M', 'Attacker': 'F'}.get(p.get('position'))
            if sp:
                e['sp'] = sp   # his rough position on the club's squad list


def current_season():
    return TODAY.year if TODAY.month >= 7 else TODAY.year - 1


def fetch_profiles(top_ids, squads, people, meta):
    """Nationality and age, one club at a time (about 2-3 calls per club)."""
    done = meta.setdefault('profiles', {})
    for tid in top_ids:
        sq = squads.get(str(tid))
        if not sq:
            continue
        missing = [pid for pid in sq['p'] if 'nat' not in people.get(str(pid), {}) or 'fn' not in people.get(str(pid), {})]
        age = days_old(done.get(str(tid)))
        if (age < PROFILE_MAX_AGE and not any('fn' not in people.get(str(pid), {}) for pid in sq['p'])) or (not missing and age < 180):
            continue
        for season in (current_season(), current_season() - 1):   # smaller leagues often lack the current season
            if season != current_season() and not [pid for pid in sq['p'] if 'nat' not in people.get(str(pid), {})]:
                break
            read_players(tid, season, people)
        done[str(tid)] = TODAY.isoformat()


def fetch_missing_nat(squads, people, lu, fx):
    """Squad members the club listing gave no nationality (often a club whose league the API has no player stats for):
    ask for each player on his own. Starters first, most starts first; a miss is retried after 30 days."""
    since = SINCE.isoformat()
    starts = Counter()
    for fid, sides in lu.items():
        f = fx.get(fid)
        if not f or f['d'] < since or not isinstance(sides, dict):
            continue
        for v in sides.values():
            if isinstance(v, dict):
                for e in v.get('xi') or []:
                    starts[e[0]] += 1
    todo = sorted({pid for sq in squads.values() if isinstance(sq, dict) for pid in sq.get('p', [])
                   if not (people.get(str(pid)) or {}).get('nat') and days_old((people.get(str(pid)) or {}).get('natTried')) >= 30},
                  key=lambda pid: -starts[pid])
    todo = [pid for pid in todo if starts[pid] > 0] + [pid for pid in todo if starts[pid] == 0]
    if todo:
        log(f'  nationality missing for {len(todo)} squad members ({sum(1 for p in todo if starts[p])} with starts)')
    for pid in todo:
        e = people.setdefault(str(pid), {})
        e['natTried'] = TODAY.isoformat()
        hit = None
        try:
            hit = ((api('players/profiles', player=pid).get('response') or [{}])[0] or {}).get('player')
        except OutOfBudget:
            raise
        except Exception:
            hit = None
        if not (hit and hit.get('nationality')):
            for season in (current_season(), current_season() - 1):
                try:
                    r = api('players', id=pid, season=season).get('response') or []
                except OutOfBudget:
                    raise
                except Exception:
                    r = []
                if r and r[0]['player'].get('nationality'):
                    hit = r[0]['player']; break
        if hit and hit.get('nationality'):
            e['nat'] = hit['nationality']
            if hit.get('firstname') is not None: e['fn'] = hit.get('firstname') or ''
            if hit.get('age'): e['age'] = hit['age']
            first = (hit.get('firstname') or '').split(' ')[0]; last = hit.get('lastname') or ''
            if first and last and len(first + last) < 26: e['n'] = f'{first} {last}'


def read_players(tid, season, people):
        page, total = 1, 1
        while page <= total:
            data = api('players', team=tid, season=season, page=page)
            total = (data.get('paging') or {}).get('total', 1)
            for x in data.get('response', []):
                p = x['player']
                e = people.setdefault(str(p['id']), {})
                first = (p.get('firstname') or '').split(' ')[0]
                last = p.get('lastname') or ''
                e['n'] = f'{first} {last}'.strip() if first and last and len(first + last) < 26 else (p.get('name') or e.get('n'))
                e['nat'] = p.get('nationality')
                e['fn'] = p.get('firstname') or ''
                if p.get('age'):
                    e['age'] = p['age']
            page += 1


# ---------------- beyond the ranked clubs: national teams and clubs players came from ----------------
def fetch_nations(people, squads, extra, meta):
    """The national team of every nationality among the ranked clubs' players (one search per country, once)."""
    nats = sorted({people.get(str(pid), {}).get('nat') for sq in squads.values() for pid in sq.get('p', [])} - {None, ''})
    tried = meta.setdefault('nations_tried', {})
    have = {v.get('nat') for v in extra.values() if v.get('kind') == 'nat'}
    for nat in nats:
        if nat in have or days_old(tried.get(nat)) < 30:
            continue
        term = ''.join(ch if ch.isalnum() or ch == ' ' else ' ' for ch in ascii_name(nat)).strip()
        try:   # the search takes letters, digits and spaces only
            data = api('teams', search=term) if len(term) >= 3 else {}
        except RuntimeError as e:
            log(f'  national team search failed for {nat}: {e}'); tried[nat] = TODAY.isoformat(); continue
        hit = [x['team'] for x in data.get('response', []) if x['team'].get('national') and norm(x['team'].get('name')) == norm(nat)]
        if hit:
            extra[str(hit[0]['id'])] = {'kind': 'nat', 'nat': nat, 'name': hit[0]['name']}
        tried[nat] = TODAY.isoformat()


def fetch_arrivals(top_ids, extra, meta, squads, careers=None):
    """Every other club today's players played for in the window. A ranked club's transfers list each player's whole
    transfer history, so a path like X -> Y -> ranked club gives both X and Y. For each club he left inside the window:
    the time he was there (from his move in, or the window's start, until the day he left). Only his matches there
    count, so only that club's matches in that stretch are needed. One transfers call per ranked club, daily."""
    if meta.get('transfers_v') != 3:   # earlier versions took clubs players left to, and whole seasons
        meta['transfers'] = {}; meta['transfers_v'] = 3
        for k in [k for k, v in extra.items() if v.get('kind') == 'club']:
            extra.pop(k)
    done = meta.setdefault('transfers', {})
    if careers is not None and meta.get('careers_v') != 2:
        done.clear(); meta['careers_v'] = 2   # careers merged from every club's list: every club once more, to collect them
    top = set(top_ids)
    members = {pid for sq in squads.values() for pid in (sq.get('p') or [])}
    lo, hi = SINCE.isoformat(), TODAY.isoformat()
    for tid in sorted(top_ids, key=lambda t: done.get(str(t)) or ''):   # the longest unchecked first, if the budget runs out
        if days_old(done.get(str(tid))) < TRANSFER_MAX_AGE:
            continue
        try:
            data = api('transfers', team=tid)
        except OutOfBudget:
            raise
        except Exception as e:   # one club's list failing (an API error, a timeout) does not stop the others
            log(f'  transfers failed for team {tid}: {e!r}'); continue
        for x in data.get('response', []):
            if (x.get('player') or {}).get('id') not in members:
                continue
            moves = sorted((t for t in x.get('transfers') or [] if t.get('date')), key=lambda t: t['date'])
            if careers is not None and moves:   # his transfer history, for the guess-who game: each club's list holds only part
                # of it (a past club may list a move or two), so every list he appears in is merged in, never replacing
                key = str(x['player']['id']); have = {(m[0], m[1], m[3]) for m in careers.get(key, [])}
                new = [[t['date'], ((t.get('teams') or {}).get('out') or {}).get('id'), ((t.get('teams') or {}).get('out') or {}).get('name'),
                                                    ((t.get('teams') or {}).get('in') or {}).get('id'), ((t.get('teams') or {}).get('in') or {}).get('name'), t.get('type') or ''] for t in moves]
                careers[key] = sorted(careers.get(key, []) + [m for m in new if (m[0], m[1], m[3]) not in have], key=lambda m: m[0])
            for k, t in enumerate(moves):
                d = t['date']
                left = (t.get('teams') or {}).get('out') or {}
                if not (lo <= d <= hi) or not left.get('id') or left['id'] in top:
                    continue   # outside the window, or a ranked club (all its matches are followed anyway)
                start = max(lo, moves[k - 1]['date']) if k else lo   # he joined that club with the move before
                e = extra.setdefault(str(left['id']), {'kind': 'club', 'name': left.get('name'), 'from': start, 'until': d})
                e['from'] = min(e.get('from') or start, start); e['until'] = max(e.get('until') or d, d)
        done[str(tid)] = TODAY.isoformat()


# ---------------- build ----------------
def fix_text(t):
    """Repair names that arrive double-encoded from the API ('CvetkoviÄ\x87' -> 'Cvetković')."""
    t = html.unescape(t) if t else t   # 'O&apos;Reilly' -> "O'Reilly"
    if t and re.search('[\xc2-\xf4][\x80-\xbf]', t):
        # piece by piece, so a name mixing real letters and broken ones ('Vukašin KrstiÄ\x87') is repaired too
        def one(m):
            try:
                return m.group(0).encode('latin-1').decode('utf-8')
            except (UnicodeEncodeError, UnicodeDecodeError):
                return m.group(0)
        t = re.sub('[\xc2-\xf4][\x80-\xbf]+', one, t)
    if t:   # stray escapes and double spaces from the API ('Yoram\\t Zague', 'John  McGinn')
        t = re.sub(r'\\[tnr]|[\t\n\r]', ' ', t)
        t = re.sub(r'\s{2,}', ' ', t).strip()
    return t


def display_name(e, pid):
    """'L. Díaz' + first name 'Luis Fernando' -> 'Luis Díaz'; 'Brahim Díaz' and 'Endrick' stay as they are."""
    sq, firsts = e.get('sq') or '', (e.get('fn') or '').split()
    m = re.match(r'^([A-ZÀ-Ý][a-z]?)\. ', sq)
    if m and firsts:
        # 'E. Martínez' + 'Damián Emiliano' -> 'Emiliano Martínez': use the first name the initial points to
        first = next((f for f in firsts if f.startswith(m.group(1))), firsts[0])
        return first + ' ' + sq.split('. ', 1)[1]
    return sq or e.get('n') or str(pid)


DEF_POS = {'GK', 'CB', 'LB', 'RB', 'DMF'}
MIN_STARTS = int(os.environ.get('MIN_STARTS', '1'))   # starts in the window a player needs to be rated and drafted


def card_raw(pid, st, ap, late_n, pst, national):
    """Raw numbers behind the card's extra A-E grades (for show: they do not feed the simulation)."""
    mins = max(ap[1], 1)
    rated = []   # (date, rating, opponent rank, at home) for his starts with a match rating
    for s in st:
        v = (pst.get(s[4]) or {}).get(str(pid))
        if v and v[1] and 3 <= v[1] <= 10:
            rated.append((s[0], v[1], s[5], s[6]))
    rs = [x[1] for x in rated]
    mean = lambda xs: sum(xs) / len(xs) if xs else None
    recent = [x[1] for x in rated if (TODAY - dt.date.fromisoformat(x[0])).days <= 90]
    older = [x[1] for x in rated if (TODAY - dt.date.fromisoformat(x[0])).days > 90]
    big = [x[1] for x in rated if 0 < x[2] <= 40]
    away = [x[1] for x in rated if not x[3]]
    n = len(st)
    return {
        'fin': (ap[4] - ap[5]) / max(mins, 1000) * 90,            # non-penalty goals per 90
        'cre': ap[6] / max(mins, 1000) * 90,                      # assists per 90
        'pen': ap[5],                                            # penalty goals
        'clu': late_n / max(mins, 1000) * 90,                     # late goals and assists per 90
        'big': (sum(big) + 3 * 6.7) / (len(big) + 3) if big else None,     # rating against top-40 UEFA clubs
        'con': -statistics.pstdev(rs) if len(rs) >= 5 else None,           # steadiness of his match ratings
        'fit': ap[1],                                            # minutes played
        'int': sum(1 for s in st if s[8] in national),           # national team starts
        'win': (sum(1 for s in st if s[1] > s[2]) + 2) / (n + 5),          # wins in his starts, shrunk
        'def': (sum(1 for s in st if s[2] == 0) + 1) / (n + 4),            # clean sheets in his starts, shrunk
        'ver': sum(1 for k, v in Counter(s[3] for s in st if s[3]).items() if v >= 2),   # positions with 2+ starts
        'mom': (mean(recent) - mean(older)) if len(recent) >= 3 and len(older) >= 3 else None,   # rating trend
        'awy': (sum(away) + 3 * 6.7) / (len(away) + 3) if away else None,  # rating away from home
        'inv': (ap[4] + ap[6]) / max(mins, 1000) * 90,           # goals and assists per 90
    }


CARD_KEYS = ['fin', 'cre', 'pen', 'clu', 'big', 'con', 'fit', 'int', 'win', 'def', 'ver', 'mom', 'awy', 'inv']
COUNT_KEYS = {'pen', 'int'}   # counts where zero means none at all: zero stays at the bottom


def card_grades(players):
    """Percentile of each raw number within the player's line (goalkeepers, defenders, midfielders, forwards)."""
    from model import LINE
    by_line = defaultdict(list)
    for p in players:
        by_line[LINE.get(p['prim'], 'MID')].append(p)
    for ps in by_line.values():
        for k in CARD_KEYS:
            vals = sorted(p['_raw'][k] for p in ps if p['_raw'][k] is not None)
            for p in ps:
                v = p['_raw'][k]
                if v is None:
                    q = 0.5
                elif k in COUNT_KEYS and v == 0:
                    q = 0.0
                else:
                    lo = bisect.bisect_left(vals, v); hi = bisect.bisect_right(vals, v)
                    q = (lo + (hi - lo) / 2) / len(vals)
                p.setdefault('gr', []).append(round(q, 2))
    for p in players:
        p.pop('_raw', None)


def monthly(m, st, month):
    """[goals, assists, starts, clean sheets in goal] per month, newest first, trailing zeros dropped."""
    m = [r[:] for r in m]
    for s in st:
        k = month(s[0]); m[2][k] += 1
        if s[3] == 'GK' and s[2] == 0: m[3][k] += 1
    out = []
    for r in m:
        while r and r[-1] == 0: r.pop()
        out.append(r)
    return out


def build_world(clubs, teams, fx, lu, squads, people, evs=None, pst=None, extra=None, subs=None):
    subs = load('subs', {}) if subs is None else subs
    evs = evs or {}; pst = pst or {}; extra = extra or {}
    national = {int(k) for k, v in extra.items() if v.get('kind') == 'nat'}
    top = {}
    for c in clubs:
        t = teams.get(tkey(c))
        if t and t.get('id'):
            top[t['id']] = c
    window = [(fid, f) for fid, f in fx.items()
              if f['s'] in FINISHED and SINCE.isoformat() <= f['d'] <= TODAY.isoformat() and f['hg'] is not None]
    # who came off the bench in each match: from the substitutions, the one of the pair who was not in the XI
    # (only where the API has no player stats; where it has them, its minutes already say who played)
    bench_in = {}
    for fid, f in window:
        if pst.get(fid) or not subs.get(fid):
            continue
        ins = []
        for t, p1, p2, mn in subs[fid]:
            L = (lu.get(fid) or {}).get(str(t))
            xi = {e[0] for e in L.get('xi') or []} if isinstance(L, dict) else set()
            if not xi:
                continue
            came = [q for q in (p1, p2) if q and q not in xi]
            if len(came) == 1 and (p1 in xi or p2 in xi):
                ins.append((came[0], t))
        bench_in[fid] = ins
    # line-ups without a formation (many smaller leagues): a player's usual position from the line-ups that have one,
    # else the API's rough one (G, D, M, F as GK, CB, CMF, CF)
    usual = defaultdict(Counter)
    for fid, f in window:
        for side, L in (lu.get(fid) or {}).items():
            if isinstance(L, dict) and L.get('xi'):
                cs = [(e[0], slot_code(L['f'], e[1], '')) for e in L['xi']]
                if all(c for _, c in cs):
                    for pid, c in cs: usual[pid][c] += 1
    ROUGH = {'G': 'GK', 'D': 'CB', 'M': 'CMF', 'F': 'CF'}
    LETTER = {'GK': 'G', 'CB': 'D', 'LB': 'D', 'RB': 'D', 'DMF': 'M', 'CMF': 'M', 'LMF': 'M', 'RMF': 'M', 'AMF': 'M',
              'LWF': 'F', 'RWF': 'F', 'CF': 'F'}
    letters = defaultdict(Counter)   # the API's rough position (G, D, M, F) in every line-up that has it
    for fid, f in window:
        for L in (lu.get(fid) or {}).values():
            if isinstance(L, dict):
                for e in L.get('xi') or []:
                    if len(e) > 3 and e[3]: letters[e[0]][e[3]] += 1
    for pid_s, e in people.items():   # ... else the squad list's
        if e.get('sp') and int(pid_s) not in letters:
            letters[int(pid_s)][e['sp']] += 1
    def rough_codes(L):
        out = []
        for e in L['xi']:
            lt = (e[3] if len(e) > 3 else '') or (letters[e[0]].most_common(1)[0][0] if letters.get(e[0]) else '')
            # his usual position in that line (the API's letter), else his usual position, else the line's plain one
            same = [(n, c) for c, n in usual[e[0]].items() if LETTER.get(c) == lt] if usual.get(e[0]) else []
            c = max(same)[1] if same else usual[e[0]].most_common(1)[0][0] if usual.get(e[0]) else ROUGH.get(lt)
            if c:
                out.append((e[0], c))
        # a player we know nothing about (gone since, say) is left out; the rest of the line-up still counts
        return out if len(out) >= 9 and sum(1 for _, c in out if c == 'GK') <= 1 else []
    club_res = defaultdict(list)
    starts = defaultdict(list)
    lats = defaultdict(list)   # where across the pitch he started: -1 left edge of his line ... +1 right edge
    for fid, f in window:
        h, a = f['h'], f['a']
        sides = lu.get(fid) or {}
        for side, opp, sc, co in ((h, a, f['hg'], f['ag']), (a, h, f['ag'], f['hg'])):
            lineup = sides.get(str(side))
            lineup = lineup if isinstance(lineup, dict) else None
            codes = [(e[0], slot_code(lineup['f'], e[1], '')) for e in lineup['xi']] if lineup else []
            if lineup and codes and all(c for _, c in codes):
                for e in lineup['xi']:
                    lv = lateral(lineup['f'], e[1])
                    if lv is not None: lats[e[0]].append(lv)
            # every match counts, friendlies included, but only for a side whose line-up came with all positions
            rough = False
            if lineup and not (codes and all(c for _, c in codes)) and not lineup.get('f'):
                codes, rough = rough_codes(lineup), True
            if not (codes and all(c for _, c in codes)):
                continue
            if side in top and opp in top:
                club_res[side].append((f['d'], sc, co, int(top[opp]['rank']), side == h))
            # a player's starts: every match with positions, ranked opponent or not (opponent rank 0 = off the list)
            for pid, code in codes:
                starts[pid].append((f['d'], sc, co, code, fid, int(top[opp]['rank']) if opp in top else 0, side == h,
                                    int(top[side]['rank']) if side in top else 0, side, rough))
    out_clubs = []
    for tid, c in top.items():
        res = sorted(club_res[tid], reverse=True)
        out_clubs.append({'id': tid, 'name': c['name'], 'ctry': c['code'], 'rank': int(c['rank']), 'n': len(res),
                          'sc10': [r[1] for r in res[:10]], 'co10': [r[2] for r in res[:10]], 'or': [r[3] for r in res[:10]],
                          'pts': c.get('points'),
                          # every relevant match in 365 days, newest first: [opponent rank, at home, scored, conceded]
                          'm': [[r[3], int(r[4]), r[1], r[2]] for r in res]})
    # every appearance in the window, from the bench too: minutes and match ratings (API player stats), goals,
    # penalties and assists (match events). For the player card and the scout, not for the ratings.
    apps = defaultdict(lambda: [0, 0, 0.0, 0, 0, 0, 0])   # [appearances, minutes, rating sum, rated, goals, penalties, assists]
    # the same by month (0 = the last 30 days ... 11), so the scout can speak about any recent stretch:
    # goals, assists, starts, clean sheets as goalkeeper
    month = lambda d: min(11, max(0, int((TODAY - dt.date.fromisoformat(d)).days / 30.44)))
    mo = defaultdict(lambda: [[0] * 12 for _ in range(4)])
    for fid, f in fx.items():
        if f['s'] not in FINISHED or not (SINCE.isoformat() <= f['d'] <= TODAY.isoformat()):
            continue
        for pid, v in (pst.get(fid) or {}).items():
            a = apps[int(pid)]; a[0] += 1; a[1] += v[0] or 0
            if v[1] and 3 <= v[1] <= 10: a[2] += v[1]; a[3] += 1   # 0 means no rating
        for pid, t in bench_in.get(fid, []):
            apps[pid][0] += 1   # off the bench in a match without player stats (minutes unknown)
        k = month(f['d'])
        for e in evs.get(fid) or []:
            if e[3] != 'o' and e[1]:
                mo[e[1]][0][k] += 1
            if e[3] != 'o' and e[2]:
                mo[e[2]][1][k] += 1
            if e[3] != 'o' and e[1]:
                apps[e[1]][4] += 1
                if e[3] == 'p': apps[e[1]][5] += 1
            if e[3] != 'o' and e[2]:
                apps[e[2]][6] += 1
    late = Counter()   # goals and assists from the 76th minute on
    for fid, f in fx.items():
        if f['s'] not in FINISHED or not (SINCE.isoformat() <= f['d'] <= TODAY.isoformat()):
            continue
        for e in evs.get(fid) or []:
            if e[3] != 'o' and (e[4] or 0) >= 76:
                if e[1]: late[e[1]] += 1
                if e[2]: late[e[2]] += 1
    # for the scout: every match he played at least a minute in (in the starting XI, with or without positions; from the
    # bench by the API's minutes; or seen in a goal), and only matches whose goals and assists we have
    tname = lambda t: (top[t]['name'] if t in top else (extra.get(str(t)) or {}).get('name'))
    played = defaultdict(dict)   # pid -> fid -> [date, team, started]
    for fid, f in window:
        # only matches whose goals we have in full (the API sends no events for some): otherwise who scored is unknown
        if fid not in evs or len(evs[fid]) < (f['hg'] or 0) + (f['ag'] or 0):
            continue
        for side in (f['h'], f['a']):
            xi = (lu.get(fid) or {}).get(str(side))
            for e in (xi.get('xi') or []) if isinstance(xi, dict) else []:
                played[e[0]][fid] = [f['d'], side, True]
        for pid, v in (pst.get(fid) or {}).items():
            if (v[0] or 0) > 0 and v[2] in (f['h'], f['a']):
                played[int(pid)].setdefault(fid, [f['d'], v[2], False])
        for pid, t in bench_in.get(fid, []):
            played[pid].setdefault(fid, [f['d'], t, False])
        for e in evs[fid]:
            if e[3] != 'o' and e[0] in (f['h'], f['a']):
                for q in (e[1], e[2]):
                    if q: played[q].setdefault(fid, [f['d'], e[0], False])
    # for the scout: each club's matches with a known line-up, newest first, and who started them
    team_xi = defaultdict(list)   # team -> [(date, set of starters)]
    for fid, f in window:
        for side in (f['h'], f['a']):
            xi = (lu.get(fid) or {}).get(str(side))
            if isinstance(xi, dict) and xi.get('xi'):
                team_xi[side].append((f['d'], {e[0] for e in xi['xi']}))
    for v in team_xi.values():
        v.sort(key=lambda x: x[0], reverse=True)
    def start_run(pid, tid):
        """How many of his club's last matches in a row he started (0 if he missed the latest)."""
        n = 0
        for _, xi in team_xi.get(tid, []):
            if pid not in xi:
                break
            n += 1
        return n
    def game(pid, fid, gk):
        d, side, started = played[pid][fid]; f = fx[fid]
        g = sum(1 for e in evs[fid] if e[3] != 'o' and e[1] == pid)
        a = sum(1 for e in evs[fid] if e[3] != 'o' and e[2] == pid)
        conceded = (f['ag'] if side == f['h'] else f['hg']) if started and gk else None   # clean sheets: starting goalkeepers only
        return d, side, started, g, a, conceded
    def history(pid):
        return sorted(played.get(pid, {}), key=lambda fid: played[pid][fid][0], reverse=True)
    def last_games(pid, gk, n=100):
        """His last n games, newest first, one number each: goals | assists << 3 | clean sheet in goal << 6 | started << 7."""
        out = []
        for fid in history(pid)[:n]:
            d, side, started, g, a, conceded = game(pid, fid, gk)
            out.append(min(7, g) | min(7, a) << 3 | (1 if conceded == 0 else 0) << 6 | (1 if started else 0) << 7)
        return out
    recent = (TODAY - dt.timedelta(days=10)).isoformat()
    def last_match(pid, gk):
        h = history(pid)
        if not h or played[pid][h[0]][0] < recent:
            return None
        d, side, started, g, a, conceded = game(pid, h[0], gk); f = fx[h[0]]; home = side == f['h']
        opp = f['a'] if home else f['h']
        name = tname(opp) or (f.get('an') if home else f.get('hn'))
        # [date, opponent, goals, assists, conceded as the starting goalkeeper (None otherwise), at home]
        return [d, fix_text(name), g, a, conceded, int(home)] if name else None
    # a nationality the API does not know, from the national team he started for
    nt_nat = defaultdict(Counter)
    for fid, f in window:
        for side in (f['h'], f['a']):
            L = (lu.get(fid) or {}).get(str(side))
            if side in national and isinstance(L, dict) and (extra.get(str(side)) or {}).get('name'):
                for e in L.get('xi') or []:
                    nt_nat[e[0]][extra[str(side)]['name']] += 1
    for pid, cnt in nt_nat.items():
        e = people.get(str(pid))
        if e is not None and not e.get('nat'):
            e['nat'] = cnt.most_common(1)[0][0]
    out_players, seen = [], set()
    for tid, c in sorted(top.items(), key=lambda x: int(x[1]['rank'])):
        for pid in (squads.get(str(tid)) or {}).get('p', []):
            if pid in seen:
                continue
            st = sorted(starts.get(pid, []), reverse=True)
            if len(st) < MIN_STARTS or not (people.get(str(pid)) or {}).get('nat'):   # a few starts, and a known nationality
                continue
            # positions only from starts in a line-up with a formation: a player known only from line-ups without one
            # (just goalkeeper, defender, midfielder or forward) is not offered; those starts still count for the others
            raw = Counter(s[3] for s in st if s[3] and not s[9])
            if not raw:
                continue
            # each start weighs by how recent it is (half as much six months back) and half as much in a friendly,
            # so his positions follow where he plays now, not where he was tried in pre-season
            cnt = Counter()
            for s in st:
                if s[3] and not s[9]:
                    cnt[s[3]] += start_weight(s[0], fx.get(s[4]) or {})
            seen.add(pid)
            # offered positions: every position he started in, most frequent first;
            # on a tie, the one he played most recently first (starts are newest first)
            last = {}
            for i, s in enumerate(st):
                last.setdefault(s[3], i)
            # on a tie, the one in the line the API most often lists him in (G, D, M, F), then the most recent
            pos = [k for k, _ in sorted(cnt.items(), key=lambda kv: (-kv[1], -letters[pid][LETTER.get(kv[0], '')], last[kv[0]]))]
            pos = pos or ['CMF']
            # a single start far from where he always plays (a striker at left-back, a centre-back up front) is
            # almost always a line-up the API got wrong, or a friendly experiment: with at least 3 starts in his
            # main position, a position two bands away (defence / midfield / attack) seen only once is left out
            main, tot_w = pos[0], sum(cnt.values()) or 1
            if raw[main] >= 3:
                pos = [k for k in pos if k == main or raw[k] > 1 or not (abs(BAND.get(k, 2) - BAND.get(main, 2)) >= 2 or 'GK' in (k, main))]
            # a second position needs at least 2 starts there, or a tenth of his (weighted) starts: one start among many
            # says little (and is often a line-up the API got wrong)
            pos = [k for k in pos if k == main or raw[k] >= 2 or cnt[k] / tot_w >= 0.10]
            # goals and assists per start (own goals and missed penalties do not count)
            g = a = 0
            m = []
            for s in st:
                gs = sum(1 for ev in evs.get(s[4]) or [] if ev[3] != 'o' and ev[1] == pid)
                as_ = sum(1 for ev in evs.get(s[4]) or [] if ev[3] != 'o' and ev[2] == pid)
                g += gs; a += as_
                # [opponent rank, at home, scored, conceded, his goals + assists, started in a defensive position,
                #  rank of the club he played for (0 = not on the UEFA list)]
                m.append([s[5], int(s[6]), s[1], s[2], gs + as_, int(s[3] in DEF_POS), s[7]])
            # clean sheets that count for him: only matches he started in a defensive position
            cs = sum(1 for s in st[:10] if s[2] == 0 and s[3] in DEF_POS)
            e = people.get(str(pid), {})
            out_players.append({'id': pid, 'name': fix_text(display_name(e, pid)), 'nat': e.get('nat'), 'age': e.get('age'),
                                'club': tid, 'prim': pos[0], 'pos': pos, 'pc': [round(cnt[k], 2) for k in pos] if cnt else [1], 'starts': len(st), 'g': g, 'a': a, 'cs': cs,
                                'sc': [s[1] for s in st[:10]], 'co': [s[2] for s in st[:10]],
                                'or': [s[5] for s in st[:10]], 'm': m,
                                # every team he started for in the window (clubs and national teams): for chemistry
                                'tm': sorted({s[8] for s in st}),
                                # the card's numbers over the whole window, bench appearances included
                                'ap': max(apps[pid][0], len(st)), 'mn': apps[pid][1], 'ga': apps[pid][4], 'pk': apps[pid][5], 'aa': apps[pid][6],
                                'rt': round(apps[pid][2] / apps[pid][3], 2) if apps[pid][3] else None,
                                'csy': sum(1 for s in st if s[2] == 0 and s[3] in DEF_POS),        # clean sheets in defensive starts
                                'csg': sum(1 for s in st if s[2] == 0 and s[3] == 'GK'),           # ... as goalkeeper
                                'nt': sum(1 for s in st if s[8] in national),                      # starts for his national team
                                'wdl': [sum(1 for s in st if s[1] > s[2]), sum(1 for s in st if s[1] == s[2]), sum(1 for s in st if s[1] < s[2])],
                                'mo': monthly(mo[pid], st, month),
                                'lm': last_match(pid, pos[0] == 'GK'), 'lg': last_games(pid, pos[0] == 'GK'),
                                'lgd': played[pid][history(pid)[0]][0] if played.get(pid) else None,   # date of his last game
                                'sr': start_run(pid, tid),   # his club's last matches in a row he started
                                # his usual side, from every start: the game puts the left one of two CBs (or DMFs, CFs) on the left
                                'lat': round(sum(lats[pid]) / len(lats[pid]), 2) if lats.get(pid) else None,
                                '_raw': card_raw(pid, st, apps[pid], late[pid], pst, national)})
    card_grades(out_players)
    # when goals fall (share per minute of the 90) and how many are own goals: for the match reports in the game
    mins, own, total = [0] * 90, 0, 0
    for fid, f in fx.items():
        if f['s'] not in FINISHED or not (SINCE.isoformat() <= f['d'] <= TODAY.isoformat()):
            continue
        for e in evs.get(fid) or []:
            total += 1
            if e[3] == 'o': own += 1
            mins[min(90, max(1, e[4] or 1)) - 1] += 1
    # names of the other teams players started for (national teams and clubs off the UEFA list)
    used = {t for p in out_players for t in p['tm']} - set(top)
    others = {str(t): [extra[str(t)].get('name'), extra[str(t)].get('kind')] for t in used if str(t) in extra}
    return {'updated': TODAY.isoformat(), 'source': 'API-Football', 'ranked': len(clubs), 'clubs': out_clubs, 'players': out_players, 'teams': others,
            'goal_shape': {'min': mins, 'own': round(own / total, 4) if total else 0.026}}


def position_check(world):
    """A few players per club with their positions, to confirm left and right are not mirrored."""
    lines = ['Check that full-backs and wingers sit on the right side.',
             f'GRID_COL1_IS_RIGHT={int(GRID_COL1_IS_RIGHT)}. If left and right are swapped, flip it.', '']
    for p in world['players']:
        if p['prim'] in ('LB', 'RB', 'LWF', 'RWF', 'LMF', 'RMF'):
            lines.append(f"{p['prim']:<4} {p['name']} ({', '.join(p['pos'])})")
        if len(lines) > 60:
            break
    with open(os.path.join(CACHE_DIR, 'position_check.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


CAREER_MIN_CLUBS = 3


def short_club(name):
    return re.sub(r'\s+(FC|CF|SC|AC|FK|SK|NK|AFC|CFC|U19|U21|U23| II)$', '', (name or '').strip()) or name


def game_kits():
    """The clubs' home colours as the game has them (CLUB_KITS and CLUB_ALIAS in index.html), so both games agree."""
    try:
        src = open(os.path.join(ROOT, 'index.html'), encoding='utf-8').read()
    except OSError:
        return {}, {}
    body = lambda name: (re.search(r'const ' + name + r' = \{(.*?)\};', src, re.S) or [None, ''])[1]
    kits = {m.group(2): [m.group(3), m.group(4)] for m in re.finditer(r'([\'"])(.+?)\1\s*:\s*\[\s*\'(#[0-9A-Fa-f]{6})\'\s*,\s*\'(#[0-9A-Fa-f]{6})\'\s*\]', body('CLUB_KITS'))}
    alias = {m.group(2): m.group(4) for m in re.finditer(r'([\'"])(.+?)\1\s*:\s*([\'"])(.+?)\3', body('CLUB_ALIAS'))}
    return kits, alias


def same_club(a, b):
    """Two spellings of one club: equal once accents, 'FC'-type words and the München/Munich kind of variants go."""
    def key(x):
        k = norm(x).replace('munich', 'munchen').replace('milano', 'milan').replace('torino', 'turin').replace('lisboa', 'lisbon')
        return ' '.join(w for w in k.split() if w not in ('fc', 'cf', 'sc', 'ac', 'fk', 'sk', 'nk', 'afc', 'club', 'de', 'the'))
    ka, kb = key(a), key(b)
    return ka == kb or (min(len(ka), len(kb)) >= 5 and difflib.SequenceMatcher(None, ka, kb).ratio() >= .88)


def write_careers(world, careers, teams):
    """careers.json for the guess-who game: every player in the game whose transfer history gives at least 3 clubs,
    as stints [club, first year, last year, loan] oldest first (the first club from the club he first left; the current
    club last, to today), with his name, nationality, main position, age and overall (for difficulty and the wrong
    answers). Youth clubs before his first transfer are not in the API, so short histories are left out."""
    club_name = {c['id']: c['name'] for c in world['clubs']}
    club_rank = {c['id']: c['rank'] for c in world['clubs']}
    rank_of_name = {c['name']: c['rank'] for c in world['clubs']}
    kits, alias = game_kits()
    out, used_kits = [], {}
    for p in world['players']:
        mv = careers.get(str(p['id']))
        if not mv:
            continue
        st = []
        who = set(norm(p['name']).split())
        def add(cid, cname, y0, y1, loan):
            nm = club_name.get(cid) or short_club(cname)
            if not nm or (who and set(norm(nm).split()) <= who):   # the API sometimes puts the player's own name as a club
                return
            # the same club under two spellings ('Bayern Munich', 'Bayern München') or a move straight back: one stint
            if st and same_club(st[-1][0], nm) and bool(loan) == bool(st[-1][3]):
                st[-1][2] = y1
                if club_name.get(cid): st[-1][0] = nm
                return
            st.append([nm, y0, y1, 1 if loan else 0])
        first = mv[0]
        add(first[1], first[2], None, int(first[0][:4]), False)
        for i, t in enumerate(mv):
            y0 = int(t[0][:4]); y1 = int(mv[i + 1][0][:4]) if i + 1 < len(mv) else TODAY.year
            add(t[3], t[4], y0, y1, 'loan' in (t[5] or '').lower() and 'back' not in (t[5] or '').lower())
        cur = club_name.get(p['club'])
        if cur and st and st[-1][0] != cur:
            st.append([cur, st[-1][2], TODAY.year, 0])
        if len({s[0] for s in st}) < CAREER_MIN_CLUBS:
            continue
        # how well known he is: years at big clubs (by UEFA rank, the top 120 counting, the higher the more), plus his
        # national team; the quiz starts with the best known
        fame = 0.0
        for nm, y0, y1, loan in st:
            r = rank_of_name.get(nm)
            if r and r <= 120:
                fame += (1 - (r - 1) / 120) ** 2 * min(6, max(1, (y1 or TODAY.year) - (y0 or (y1 or TODAY.year) - 1)))
        fame += min(10, p.get('nt') or 0) * 0.4 + (p.get('ovr') or 60) / 25
        for nm, *_ in st:
            k = kits.get(nm) or kits.get(alias.get(nm, ''))
            if k: used_kits[nm] = k
        out.append({'id': p['id'], 'n': p['name'], 'nat': p.get('nat'), 'pos': p['prim'], 'age': p.get('age'), 'ovr': p.get('ovr'), 'f': round(fame, 2), 'c': st})
    with open(os.path.join(ROOT, 'careers.json'), 'w', encoding='utf-8') as f:
        json.dump({'updated': TODAY.isoformat(), 'kits': used_kits, 'players': out}, f, ensure_ascii=False, separators=(',', ':'))
    log(f'  careers.json: {len(out)} players')


WEEKLY_CLUBS = 22


def weekly_challenge(world, fx, prev=None, allow_new=True, top_pts=None, national=frozenset()):
    """The weekly challenge, the same for everyone from Monday to Sunday (UTC): the 22 most successful clubs of the 7 days
    before that Monday, at most one per country and only clubs with 11 players to draft. Success, per competitive match:
    the points won (3, 1, 0) plus three times how much better the result was than the clubs' ratings before kick-off
    expected (Elo, with home advantage): two wins count, and a win over a stronger side counts more than one over a weaker
    side, so neither the giants nor a single upset fill the list. Ties: goal difference, goals, UEFA rank. Split by UEFA rank into 11 pots of 2: the club each pot shows first and the
    order of the pots are drawn once for the week, so everyone gets the same draft; Redraw shows the pot's other club.
    Once made, a week's challenge is kept as it is (later data cannot change it mid-week)."""
    week = TODAY - dt.timedelta(days=TODAY.weekday())
    wid = week.isoformat()
    if prev and prev.get('week') == wid and len(prev.get('pots') or []) == 11:
        return prev
    if not allow_new:
        return prev
    lo, hi = (week - dt.timedelta(days=7)).isoformat(), (week - dt.timedelta(days=1)).isoformat()
    per = Counter(p['club'] for p in world['players'])
    clubs = {c['id']: c for c in world['clubs'] if per[c['id']] >= 11}
    rec = {tid: [0.0, 0, 0, 0] for tid in clubs}   # success, goal difference, goals, matches
    pre = model.run_elo(fx, top_pts, hi, national, (week - dt.timedelta(days=372)).isoformat())[1] if top_pts else {}
    for fid, f in fx.items():
        if f['s'] not in FINISHED or f['hg'] is None or not (lo <= f['d'] <= hi) or 'riendl' in (f.get('lg') or ''):
            continue
        rh, ra = pre.get(fid, (1500.0, 1500.0))
        eh = 1 / (10 ** (-(rh + model.HFA_ELO - ra) / 400) + 1)
        for t, gf, ga, ex in ((f['h'], f['hg'], f['ag'], eh), (f['a'], f['ag'], f['hg'], 1 - eh)):
            if t in rec:
                res = 1.0 if gf > ga else 0.5 if gf == ga else 0.0
                rec[t][0] += (3 if gf > ga else 1 if gf == ga else 0) + 3 * (res - ex); rec[t][1] += gf - ga; rec[t][2] += gf; rec[t][3] += 1
    order = sorted((t for t in clubs if rec[t][3]), key=lambda t: (-rec[t][0], -rec[t][1], -rec[t][2], clubs[t]['rank']))
    order += sorted((t for t in clubs if not rec[t][3]), key=lambda t: clubs[t]['rank'])
    chosen, ctry = [], set()
    for t in order:
        if clubs[t]['ctry'] in ctry:
            continue
        chosen.append(t); ctry.add(clubs[t]['ctry'])
        if len(chosen) == WEEKLY_CLUBS:
            break
    if len(chosen) < WEEKLY_CLUBS:
        return None
    chosen.sort(key=lambda t: clubs[t]['rank'])
    pots = [chosen[2 * i:2 * i + 2] for i in range(11)]
    rnd = random.Random('ttny-weekly-' + wid)
    pot_order = list(range(11)); rnd.shuffle(pot_order)
    return {'week': wid, 'from': lo, 'to': hi, 'pots': pots, 'order': pot_order, 'first': [rnd.randint(0, 1) for _ in range(11)],
            'score': {str(t): round(rec[t][0], 2) for t in chosen}}


def main():
    if not KEY:
        sys.exit('Set API_FOOTBALL_KEY first.')
    meta = load('meta', {})
    clubs = read_clubs(meta)
    teams = load('teams', {})
    fx = load('fixtures', {})
    lu = load('lineups', {})
    pst = load('pstats', {})
    evs = load('events', {})
    subs = load('subs', {})
    squads = load('squads', {})
    people = load('people', {})
    extra = load('extra_teams', {})   # national teams and non-ranked clubs players came from
    careers = load('careers', {})     # every player's transfer history (guess-who)
    stopped = None
    core_done = False
    top_ids = []
    try:
        log('1/6 Matching clubs to API teams')
        resolve_teams(clubs, teams, meta)
        top = {teams[tkey(c)]['id'] for c in clubs if teams.get(tkey(c), {}).get('id')}
        top_ids = [teams[tkey(c)]['id'] for c in clubs if teams.get(tkey(c), {}).get('id')]
        log('2/6 Season fixtures (first run only)')
        fetch_team_seasons(top_ids, fx, meta)
        log('3/6 Latest results')
        fetch_recent_days(top | {int(k) for k in extra}, fx, meta)
        log('4/6 Line-ups')
        fetch_lineups(top, fx, lu, evs, pst, subs=subs)
        core_done = True
        log('5/6 Rosters')
        fetch_squads(top_ids, squads, people)
        log('6/6 Nationality and age')
        fetch_profiles(top_ids, squads, people, meta)
        fetch_missing_nat(squads, people, lu, fx)
        log('7/7 National teams and the clubs players came from')
        fetch_nations(people, squads, extra, meta)
        fetch_arrivals(top_ids, extra, meta, squads, careers)
        ext_ids = [int(k) for k in extra]
        keep = top | set(ext_ids)   # drop matches of clubs no longer followed (the first version took too many)
        for fid in [k for k, f in fx.items() if f['h'] not in keep and f['a'] not in keep]:
            fx.pop(fid, None); lu.pop(fid, None); evs.pop(fid, None); pst.pop(fid, None); subs.pop(fid, None)
        fetch_team_seasons(ext_ids, fx, meta, tolerant=True)
        nat_ids = {int(k) for k, v in extra.items() if v.get('kind') == 'nat'}
        until = {int(k): (v.get('from') or SINCE.isoformat(), v['until']) for k, v in extra.items() if v.get('kind') == 'club' and v.get('until')}
        wanted.base = top | nat_ids   # ranked clubs and national teams: every match in the window
        fetch_lineups(top | set(ext_ids), fx, lu, evs, pst, first=nat_ids, until=until, subs=subs, backfill=True)
    except OutOfBudget:
        stopped = f'Stopped after {used} calls (MAX_REQUESTS={BUDGET}). The next run continues from here.'
        log(stopped)
    except Exception as e:   # a network or API failure after results and line-ups are in: build with what we have
        if not core_done:
            raise
        stopped = f'Stopped by an error after {used} calls: {e!r}. The next run continues from here.'
        log(stopped)
    finally:
        cutoff = (TODAY - dt.timedelta(days=400)).isoformat()
        for fid in [k for k, f in fx.items() if f['d'] < cutoff]:
            fx.pop(fid, None)
            lu.pop(fid, None)
            evs.pop(fid, None)
            pst.pop(fid, None)
            subs.pop(fid, None)
        for name, obj in (('careers', careers), ('teams', teams), ('fixtures', fx), ('lineups', lu), ('events', evs), ('pstats', pst), ('subs', subs), ('squads', squads), ('people', people), ('meta', meta), ('extra_teams', extra)):
            save(name, obj)
    world = build_world(clubs, teams, fx, lu, squads, people, evs, pst, load('extra_teams', {}), subs)
    world['complete'] = stopped is None
    # the rating model (pipeline/model.py): club Elo, the goals model, player overalls, credibility tests
    top_map = {t['id']: c for c in clubs for t in [teams.get(tkey(c)) or {}] if t.get('id')}
    national = {int(k) for k, v in extra.items() if v.get('kind') == 'nat'}
    report = model.build(world, fx, lu, evs, pst, top_map, SINCE.isoformat(), TODAY.isoformat(), log=log, national=national)
    with open(os.path.join(CACHE_DIR, 'model_report.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    failed = [k for k, v in report['pass'].items() if not v]
    if failed:
        log(f"  CREDIBILITY TESTS FAILED: {', '.join(failed)}. world.json is kept as it was; see data/cache/model_report.json")
    # the game reads world.json: publish only once results, line-ups and every roster are in, and the tests pass
    have_rosters = all(str(t) in squads for t in top_ids)
    ready = core_done and have_rosters and world['players'] and not failed
    target = OUT if ready else OUT.replace('world.json', 'world.partial.json')
    # the difficulty lift is calibrated once a day by draft bots (calibrate.yml): keep the last one
    prev_world = {}
    try:
        with open(OUT, encoding='utf-8') as f:
            prev_world = json.load(f)
        old = prev_world.get('model') or {}
        for k in ('lift', 'lift_log'):
            if old.get(k): world.setdefault('model', {})[k] = old[k]
    except (OSError, ValueError):
        pass
    # a new week's challenge only from a run that fetched everything (last week's results complete); until then the
    # old one stays in the data and the game shows none for the new week
    world['weekly'] = weekly_challenge(world, fx, prev_world.get('weekly'), allow_new=stopped is None and core_done,
                                       top_pts={tid: (c.get('points') or 0.5) for tid, c in top_map.items()}, national=national)
    # the game never reads every player's and club's match-by-match list (the model is done with them): left out of the
    # file, which makes it about 40% smaller to download and to read on a phone
    for p in world['players']:
        p.pop('m', None); p.pop('or', None)
    for c in world['clubs']:
        c.pop('m', None); c.pop('or', None)
    with open(target, 'w', encoding='utf-8') as f:
        json.dump(world, f, ensure_ascii=False, separators=(',', ':'))
    log(f'Wrote {os.path.basename(target)}')
    position_check(world)
    try:
        write_careers(world, careers, teams)
    except Exception as e:   # the guess-who file never stops the main game's data
        log(f'  careers.json not written: {e}')
    eligible_clubs = Counter(p['club'] for p in world['players'])
    log(f"world.json: {len(world['clubs'])} clubs, {len(world['players'])} rated players, "
        f"{sum(1 for v in eligible_clubs.values() if v >= 11)} clubs with 11+ rated players, {used} calls used")


if __name__ == '__main__':
    main()
