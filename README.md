# This Time Next Year

Draft one player from each of 11 pots of clubs on the UEFA 5-year ranking, then win the Euro Cup.
A static web game (one `index.html`) served by GitHub Pages, with data refreshed daily by a GitHub Action.

## How the data works

`pipeline/update.py` calls [API-Football](https://www.api-football.com) and writes `world.json`:

- **Clubs:** every club on the UEFA 5-year club ranking, with its points, read once a week from the data service behind uefa.com. If that fails, the script keeps the last good list; with no list at all it falls back to `pipeline/clubs.csv`. Clubs are matched to API-Football by name. If one is matched wrongly or not found, add `name,api_id` to `pipeline/team_ids.csv` (the name as UEFA writes it).
- **Players:** everyone on a ranked club's roster with **10+ starts against ranked clubs in the last 365 days** (friendlies count, matches without line-up positions do not) and a known nationality. Every such start is kept with the opponent, venue, score, his goals and assists, his position and the club he played for.
- **Clubs' results:** every match against another ranked club in the same window, under the same positions rule.
- **Match model:** the base home/draw/away rates, the real scorelines behind each result and the fitted tilt `k`, all from matches between ranked clubs.

Everything fetched is cached in `data/cache/`, so each run only asks for what is new.
If no `world.json` exists, the game runs on generated demo data.

## Setup

1. Get an API key at [dashboard.api-football.com](https://dashboard.api-football.com).
2. In this repo: **Settings → Secrets and variables → Actions**
   - Secret `API_FOOTBALL_KEY` = your key
   - Variable `MAX_REQUESTS` = `90` on the free plan, `7000` on Pro
3. **Actions → Update data → Run workflow** for the first load. On the free plan the first full load takes about 10 daily runs; on Pro it finishes in one.
4. **Settings → Pages →** deploy from the `main` branch, root folder.

## After the first full run

- Open `data/cache/teams.json` and check every club was matched to the right API team. Wrong or missing ones go into `pipeline/team_ids.csv`.
- Open `data/cache/position_check.txt`. If left-backs show up as RB and right wingers as LW, set the Actions variable `GRID_COL1_IS_RIGHT` to `1`, delete `data/cache/lineups.json` and run again.

## Run locally

```
API_FOOTBALL_KEY=... MAX_REQUESTS=90 python3 pipeline/update.py
python3 -m http.server   # then open http://localhost:8000
```

Offline test against a fake API: `python3 pipeline/mock_api.py 8765` and
`API_BASE=http://127.0.0.1:8765 API_FOOTBALL_KEY=x SLEEP=0 MAX_REQUESTS=5000 python3 pipeline/update.py`.
