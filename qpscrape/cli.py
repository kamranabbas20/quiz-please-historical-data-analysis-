"""Command line entry point: ``python -m qpscrape <discover|scrape|validate|all>``."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from . import discover as discover_mod
from . import scrape as scrape_mod
from . import sources
from . import validate as validate_mod


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qpscrape", description=__doc__)
    parser.add_argument("command",
                        choices=["discover", "scrape", "validate", "all"])
    parser.add_argument("--base", default=sources.BASE)
    parser.add_argument("--url", default=None,
                        help="page to inspect during discovery "
                             "(default <base>/schedule-past)")
    parser.add_argument("--out", default=".", help="output directory")
    parser.add_argument("--discovery-dir", default="discovery")
    parser.add_argument("--delay", type=float, default=0.5,
                        help="minimum seconds between HTTP requests")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=None,
                        help="cap detail-page crawl (for smoke tests)")
    parser.add_argument("--headful", action="store_true",
                        help="show the browser during discovery")
    parser.add_argument("--no-browser", action="store_true",
                        help="skip Playwright sources entirely")
    parser.add_argument("--no-details", action="store_true",
                        help="skip per-game page crawl")
    parser.add_argument("--ignore-robots", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.command in ("discover", "all"):
        url = args.url or f"{args.base}/schedule-past"
        report = discover_mod.discover(
            url=url, out_dir=args.discovery_dir, headless=not args.headful,
        )
        print(json.dumps(report["suggested_config"], ensure_ascii=False, indent=2))
        print(f"best endpoint: {report['best_endpoint']}")
        print(f"game links in rendered DOM: {len(report['dom_game_links'])}")

    if args.command in ("scrape", "all"):
        scrape_mod.run(
            base=args.base,
            config_path=f"{args.discovery_dir}/config.json",
            out_dir=args.out,
            delay=args.delay,
            workers=args.workers,
            use_browser=not args.no_browser,
            crawl_details=not args.no_details,
            limit=args.limit,
            respect_robots=not args.ignore_robots,
        )

    if args.command in ("validate", "all"):
        print(validate_mod.report(
            f"{args.out}/baku_quizplease_history.json",
            f"{args.out}/duplicates_removed.json",
        ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
