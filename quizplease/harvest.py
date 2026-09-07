"""Walk a city's whole back catalogue of finished games.

One JSON file per game plus two CSVs per city: `games.csv` (one row per game)
and `results.csv` (one row per team per game, the long format most analysis
wants). Harvesting is resumable -- games already on disk are skipped unless
`refresh` is set, so an interrupted run costs nothing to restart.
"""

import concurrent.futures
import csv
import datetime
import io
import json
import os

from .api import ApiError, QuizPleaseApi
from .rating import RatingApi
from .scraper import (
    GAME_CSV_COLUMNS, RESULT_CSV_COLUMNS, ScrapeError, dumps, fetch, game_csv_row,
    normalize_game, normalize_results, parse_results_table, result_csv_rows,
    round_names_of,
)
from .xlsx import read_first_sheet

__all__ = ["harvest_city", "load_city_games", "write_city_tables", "write_standings"]

GAME_PAGE_URL = "https://%s.quizplease.com/game/%s"


def scoreboard_for(api, record, on_note=None):
    """The best scoreboard available for a game.

    Two sources, in order of quality:

    1. `/api/games/{id}/results` -- JSON, and the only one carrying team ids,
       but it only covers games from roughly the last six months.
    2. `result.table` -- the .xlsx the site has published for years. No team
       ids, and the layout drifts between seasons, but it is the whole back
       catalogue.

    Returns `(rows, source)`.
    """
    rows = normalize_results(api.results(record["id"]))
    if rows:
        return rows, "api"

    table_url = (record.get("result") or {}).get("table")
    if not table_url:
        return [], None
    try:
        workbook = fetch(table_url, binary=True)
        rows = parse_results_table(read_first_sheet(io.BytesIO(workbook)))
    except (ScrapeError, ValueError, KeyError) as error:
        if on_note:
            on_note("scoreboard unreadable: %s" % error)
        return [], None
    return rows, "xlsx" if rows else None


def _city_context(city):
    """Split a `/api/cities/short` entry into the bits normalize_game wants."""
    country = dict(city.get("country") or {})
    currency = country.get("currency")
    if isinstance(currency, dict):
        country["currency"] = currency.get("symbol")
    return (
        {"id": city.get("id"), "name": city.get("title"), "slug": city.get("slug")},
        country,
    )


def harvest_city(slug, out_dir="data", api=None, refresh=False, limit=None,
                 per_page=100, on_progress=None, workers=1):
    """Scrape every finished game of one city into ``out_dir/<slug>``.

    Returns a summary dict. ``on_progress(index, total, record_or_none, note)``
    is called after each game, in listing order, so callers can report progress
    their own way.

    ``workers`` fetches that many games at once. Each game costs two or three
    requests (record, scoreboard JSON, sometimes an .xlsx download), and the
    time is nearly all latency, so a handful of workers turns a city the size
    of Moscow from days into hours. Keep it small: this is someone's server.
    """
    api = api or QuizPleaseApi()
    city = api.city_by_slug(slug)
    city_info, country_info = _city_context(city)

    city_dir = os.path.join(out_dir, slug)
    games_dir = os.path.join(city_dir, "games")
    os.makedirs(games_dir, exist_ok=True)

    max_pages = None
    if limit:
        max_pages = max(1, -(-limit // per_page))   # stop paging early
    listed = list(api.finished_games(city["id"], per_page=per_page, max_pages=max_pages))
    if limit:
        listed = listed[:limit]

    def fetch_one(listing):
        """Fetch and write one game. Returns (record_or_None, note)."""
        game_id = listing.get("id")
        path = os.path.join(games_dir, "%s.json" % game_id)

        if not refresh and os.path.exists(path):
            return None, "cached"

        try:
            game = api.game(game_id)
            record = normalize_game(game, GAME_PAGE_URL % (slug, game_id),
                                    city=city_info, country=country_info)
            note = []
            record["results"], record["results_source"] = scoreboard_for(
                api, game, on_note=note.append)
        except ApiError as error:
            return None, "failed: %s" % error

        record["scraped_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(dumps(record) + "\n")

        if record["results"]:
            label = "scraped (%s, %d teams)" % (record["results_source"], len(record["results"]))
        elif note:
            label = "scraped (%s)" % note[0]
        else:
            label = "scraped (no scoreboard published)"
        return record, label

    scraped = skipped = failed = 0
    if workers > 1:
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
        # map keeps listing order, so progress still reads chronologically.
        outcomes = pool.map(fetch_one, listed)
    else:
        pool = None
        outcomes = (fetch_one(listing) for listing in listed)

    try:
        for index, (record, note) in enumerate(outcomes, start=1):
            if record is not None:
                scraped += 1
            elif note == "cached":
                skipped += 1
            else:
                failed += 1
            if on_progress:
                on_progress(index, len(listed), record, note)
    finally:
        if pool:
            pool.shutdown()

    records = load_city_games(city_dir)
    tables = write_city_tables(city_dir, records)
    return {
        "city": city["title"],
        "slug": slug,
        "city_id": city["id"],
        "listed": len(listed),
        "scraped": scraped,
        "skipped": skipped,
        "failed": failed,
        "games_on_disk": len(records),
        "results_rows": sum(len(record.get("results", [])) for record in records),
        "tables": tables,
    }


def load_city_games(city_dir):
    """Every game JSON written for a city, oldest first."""
    games_dir = os.path.join(city_dir, "games")
    records = []
    for name in sorted(os.listdir(games_dir)) if os.path.isdir(games_dir) else []:
        if not name.endswith(".json"):
            continue
        with open(os.path.join(games_dir, name), encoding="utf-8") as handle:
            records.append(json.load(handle))
    records.sort(key=lambda record: (record.get("date") or "", record.get("id") or ""))
    return records


def write_city_tables(city_dir, records):
    """Write `games.csv` and `results.csv` for a city."""
    games_path = os.path.join(city_dir, "games.csv")
    results_path = os.path.join(city_dir, "results.csv")
    round_names = round_names_of(records)

    with open(games_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(GAME_CSV_COLUMNS)
        writer.writerows(game_csv_row(record) for record in records)

    with open(results_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(RESULT_CSV_COLUMNS + round_names)
        for record in records:
            writer.writerows(result_csv_rows(record, round_names))

    return [games_path, results_path]


STANDINGS_COLUMNS = ["team_id", "team", "league_id", "league", "league_code",
                     "position", "points", "games", "rank_title"]


def write_standings(slug, out_dir="data", api=None, delay=0.0):
    """Save a city's all-time rating tables to `<out_dir>/<slug>/standings.csv`.

    These are lifetime totals per team per league -- the only public record of
    the years of games that predate the published scoreboards.
    """
    api = api or RatingApi(delay=delay)
    rows = api.city_standings(slug)

    city_dir = os.path.join(out_dir, slug)
    os.makedirs(city_dir, exist_ok=True)
    path = os.path.join(city_dir, "standings.csv")
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(STANDINGS_COLUMNS)
        writer.writerows([row.get(column) for column in STANDINGS_COLUMNS] for row in rows)
    return path, len(rows)
