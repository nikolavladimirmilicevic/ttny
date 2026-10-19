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

**The draft.** The pots are revealed one at a time in random order; from each you get one club and pick one player. A run never brings two clubs from the same country; a player you kept shows greyed out when his club comes up. You pick on name, nationality and positions only: no ratings during the draft. The list is sorted by main position, then second and third position, then name, and each position is coloured: main (green), also plays (yellow), rarely plays (red). Three helps, each once per run: **Redraw** (another club from the same pot), **Fifty-fifty** (rules out the weaker half) and **Scout's tip** (one standout player: at least 2 goals, assists or clean sheets in as few of his last 2, 5, 10, 20, 50 or 100 games as possible, his club's last matches in a row he started, or what he did in a match in the past seven days).

On a phone a small pitch in the top bar shows each pick dropping into his place. **The shape.** The draft starts in 4-2-2-2 and keeps that shape while it still works; when it stops working, another one that works is picked at random. Players are placed in the best positions automatically. At the end you choose the shape (all 14 PES 2021 formations, alphabetical) and confirm the XI with a second tap.

**The season** follows the real formats: a 36-club league phase (eight matches in the Champions and Europa League, six in the Conference League), the play-off for places 9 to 24, then two-legged knockouts and a one-match final. You see the draw, the table after every matchday, the bracket, odds for your next match (only the next one: later matches show none yet) and every result, each match played out live with its goals (a shootout kick by kick, with who steps up), labels such as hat-trick, comeback, clean sheet or biggest win of the run, and a new knockout opponent drawn on a spinning wheel. The final gets its own gold card under floodlights. Every upcoming match names the opponent's three strongest players (no ratings). Each run opens with a short title card: the run, the competition, who stays. The run's summary shows your road through the competition, tells the run in one sentence and names the player of the run (goals, assists, player-of-the-match awards). After the draft your grades turn over one by one, the best last. Clubs carry a small shield in their colours with their initials (no real badges). Sound is off by default; the speaker in the top bar turns on crowd, whistle and applause, made in the browser.

**Keeping players.** The further you go, the more you keep: league phase 5, play-off 6, Round of 16 7, quarter-final 8, semi-final 9, final 10, title 11. A title moves the challenge up a competition; missing the top 24 moves it down, and in the Conference League it ends the challenge. A player who leaves never comes back.

**Clocks.** 60 seconds per draft pick, 2 minutes to confirm the shape, 60 seconds per step of the season, 2 minutes to choose who stays, 10 minutes to start the next run. When a clock runs out the game decides: a random player, a random shape, the next step, nobody kept, the end of the challenge. Away from the game (phone asleep, tab closed), every clock that ran out meanwhile is played out in order on return, so after a few minutes you land where the game would be by now and after a few hours the challenge is over.

**After each run and after the challenge** you get a summary screen (with your XI, or the best XI of the challenge, on the pitch as in the image) and an image to share or save: the XI on the pitch with grades, goals and assists, the captain, the difficulty and the run's numbers; for the challenge, trophies per competition, the best XI of the challenge (most runs, then Champions League, Europa League and Conference League titles, goals, assists, clean sheets for keepers, then who was drafted earlier), every run, top scorers, assists and the players who stayed longest. The captain is the player drafted earliest; in the best XI of the challenge, the one with the most runs, then the one drafted earliest.

**What you see of ratings.** Your players get a grade from A to E and a card with 14 more grades (finishing, creativity, big games, consistency and so on). Your team's rating is the average of your 11 (a little lower for anyone away from his main position; shown as level, performance, experience and form) plus a bonus for chemistry, shown from 70 (none) to 99 (full): 2 per player who played with him in the last year and 1 per compatriot, at most 3 per player (same league counted too until measured on national teams: only team-mates win matches). Opponents show no rating at all. A player you keep wears a small ×2, ×3 for his runs with you.

**Achievements** sit under the trophy cabinet: first title, hat-trick, unbeaten run, a title on Hard, all three competitions won, a challenge played through all ten runs.

## How the data works

`pipeline/update.py` builds `world.json`. It only ever uses the last 365 days; the UEFA 5-year points are the one exception.

- **Clubs:** every club on the UEFA 5-year club ranking, read weekly from the service behind uefa.com (fallback: the last good list, then `pipeline/clubs.csv`). Clubs are matched to API-Football among the teams that played in UEFA competitions in the last six seasons (same country, closest name), then by the API's name search; fix a wrong or missing match in `pipeline/team_ids.csv`.
- **Players:** every current member of those clubs with at least one start in the window and a known nationality (from the API, else from the national team he started for). Positions come only from line-ups with a formation, so a player seen only in line-ups without one (many smaller leagues) is not offered; for everyone else those starts still count, in his usual position.
- **Matches:** all their matches in the window: their clubs, their national teams, friendlies, and the clubs they played for before a transfer (only for the stretch they were there). Line-ups, goals, assists, substitutions, minutes and match ratings come with them. Where the API has no player statistics for a match (many smaller leagues), appearances from the bench come from the substitutions.

Everything fetched is cached in `data/cache/`, so each run only asks for what is new. `world.json` is published only when results, line-ups and rosters are complete and all nine credibility tests pass; otherwise the run writes `world.partial.json` and the game keeps the last good data.

### The model (`pipeline/model.py`)

- **Club strength:** Elo over every match in the window (K 20, friendlies at half weight, home advantage 60), seeded from UEFA points and blended 80/20 with that seed. Clubs off the UEFA list and national teams start from a rough guess, so for their first 20 matches they move three times as fast (K 60).
- **Goals:** a Poisson model with a draw correction, fitted on matches between ranked clubs.
- **Player strength:** learned from results, on the clubs' Elo scale. In every match with both line-ups a side's strength is the mean of its 11 starters plus home advantage, and after the match all 22 move toward the result (K 24, 60 for a player's first 15 matches, friendlies at half weight). A player starts from his club's rating when he first appears. On top come his match ratings against his team-mates' in the same matches. Held out on the last 60 days, this predicts results about as well as club Elo for clubs and much better for national teams, whose XIs come from many clubs, like a draft. The overall (45-97, A from 82) is that strength on a straight line, each line centred on the same mean. The card's level, performance, experience and form describe the player; they are not a formula.
- **Positions:** from every start in the window. Away from his main position a player loses 1 point where he plays often and 2 where he rarely plays.
- **Your XI:** the mean of your 11 (a little lower away from a player's main position) on the same scale as the clubs, plus chemistry, plus one lift per competition for strong XIs. A draft bot calibrates that lift every day (`pipeline/tools/calibrate.js`, workflow *Calibrate difficulty*) so a sharp drafter wins about 13% / 10% / 8% of titles on Easy / Medium / Hard.
- **Credibility tests:** the big clubs on top, the best players from the best clubs, realistic title odds per competition, no position overrepresented among the top 300, and more. Results go to `data/cache/model_report.json`.

## Setup

1. Get an API key at [dashboard.api-football.com](https://dashboard.api-football.com).
2. **Settings → Secrets and variables → Actions:** secret `API_FOOTBALL_KEY`; variable `MAX_REQUESTS` (`90` on the free plan, `7000` on Pro).
3. **Actions → Update data → Run workflow** for the first load. It then runs five times a day (05:17, 08:47, 11:17, 17:17 and 21:47 UTC; GitHub sometimes delays or skips a scheduled run, so the others are the backup) and rebases onto `main` before pushing, so a code push during a run is safe.
4. **Settings → Pages:** deploy from `main`, root folder.

After the first full run, check `data/cache/teams.json` (every club matched to the right team) and `data/cache/position_check.txt` (if left-backs show up as RB, set the variable `GRID_COL1_IS_RIGHT` to `1`, delete `data/cache/lineups.json` and run again).

**Updating the game:** run `python3 pipeline/tools/stamp.py` before committing a change to `index.html`. It writes a new build id into the page and into `version.json`; an open copy of the game checks `version.json` every minute and reloads itself (never in the middle of a live match), and the run carries on.

## Run locally

```
API_FOOTBALL_KEY=... MAX_REQUESTS=90 python3 pipeline/update.py
python3 -m http.server   # then open http://localhost:8000
```

Offline test against a fake API: `python3 pipeline/mock_api.py 8765` and
`API_BASE=http://127.0.0.1:8765 API_FOOTBALL_KEY=x SLEEP=0 MAX_REQUESTS=5000 python3 pipeline/update.py`.
Difficulty calibration: serve the game on port 8830 and run `node pipeline/tools/draft_bots.js 300`.
