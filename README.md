# This Time Next Year

Draft one player from each of 11 pots of Europe's top-100 clubs, then win the Champions League.
A static web game (one `index.html`) served by GitHub Pages, with data refreshed daily by a GitHub Action.

## How the data works

`pipeline/update.py` calls [API-Football](https://www.api-football.com) and writes `world.json`:

- **Clubs:** the top 100 of the UEFA 5-year club ranking, listed in `pipeline/clubs.csv`. Update that file when UEFA publishes a new ranking.
- **Players:** everyone on a top-100 roster with **10+ starts against top-100 clubs in the last 365 days**. A player's last 10 of those starts give his scored and conceded lists. His positions are the slots he started in, read from the line-up grid.
- **Opponents:** each club's last 10 results against other top-100 clubs.

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

- Open `data/cache/teams.json` and check every club was matched to the right API team. Wrong or missing ones: put the correct id in the `api_id` column of `clubs.csv`.
- Open `data/cache/position_check.txt`. If left-backs show up as RB and right wingers as LW, set the Actions variable `GRID_COL1_IS_RIGHT` to `0`, delete `data/cache/lineups.json` and run again.

## Run locally

```
API_FOOTBALL_KEY=... MAX_REQUESTS=90 python3 pipeline/update.py
python3 -m http.server   # then open http://localhost:8000
```

Offline test against a fake API: `python3 pipeline/mock_api.py 8765` and
`API_BASE=http://127.0.0.1:8765 API_FOOTBALL_KEY=x SLEEP=0 MAX_REQUESTS=5000 python3 pipeline/update.py`.
