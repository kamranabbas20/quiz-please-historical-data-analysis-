# quiz-please-historical-data-analysis

Scraper for [Quiz Please](https://quizplease.com) game pages, plus the scraped data.

Game pages are server-rendered Nuxt, so the entire game record — venue, price,
format, team counts, and the link to the published scoreboard — already sits in
the `window.__NUXT__` payload. This scraper reads that payload directly instead
of scraping rendered HTML, then downloads the `.xlsx` scoreboard a finished game
publishes and flattens it into per-team, per-round rows.

Pure standard library: no `requests`, no `beautifulsoup4`, no `openpyxl`, and no
JS engine. The payload is parsed, never executed.

## Usage

```bash
# scrape one game into data/games/
python -m quizplease https://baku.quizplease.com/game/01a04287-57c0-70c9-a6db-e2a8f574355e

# several at once; bare UUIDs resolve against --city
python -m quizplease --city baku 01a04287-57c0-70c9-a6db-e2a8f574355e <another-id>

# print JSON instead of writing files
python -m quizplease --stdout <url>
```

| flag | effect |
| --- | --- |
| `--city SLUG` | subdomain used when a bare game id is given (default `baku`) |
| `-o, --out-dir DIR` | where to write (default `data/games`) |
| `--stdout` | print JSON, write nothing |
| `--no-results` | skip the scoreboard download |
| `--raw` | keep the untouched game record from the payload under `raw` |

As a library:

```python
from quizplease import scrape_game

game = scrape_game("https://baku.quizplease.com/game/01a04287-57c0-70c9-a6db-e2a8f574355e")
print(game["results"][0]["team"], game["results"][0]["total"])
```

## Output

Each game produces `data/games/<id>.json` and, when the game has finished,
`data/games/<id>_results.csv`.

The JSON record carries game identity and timing (`id`, `title`, `game_number`,
`date`, `status`), location (`city`, `country`, `place` with coordinates),
commercials (`price`, `currency`, `pay_method`), the format block parsed into
key/value pairs (`Тема`, `Сложность`, `Формат`, …), attendance
(`teams_registered`, `teams_came`, `people_registered`) and `results` — one entry
per team with `place`, `team`, `rank`, `total` and per-round scores.

The CSV is the same scoreboard in one flat table, with the game's identity
repeated on every row so files from many games concatenate directly:

```
game_id,date,city,title,game_number,place,team,rank,total,round_1,…,round_7
01a04287-…,2026-09-01T19:30:00,Баку,[видеоигры] BAKU,3,1,Noldor,unattainable,52,5,…,18
```

Ranks come through as the site's own codes (`novich`, `sergeant`, `lieutenant`,
`general`, `rambo`, `chuck`, `unattainable`); the JSON adds the Russian label as
`rank_title`.

## Layout

```
quizplease/
  jsobj.py    parser for the minified `window.__NUXT__` literal
  xlsx.py     minimal .xlsx reader (shared/inline strings and numbers)
  scraper.py  fetch, normalise, flatten
  cli.py      command line entry point
tests/        unit tests, no network access required
data/games/   scraped output
```

## Tests

```bash
python -m unittest discover -s tests
```

## Notes

- Scores are half-points on some rounds, so totals are floats when they need to
  be and ints when they don't.
- A game that has not finished has no scoreboard; `results` is then empty and no
  CSV is written.
- `scrape_game_from_html(html, url, results_xlsx=...)` re-processes an archived
  copy of a page without touching the network.
