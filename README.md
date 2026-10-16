# This Time Next Year

<img src="logo320.png" alt="This Time Next Year" width="200" align="right">

A European football draft game. Draft one real player from each of 11 pots of clubs on the UEFA 5-year ranking, then take your XI through a full European season. Win and keep your best players for the next run, for a challenge of up to ten runs, all in this one season.

One static page (`index.html`) plus one data file (`world.json`), refreshed every day by a GitHub Action from [API-Football](https://www.api-football.com).

**Tracker:** defects, enhancements, feedback and questions go to [Issues](https://github.com/nikolavladimirmilicevic/ttny/issues). Pick one of the four forms; the priority you choose becomes a label automatically.

## The game

**A challenge** starts with one choice, the difficulty:

| Difficulty | Starts in | Clubs you draft from |
|---|---|---|
| Easy | Champions League | the top third of the UEFA ranking |
| Medium | Europa League | the top two thirds |
| Hard | Conference League | every ranked club |

**The draft.** The pots are revealed one at a time in random order; from each you get one club and pick one player. You pick on name, nationality and positions only: no ratings during the draft. The list is sorted by main position, then second and third position, then name, and each position is coloured: main (green), also plays (yellow), rarely plays (red). Three helps, each once per run: **Redraw** (another club from the same pot), **Fifty-fifty** (rules out the weaker half) and **Scout's tip** (one standout player: at least 2 goals, assists or clean sheets in as few of his last 2, 5, 10, 20, 50 or 100 games as possible, his club's last matches in a row he started, or what he did in a match in the past seven days).

**The shape.** The draft starts in 4-2-2-2 and keeps that shape while it still works; when it stops working, another one that works is picked at random. Players are placed in the best positions automatically. At the end you choose the shape (all 14 PES 2021 formations, alphabetical) and confirm the XI with a second tap.

**The season** follows the real formats: a 36-club league phase (eight matches in the Champions and Europa League, six in the Conference League), the play-off for places 9 to 24, then two-legged knockouts and a one-match final. You see the draw, the table after every matchday, the bracket, odds for your next match and every result.

**Keeping players.** The further you go, the more you keep: league phase 5, play-off 6, Round of 16 7, quarter-final 8, semi-final 9, final 10, title 11. A title moves the challenge up a competition; missing the top 24 moves it down, and in the Conference League it ends the challenge. A player who leaves never comes back.

**Clocks.** 60 seconds per draft pick, 2 minutes to confirm the shape, 60 seconds per step of the season, 2 minutes to choose who stays, 10 minutes to start the next run. When a clock runs out the game decides: a random player, a random shape, the next step, nobody kept, the end of the challenge.

**After each run and after the challenge** you get a summary screen (with your XI, or the best XI of the challenge, on the pitch as in the image) and an image to share or save: the XI on the pitch with grades, goals and assists, the captain, the difficulty and the run's numbers; for the challenge, trophies per competition, the best XI of the challenge (most runs, then goals, assists, clean sheets for keepers, then who was drafted earlier), every run, top scorers, assists and the players who stayed longest. The captain is the player drafted earliest; in the best XI of the challenge, the one with the most runs, then the one drafted earliest.

**What you see of ratings.** Your players get a grade from A to E and a card with 14 more grades (finishing, creativity, big games, consistency and so on). Your team's rating is the average of your 11 (a little lower for anyone away from his main position; shown as level, performance, experience and form) plus a bonus for chemistry, shown from 70 (none) to 99 (full): 2 per player who played with him in the last year, 1 per compatriot, 1 per player from a club in the same league, at most 3 per player. Opponents show no rating at all.

## How the data works

`pipeline/update.py` builds `world.json`. It only ever uses the last 365 days; the UEFA 5-year points are the one exception.

- **Clubs:** every club on the UEFA 5-year club ranking, read weekly from the service behind uefa.com (fallback: the last good list, then `pipeline/clubs.csv`). Clubs are matched to API-Football among the teams that played in UEFA competitions in the last six seasons (same country, closest name), then by the API's name search; fix a wrong or missing match in `pipeline/team_ids.csv`.
- **Players:** every current member of those clubs with at least one start in the window and a known nationality (from the API, else from the national team he started for). Where a line-up comes without a formation (many smaller leagues), a start counts in the player's usual position from other line-ups, else in the API's rough one (goalkeeper, defender, midfielder, forward).
- **Matches:** all their matches in the window: their clubs, their national teams, friendlies, and the clubs they played for before a transfer (only for the stretch they were there). Line-ups, goals, assists, substitutions, minutes and match ratings come with them. Where the API has no player statistics for a match (many smaller leagues), appearances from the bench come from the substitutions.

Everything fetched is cached in `data/cache/`, so each run only asks for what is new. `world.json` is published only when results, line-ups and rosters are complete and all nine credibility tests pass; otherwise the run writes `world.partial.json` and the game keeps the last good data.

### The model (`pipeline/model.py`)

- **Club strength:** Elo over every match in the window (K 20, friendlies at half weight, home advantage 60), seeded from UEFA points and blended 80/20 with that seed. Clubs off the UEFA list and national teams start from a rough guess, so for their first 20 matches they move three times as fast (K 60).
- **Goals:** a Poisson model with a draw correction, fitted on matches between ranked clubs.
- **Player overall:** 45% the strength of his clubs, 15% playing time, 30% performance (his match ratings, adjusted for opponent strength, shrunk toward his line's average), 10% form (last 5 ratings from the last 60 days). Older matches count less (a rating counts half after 90 days), and appearances from the bench of 30+ minutes count toward performance and playing time. Performance and form count by rank within his line, so a player dominating a weak league cannot outweigh the rest. Players with few starts lean toward a squad player's score. The result is placed on a 45-97 scale (A from 82).
- **Positions:** from every start in the window. Away from his main position a player loses 1 point where he plays often and 2 where he rarely plays.
- **Your XI:** rated like a club's own eleven, plus a lift for strong XIs and the chemistry bonus; the lift is calibrated with draft bots so a sharp drafter wins about 13% / 10% / 8% of titles on Easy / Medium / Hard.
- **Credibility tests:** the big clubs on top, the best players from the best clubs, realistic title odds per competition, no position overrepresented among the top 300, and more. Results go to `data/cache/model_report.json`.

## Setup

1. Get an API key at [dashboard.api-football.com](https://dashboard.api-football.com).
2. **Settings → Secrets and variables → Actions:** secret `API_FOOTBALL_KEY`; variable `MAX_REQUESTS` (`90` on the free plan, `7000` on Pro).
3. **Actions → Update data → Run workflow** for the first load. It then runs three times a day (05:17, 11:17 and 17:17 UTC; GitHub sometimes delays or skips a scheduled run, so the later ones are the backup) and rebases onto `main` before pushing, so a code push during a run is safe.
4. **Settings → Pages:** deploy from `main`, root folder.

After the first full run, check `data/cache/teams.json` (every club matched to the right team) and `data/cache/position_check.txt` (if left-backs show up as RB, set the variable `GRID_COL1_IS_RIGHT` to `1`, delete `data/cache/lineups.json` and run again).

## Run locally

```
API_FOOTBALL_KEY=... MAX_REQUESTS=90 python3 pipeline/update.py
python3 -m http.server   # then open http://localhost:8000
```

Offline test against a fake API: `python3 pipeline/mock_api.py 8765` and
`API_BASE=http://127.0.0.1:8765 API_FOOTBALL_KEY=x SLEEP=0 MAX_REQUESTS=5000 python3 pipeline/update.py`.
Difficulty calibration: serve the game on port 8830 and run `node pipeline/tools/draft_bots.js 300`.
