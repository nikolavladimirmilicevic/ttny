#!/usr/bin/env python3
"""Tiny fake of the API-Football endpoints update.py uses, for offline testing.
Run:  python3 pipeline/mock_api.py 8765   then   API_BASE=http://127.0.0.1:8765 API_FOOTBALL_KEY=x python3 pipeline/update.py"""
import csv, datetime as dt, json, os, random, sys, urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R = random.Random(7)
TODAY = dt.date.fromisoformat(os.environ.get('TODAY', dt.date.today().isoformat()))
clubs = list(csv.DictReader(open(os.path.join(ROOT, 'pipeline', 'clubs.csv'), encoding='utf-8')))
TEAMS = {1000 + int(c['rank']): {'id': 1000 + int(c['rank']), 'name': c['name'], 'country': c['country'], 'national': False} for c in clubs}
for i in range(40):  # some clubs outside the top 100
    TEAMS[5000 + i] = {'id': 5000 + i, 'name': f'Other {i}', 'country': R.choice([c['country'] for c in clubs]), 'national': False}
NATS = ['England', 'Spain', 'France', 'Brazil', 'Argentina', 'Germany', 'Portugal', 'Netherlands', 'Serbia', 'Senegal', "Côte d'Ivoire", 'Japan']
FORMS = {'4-3-3': ['G', 'D', 'D', 'D', 'D', 'M', 'M', 'M', 'F', 'F', 'F'], '4-2-3-1': ['G', 'D', 'D', 'D', 'D', 'M', 'M', 'M', 'M', 'M', 'F'],
         '3-5-2': ['G', 'D', 'D', 'D', 'M', 'M', 'M', 'M', 'M', 'F', 'F'], '4-4-2': ['G', 'D', 'D', 'D', 'D', 'M', 'M', 'M', 'M', 'F', 'F']}
PL = {}
for tid in TEAMS:
    PL[tid] = [{'id': tid * 100 + k, 'name': f'Player {tid}-{k}', 'age': R.randint(18, 35), 'nat': R.choice(NATS)} for k in range(24)]
FX = []
fid = 1
ids = list(TEAMS)
for _ in range(4200):
    a, b = R.sample(ids, 2)
    if a >= 5000 and b >= 5000:
        continue
    d = TODAY - dt.timedelta(days=R.randint(0, 420))
    FX.append({'id': fid, 'date': d.isoformat() + 'T19:00:00+00:00', 'h': a, 'a': b, 'hg': R.choice([0, 0, 1, 1, 1, 2, 2, 3, 4]),
               'ag': R.choice([0, 0, 1, 1, 2, 2, 3]), 'fh': R.choice(list(FORMS)), 'fa': R.choice(list(FORMS))})
    fid += 1


def season_of(d):
    return d.year if d.month >= 7 else d.year - 1


def fx_item(f, with_lineups=False):
    it = {'fixture': {'id': f['id'], 'date': f['date'], 'status': {'short': 'FT' if f['date'][:10] <= TODAY.isoformat() else 'NS'}},
          'league': {'name': 'Mock League'}, 'teams': {'home': {'id': f['h'], 'name': TEAMS[f['h']]['name']}, 'away': {'id': f['a'], 'name': TEAMS[f['a']]['name']}},
          'goals': {'home': f['hg'], 'away': f['ag']}, 'score': {'fulltime': {'home': f['hg'], 'away': f['ag']}}}
    if with_lineups:
        it['lineups'] = []
        for tid, form in ((f['h'], f['fh']), (f['a'], f['fa'])):
            lines = [1] + [int(x) for x in form.split('-')]
            xi, k = [], 0
            squad = PL[tid][:16]
            for row, n in enumerate(lines, start=1):
                for col in range(1, n + 1):
                    p = squad[k]
                    xi.append({'player': {'id': p['id'], 'name': p['name'], 'pos': FORMS[form][k], 'grid': f'{row}:{col}'}})
                    k += 1
            it['lineups'].append({'team': {'id': tid}, 'formation': form, 'startXI': xi})
    return it


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        path = u.path.strip('/')
        resp, paging = [], {'current': 1, 'total': 1}
        if path == 'teams':
            s = q['search'].lower()
            resp = [{'team': t} for t in TEAMS.values() if s[:5] in t['name'].lower()]
        elif path == 'fixtures' and 'ids' in q:
            want = {int(x) for x in q['ids'].split('-')}
            resp = [fx_item(f, True) for f in FX if f['id'] in want]
        elif path == 'fixtures' and 'team' in q:
            t, s = int(q['team']), int(q['season'])
            resp = [fx_item(f) for f in FX if t in (f['h'], f['a']) and season_of(dt.date.fromisoformat(f['date'][:10])) == s]
        elif path == 'fixtures' and 'date' in q:
            resp = [fx_item(f) for f in FX if f['date'][:10] == q['date']]
        elif path == 'players/squads':
            t = int(q['team'])
            resp = [{'team': {'id': t}, 'players': [{'id': p['id'], 'name': p['name'], 'age': p['age']} for p in PL[t]]}]
        elif path == 'players':
            t, page = int(q['team']), int(q.get('page', 1))
            chunk = PL[t][(page - 1) * 20: page * 20]
            paging = {'current': page, 'total': 2}
            resp = [{'player': {'id': p['id'], 'name': p['name'], 'firstname': 'Test', 'lastname': p['name'].split(' ')[1], 'age': p['age'], 'nationality': p['nat']}} for p in chunk]
        body = json.dumps({'errors': [], 'response': resp, 'paging': paging}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(body)


if __name__ == '__main__':
    HTTPServer(('127.0.0.1', int(sys.argv[1] if len(sys.argv) > 1 else 8765)), H).serve_forever()
