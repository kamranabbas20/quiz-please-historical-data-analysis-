# quiz-please-historical-data-analysis

Two things live here:

1. **A scraper** for [Quiz Please](https://quizplease.com) game results (`quizplease/`).
2. **A dashboard** for exploring a team's history game by game (`dashboard/` + `app/`).

Both are pure standard library / vanilla JS: no `requests`, no `pandas`, no
`openpyxl`, no npm install, no build step, no JS engine needed to scrape.

---

## Quick start

```bash
# 1. scrape a city (already done for Baku — data/baku/ is committed)
python3 -m quizplease --delay 0.05 city baku --workers 5
python3 -m quizplease --delay 0.05 standings baku     # all-time rating tables

# 2. compile the browser dataset
python3 -m dashboard.build

# 3. serve the app (fetch() needs http://, not file://)
python3 -m http.server 8765 --directory app
# open http://127.0.0.1:8765/
```

To deploy, publish the `app/` directory as static files — GitHub Pages, S3, any
web server. There is no backend.

---

## What the data actually is

Scoreboards go back to the start. For Baku:

| | |
|---|---|
| Games in the schedule | 285 (April 2023 → September 2026) |
| Games with a published scoreboard | 258 |
| Team-game rows | 3,954 |
| Distinct teams | 682 |
| Seasons covered | 15 |
| All-time rating standings | 766 team/league rows, up to 196 games per team |

The 27 games with no scoreboard have nothing published for them at all —
`result.table` is null and no rounds are recorded — so they are kept in the
dataset as games without results rather than silently dropped.

**Two scoreboard sources, and one parameter that matters.** `/api/games/view/{id}`
omits the `result` object unless the request asks for it:

```
GET /api/games/view/{id}?relationships[]=result
```

Without that parameter the field comes back `null` for every game, and the only
scoreboards left are the ones `/api/games/{id}/results` serves — which covers
roughly the last six months. That looks exactly like "the site only publishes
recent results", and it is wrong: with the parameter, Baku has scoreboards back
to its first game in 2023, and Moscow back to 2021. The scraper asks for the
relationship and prefers whichever source is richer:

| Source | Coverage | Team ids | Layout |
|---|---|---|---|
| `/api/games/{id}/results` | ~last 6 months | yes | stable JSON |
| `result.table` (.xlsx) | the whole back catalogue | no | drifts between seasons |

The .xlsx layout has changed over the years — rounds before or after the total,
`1 раунд` vs `Раунд 1`, per-format extra columns (a Hogwarts house), trailing
notes below the table — so the parser matches columns by name and never by
position. `results_source` on each game records which source it came from.

Two more data facts worth knowing before reading any number:

- **A team has one id per rating league** (classic vs. film-and-music), so ids
  do not identify a team across formats — and the .xlsx scoreboards carry no ids
  at all. Teams are keyed by their **name**, with any ids kept as aliases.
- **No theoretical maximum score is published.** "% от лучшего" is measured
  against the best total actually achieved in that game, so the winner is always
  100%. It answers "how close to the best team in the room", not "how close to
  perfect".

---

## The dashboard

Filters run top-down — **город → команда → формат → лига → сезон → период** — in
one row that scopes every panel below it. Changing the city reloads that city's
dataset and rebuilds the team list; changing the team rebuilds the format,
league and season options from that team's own games, each with a count, so no
filter can produce an empty view.

Four views:

- **Обзор** — KPI cards (games, average percentile, trend, wins, top-3, average
  and best position, average/median score, consistency) plus the chronological
  timeline: percentile with a 5-game rolling average, total score, finishing
  position against the size of the field, distribution of places, distribution
  of results, and average percentile per format.
- **Игры и разбор** — a sortable table of every game, and for the selected one:
  score, place, round-by-round scores against the field's average and best,
  strongest and weakest round, comparison with the team's career averages,
  position after each round, and the game's full final table.
- **Форматы** — one row per quiz format: games, average score, average place,
  win rate, top-3 rate, average percentile, consistency.
- **Сравнение команд** — up to three opponents beside the selected team, with
  head-to-head games and who finished higher.

Every chart has a hover/keyboard readout **and** a table view, so no value is
reachable only by pointing at it. The palette is validated for colour-vision
deficiency in both light and dark themes.

### Metrics

| Metric | Definition |
|---|---|
| Процентиль | share of the field finished ahead of: `(N − place) / (N − 1) × 100`; a one-team game is 100 |
| % от лучшего | `total / best total in that game × 100` |
| Динамика | least-squares slope of percentile over the game sequence, in points per game; needs 3+ games |
| Стабильность | sample σ of "% от лучшего"; undefined for a single game |
| Recent form | mean percentile over the last 5 games |

Percentile is the only measure comparable **between** formats — formats differ
in round count, so raw scores are not (a 10-round music party scores ~100, a
7-round classic ~60).

---

## Architecture

```
quizplease/          data collection — the site's public API
  api.py             /api/cities, /api/games/finished/{city}, /view/{id}, /{id}/results
  rating.py          rating-api.quizplease.com — all-time standings
  harvest.py         walk a city's back catalogue, write per-city JSON + CSV
  scraper.py         normalise a game record; also scrapes a page directly
  jsobj.py           parser for the minified window.__NUXT__ literal
  xlsx.py            minimal .xlsx reader for the published scoreboard files
  cli.py             python -m quizplease {game,city,standings,cities}

dashboard/           cleaning, normalisation, analytics
  model.py           the internal data model: Game + TeamGame, team keys, seasons
  analytics.py       per-game derived metrics (percentile, % of best, round shares)
  build.py           data/ -> app/data/*.json

app/                 visualisation and UI (static)
  js/data.js         loading, indexing, caching, filtering
  js/stats.js        statistics over a filtered slice (pure functions)
  js/charts.js       SVG chart primitives
  js/ui.js           views, filters, wiring
  css/app.css        design tokens, light + dark
```

The split that matters: **facts about a game** (its field size, its best score,
each team's percentile within it) never change with a filter, so they are
computed once in `dashboard/analytics.py` at build time. **Facts about a
selection** (averages, win rate, form, trend) depend on what the reader filtered
to, so they are computed in `app/js/stats.js` on the fly. Nothing is computed in
both places.

The app hard-codes no city, team or format: it reads `app/data/index.json`,
loads one file per city on demand, and derives every option from the data.

---

## Data schema

### Scraper output (`data/<city>/`)

| Path | Contents |
|---|---|
| `games/<game_id>.json` | one normalised game, including its scoreboard |
| `games.csv` | one row per game |
| `results.csv` | one row per team per game (long format) |
| `standings.csv` | all-time rating table rows: team, league, points, games, rank |

A game JSON:

```jsonc
{
  "id": "01a04287-…", "url": "https://baku.quizplease.com/game/…",
  "title": "[видеоигры] BAKU", "game_number": "3", "full_title": "[видеоигры] BAKU 3",
  "date": "2026-09-01T19:30:00", "status": "finished",
  "city":   { "id": 158, "name": "Баку", "slug": "baku" },
  "country":{ "id": 30, "name": "Азербайджан" },
  "place":  { "id": 1309, "title": "Paulaner Braühaus", "address": "…", "lat": …, "lon": … },
  "template": { "id": 180, "title": "[видеоигры]", "level": "medium" },
  "format": { "Тема": "видеоигры", "Сложность": "нормальная", "Формат": "7 раундов, 2 часа" },
  "league": "классика", "price": 15, "currency": "₼",
  "teams_registered": 8, "teams_came": 8, "people_registered": 56,
  "results": [
    { "place": 1, "team": "Noldor", "team_id": 1179382, "rank": "unattainable",
      "rank_title": "Недосягаемые", "total": 52,
      "rounds": { "round_1": 5, "round_2": 5, "round_7": 18 } }
  ]
}
```

### App dataset (`app/data/<city>.json`)

```jsonc
{
  "city":     { "slug": "baku", "name": "Баку", "currency": "₼" },
  "coverage": { "games_total": 285, "games_with_results": 17, "result_rows": 143,
                "teams": 53, "date_from": …, "scored_from": …, "scored_to": … },
  "filters":  { "game_types": […], "leagues": […], "seasons": […], "venues": […] },
  "games": [ { "id", "date", "season", "title", "game_number", "game_type", "league",
               "theme", "difficulty", "format", "venue", "address", "price", "currency",
               "url", "rounds": ["round_1", …], "round_labels": ["Раунд 1", …],
               "teams_count", "best_total", "worst_total", "mean_total", "has_results",
               "results_source": "api" | "xlsx" | null } ],
  "rows":  [ { "game_id", "team", "team_key", "team_ids": [ … ], "position", "total",
               "percentile", "score_pct", "gap_to_best", "gap_to_mean",
               "rank", "rank_title",
               "attributes": { "факультет": "1Gryffindor" },  // per-format extra columns
               "rounds":    [5, 5, 6.5, …],   // aligned with the game's `rounds`
               "round_pct": [100, 83, …] } ],
  "teams": [ { "key", "name", "names": [ … ], "ids": [ … ], "games", "first", "last" } ],
  "standings": [ { "team", "team_key", "league", "league_code", "position",
                   "points", "games", "rank_title" } ]
}
```

`team_key` is the team name normalised (case-folded, whitespace and trailing
punctuation stripped); `names` keeps every spelling seen, and the display name is
the most common one.

---

## Updating the dataset with new games

```bash
python3 -m quizplease --delay 0.05 city baku --workers 5   # only new games are fetched
python3 -m quizplease --delay 0.05 standings baku
python3 -m dashboard.build                                 # rewrites app/data/
```

`city` is resumable and incremental: games already on disk are skipped, so a
weekly run costs one listing request plus two or three per new game. `--refresh`
re-fetches everything; a game that fails is reported and the run continues.
`--workers N` fetches N games at once — the time is nearly all latency, so this
is the difference between 5 games a minute and 60. Keep it modest; it is
someone else's server.

The app picks the rebuilt files up on reload — no code change, no configuration.

### Adding another city

```bash
python3 -m quizplease cities --counts -o /tmp/cities.csv   # slugs and game counts
python3 -m quizplease --delay 0.15 city tbilisi
python3 -m quizplease --delay 0.15 standings tbilisi
python3 -m dashboard.build
```

The city appears in the dropdown automatically. Scraping costs two or three
requests per game, so use `--delay` and `--workers`, and expect a city the size
of Moscow (8,534 games, with scoreboards back to 2021) to take hours even so.
There are ~101,000 finished games across 244 cities;
`python3 -m quizplease cities --counts` prints the current breakdown.

---

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py'   # 52 tests: scraper, model, analytics, build
node --test tests/test_stats.mjs                       # 10 tests: dashboard statistics

# browser tests need the app running on :8765
python3 -m http.server 8765 --directory app &
node tests/browser/smoke.mjs           # every view renders, no console errors
node tests/browser/interactions.mjs    # filters, single-game teams, comparison, table views
node tests/browser/looks.mjs           # dark mode + phone width, no horizontal overflow
python3 tests/validate_against_source.py Колобки Ванси Noldor
```

The last one is the important one: it recomputes every headline figure straight
from `data/baku/games/*.json` — code that shares nothing with the app — drives
the real page in Chromium, and compares the two. `tests/test_multicity.py` builds
a throwaway two-city dataset and checks that switching city really does swap the
teams, the filters and the coverage line.

## Notes and limits

- Scores are half-points in some rounds, so totals are floats where they need to
  be and ints where they don't.
- `came_peoples` is 0 on most games — venues record team counts, not head counts.
  Use `people_registered` for attendance.
- The rating standings endpoint returns the full league table, but only page one
  reports the total; the client remembers it.
- A game with no published scoreboard is still kept, so coverage can be stated
  honestly rather than silently dropped.
- Field sizes have shrunk a lot in Baku — 30+ teams a game in 2023, 6–12 in
  2026 — so a finishing position means different things in different years.
  That is exactly why the percentile, not the place, is the comparable measure.
