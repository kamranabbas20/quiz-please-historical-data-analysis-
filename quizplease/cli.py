"""Command line entry point."""

import argparse
import csv
import os
import sys

from .api import ApiError, QuizPleaseApi
from .harvest import harvest_city, write_standings
from .scraper import ScrapeError, dumps, results_to_csv_rows, scrape_game


def build_parser():
    parser = argparse.ArgumentParser(
        prog="python -m quizplease",
        description="Scrape Quiz Please games into JSON and CSV.",
    )
    parser.add_argument("--delay", type=float, default=0.0, metavar="SECONDS",
                        help="pause between API calls (default: 0)")
    sub = parser.add_subparsers(dest="command", required=True)

    game = sub.add_parser("game", help="scrape individual game pages")
    game.add_argument("games", nargs="+", metavar="URL_OR_ID")
    game.add_argument("--city", default="baku",
                      help="subdomain used when a bare game id is given (default: baku)")
    game.add_argument("-o", "--out-dir", default="data/games")
    game.add_argument("--stdout", action="store_true",
                      help="print JSON to stdout instead of writing files")
    game.add_argument("--no-results", action="store_true",
                      help="skip downloading the scoreboard")
    game.add_argument("--raw", action="store_true",
                      help="keep the untouched record from the page payload")

    city = sub.add_parser("city", help="scrape a city's entire back catalogue")
    city.add_argument("slugs", nargs="+", metavar="CITY_SLUG",
                      help="city slug, e.g. baku (see the `cities` command)")
    city.add_argument("-o", "--out-dir", default="data")
    city.add_argument("--limit", type=int, help="only the N most recent games")
    city.add_argument("--refresh", action="store_true",
                      help="re-fetch games already on disk")
    city.add_argument("--quiet", action="store_true", help="only print the summary")
    city.add_argument("--workers", type=int, default=1, metavar="N",
                      help="fetch N games at once (default: 1). Keep it modest — "
                           "each game costs two or three requests to someone's server.")

    standings = sub.add_parser(
        "standings", help="all-time rating standings for a city (points, games played)")
    standings.add_argument("slugs", nargs="+", metavar="CITY_SLUG")
    standings.add_argument("-o", "--out-dir", default="data")

    cities = sub.add_parser("cities", help="list cities and how many finished games each has")
    cities.add_argument("--counts", action="store_true",
                        help="query the number of finished games per city (slow)")
    cities.add_argument("-o", "--out-file", help="write the listing to CSV")
    return parser


def _scrape_games(args, api):
    failures = 0
    for reference in args.games:
        try:
            record = scrape_game(reference, city=args.city,
                                 with_results=not args.no_results, keep_raw=args.raw)
        except (ScrapeError, ValueError) as error:
            print("error: %s" % error, file=sys.stderr)
            failures += 1
            continue

        if args.stdout:
            print(dumps(record))
            continue

        os.makedirs(args.out_dir, exist_ok=True)
        json_path = os.path.join(args.out_dir, "%s.json" % record["id"])
        with open(json_path, "w", encoding="utf-8") as handle:
            handle.write(dumps(record) + "\n")
        print("wrote %s" % json_path)

        if record.get("results"):
            csv_path = os.path.join(args.out_dir, "%s_results.csv" % record["id"])
            with open(csv_path, "w", encoding="utf-8", newline="") as handle:
                csv.writer(handle).writerows(results_to_csv_rows(record))
            print("wrote %s" % csv_path)
    return failures


def _harvest_cities(args, api):
    failures = 0
    for slug in args.slugs:
        def progress(index, total, record, note, slug=slug):
            if args.quiet or note == "cached":
                return
            label = record["full_title"] if record else note
            print("[%s] %4d/%d %s %s" % (slug, index, total, note, label))

        try:
            summary = harvest_city(slug, out_dir=args.out_dir, api=api,
                                   refresh=args.refresh, limit=args.limit,
                                   on_progress=progress, workers=max(1, args.workers))
        except ApiError as error:
            print("error: %s" % error, file=sys.stderr)
            failures += 1
            continue

        print("%s (%s): %d games listed, %d scraped, %d cached, %d failed -> "
              "%d games / %d result rows"
              % (summary["city"], slug, summary["listed"], summary["scraped"],
                 summary["skipped"], summary["failed"], summary["games_on_disk"],
                 summary["results_rows"]))
        for path in summary["tables"]:
            print("  wrote %s" % path)
    return failures


def _standings(args):
    failures = 0
    for slug in args.slugs:
        try:
            path, count = write_standings(slug, out_dir=args.out_dir, delay=args.delay)
        except ApiError as error:
            print("error: %s" % error, file=sys.stderr)
            failures += 1
            continue
        print("wrote %s (%d team/league rows)" % (path, count))
    return failures


def _list_cities(args, api):
    rows = []
    for city in sorted(api.cities(), key=lambda c: (c.get("title") or "")):
        country = (city.get("country") or {}).get("title")
        total = ""
        if args.counts:
            _, pagination = api.finished_games_page(city["id"], page=1, per_page=1)
            total = pagination.get("total", "")
        rows.append([city.get("id"), city.get("slug"), city.get("title"), country, total])

    if args.out_file:
        with open(args.out_file, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["city_id", "slug", "city", "country", "finished_games"])
            writer.writerows(rows)
        print("wrote %s (%d cities)" % (args.out_file, len(rows)))
    else:
        for row in rows:
            print("%-6s %-16s %-28s %-22s %s" % tuple(str(value) for value in row))
    return 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    api = QuizPleaseApi(delay=args.delay)

    if args.command == "game":
        return 1 if _scrape_games(args, api) else 0
    if args.command == "city":
        return 1 if _harvest_cities(args, api) else 0
    if args.command == "standings":
        return 1 if _standings(args) else 0
    return _list_cities(args, api)


if __name__ == "__main__":
    sys.exit(main())
