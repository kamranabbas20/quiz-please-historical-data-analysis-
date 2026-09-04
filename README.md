# quiz-please-historical-data-analysis

Scraper for the complete historical QuizPlease game archive of the Baku city
site (`https://baku.quizplease.com/schedule-past`), producing
`baku_quizplease_history.csv` and `baku_quizplease_history.json`.

## Status: code complete, dataset not yet generated

The scraper has **not been run against the live site**. It was authored in a
sandboxed environment whose egress proxy answers `403` to `CONNECT` for every
host outside a small package-registry allowlist — `curl https://example.com`,
`curl https://baku.quizplease.com/...` and headless Chromium
(`net::ERR_TUNNEL_CONNECTION_FAILED`, with and without the proxy) all fail
identically. The discovery step therefore could not observe the site's real
network traffic, so **no API endpoint has been identified yet** and the CSV/JSON
deliverables are absent. Run `python -m qpscrape all` on a machine with
internet access to produce them; discovery is automated, so no manual DevTools
work is required.

Everything that does not require network access has been tested offline —
see `tests/test_offline.py`.

## Install

```bash
pip install -r requirements.txt
python -m playwright install chromium   # skip if Chromium is already provisioned
```

## Run

```bash
# 1. Find out how the archive loads (browser + network interception)
python -m qpscrape discover                 # add --headful to watch it

# 2. Scrape using whatever discovery found, plus every fallback source
python -m qpscrape scrape

# 3. Completeness report
python -m qpscrape validate

# Or all three:
python -m qpscrape all
```

Useful flags: `--limit 20` (smoke test), `--delay 1.0` (slower crawl),
`--no-browser` (HTTP only), `--workers 2`, `--out data/`, `-v`.

Re-running is safe and idempotent: outputs are rewritten from scratch each run,
so new historical games are picked up automatically.

## How it works

```
discover (Playwright)  ->  discovery/config.json
                              |
     +------------------------+------------------------+
     |            |                |                   |
   API        sitemap.xml     static archive      rendered DOM
 (preferred)  (+robots.txt)   pages + ?page=N     (scroll/load-more)
     |            |                |                   |
     +------------+--------+-------+-------------------+
                           v
                 union of /game/<uuid> URLs
                           v
                 per-game detail crawl
                           v
        normalise -> deduplicate -> validate -> CSV + JSON
```

### Discovery (`qpscrape/discover.py`)

Loads `/schedule-past` in Chromium, records every XHR/fetch/document response,
exhausts infinite scroll and "load more"/"показать ещё" buttons, then **ranks
observed endpoints by how many game-shaped JSON records each returned**. The
winner, its query parameters, required headers and an inferred pagination style
(`page` / `offset` / `cursor`) are written to `discovery/config.json`. Raw
evidence is kept in `discovery/network_log.json` and `discovery/report.json`.

### Why four sources, not one

No single source is trusted to be complete, which is the whole point of the
task:

| Source | Catches | Misses |
| --- | --- | --- |
| Backend API | everything the archive UI can show, paginated | records the endpoint window excludes |
| `sitemap.xml` (+ nested indexes, discovered via `robots.txt`) | old games no longer linked from the listing | games never indexed |
| Static archive pages, incl. `?page=N` walking | server-rendered listings | client-only content |
| Rendered DOM after full scroll | client-only content | nothing beyond what the UI loads |

All four run and their `/game/<uuid>` URL sets are unioned, so a year that one
source has dropped is still recovered by another. Each record's `source` column
records which sources it was found in.

### Extraction is shape-based, not selector-based

`qpscrape/extract.py` deliberately avoids hard-coded CSS selectors. For any
page or payload it pulls, in precedence order: hydration blobs
(`__NEXT_DATA__` / `__NUXT__` / `__INITIAL_STATE__` / `__APOLLO_STATE__`),
large inline JSON literals, schema.org JSON-LD, OpenGraph meta, then labelled
text (ru/az/en label words). Game records are located by *shape* — a dict
carrying an id plus a date-ish or venue-ish key — and mapped to dataset fields
through an alias table, so the same code handles an API response and an
embedded blob without knowing either schema in advance.

### Normalisation

Dates to `YYYY-MM-DD`, times to `HH:MM` (ISO timestamps parsed as timestamps,
not scanned for a bare time), prices to a number with the currency kept
separately (`₼`/`AZN`/`RUB`/`USD`/... recognised), durations to whole minutes
(`2 часа 30 мин` → `150`). Russian and Azerbaijani month names are supported,
since the site is not served in English.

### Deduplication

Hierarchy: UUID → canonical game URL → `date + normalised title`. Nothing is
dropped silently — every removal is written to `duplicates_removed.json` with
its reason, and non-empty fields from the discarded copy backfill empty fields
on the kept record.

## Output schema

`game_id, date, start_time, title, game_number, category, theme, venue,
venue_address, price, currency, duration_minutes, rounds, difficulty, status,
description, game_url, results_url, image_url, city, latitude, longitude,
source, scraped_at`

## Validation report

`python -m qpscrape validate` prints total games, earliest/latest date, counts
by year and by category, missing-field counts, duplicates removed with reasons,
counts by source, and **months with zero games between the first and last
date** as candidate archive gaps. Any of 2023–2026 missing entirely is flagged
explicitly rather than treated as "no games occurred".

## Ethics and rate limiting

Only publicly accessible pages are fetched. `robots.txt` is honoured by default
(`--ignore-robots` exists but is not the default), requests are serialised
through a rate limiter (default 0.5 s minimum gap, `--delay` to raise it),
failures retry with exponential backoff plus jitter and respect `Retry-After`,
and no login, CAPTCHA or access control is bypassed. No player-level or private
data is collected.

## Known gaps and uncertainties

1. **No endpoint identified yet.** Discovery has never run against the live
   site (see Status). Until it does, `qpscrape scrape` falls back to sitemap +
   static pages + rendered DOM, which may be less complete than the API.
2. **Pagination inference is a guess until observed.** `_guess_pagination`
   reads the parameters the site itself used; if the archive turns out to use
   POST bodies or GraphQL variables for paging, `discovery/config.json` needs a
   manual `post_data` edit. The `unknown` style deliberately stops after one
   page rather than inventing parameters.
3. **Field coverage is unverified.** The alias tables cover the key names these
   payloads commonly use, but `difficulty`, `rounds` and `duration` may simply
   not be exposed for Baku games; they will come out null rather than wrong.
4. **`schedule-past` may not reach back to 2023.** If the validation report
   flags a missing year, check `discovery/report.json` for an endpoint
   parameter (a date range or season filter) that the UI never exercised.
5. **Results/protocol data is out of scope.** `results_url` is captured when a
   page links one, but team standings are not parsed.
