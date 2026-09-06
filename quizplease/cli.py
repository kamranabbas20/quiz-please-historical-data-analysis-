"""Command line entry point: scrape game pages into JSON + CSV."""

import argparse
import csv
import os
import sys

from .scraper import ScrapeError, dumps, results_to_csv_rows, scrape_game


def build_parser():
    parser = argparse.ArgumentParser(
        prog="python -m quizplease",
        description="Scrape Quiz Please game pages into JSON and CSV.",
    )
    parser.add_argument("games", nargs="+", metavar="URL_OR_ID",
                        help="game page URL, or bare game UUID (see --city)")
    parser.add_argument("--city", default="baku",
                        help="subdomain used when a bare game id is given (default: baku)")
    parser.add_argument("-o", "--out-dir", default="data/games",
                        help="directory for the scraped files (default: data/games)")
    parser.add_argument("--stdout", action="store_true",
                        help="print JSON to stdout instead of writing files")
    parser.add_argument("--no-results", action="store_true",
                        help="skip downloading the .xlsx scoreboard")
    parser.add_argument("--raw", action="store_true",
                        help="keep the untouched game record from the page payload")
    return parser


def write_game(record, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, "%s.json" % record["id"])
    with open(json_path, "w", encoding="utf-8") as handle:
        handle.write(dumps(record) + "\n")
    written = [json_path]

    if record.get("results"):
        csv_path = os.path.join(out_dir, "%s_results.csv" % record["id"])
        with open(csv_path, "w", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerows(results_to_csv_rows(record))
        written.append(csv_path)
    return written


def main(argv=None):
    args = build_parser().parse_args(argv)
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

        for path in write_game(record, args.out_dir):
            print("wrote %s" % path)
        print("  %s %s | %s | %d teams, %d results"
              % (record["title"], record["game_number"] or "", record["date_raw"],
                 record["teams_came"] or 0, len(record["results"])))

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
