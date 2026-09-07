"""Compile scraped games into the dataset the browser app loads.

Reads whatever cities exist under `data/` -- nothing is hard-coded, so adding a
city (or re-running the scraper for an existing one) and rebuilding is all it
takes for the app to show it. Everything the app needs is precomputed here or
derived in the browser; the app never parses the scraper's format.

    python -m dashboard.build              # data/ -> app/data/
    python -m dashboard.build --data-dir … --out-dir …
"""

import argparse
import csv
import datetime
import json
import os
import sys

from .analytics import annotate_game
from .model import build_dataset, team_key

__all__ = ["build_city", "build_all", "discover_cities"]

GAME_FIELDS = ("id", "date", "season", "title", "game_number", "game_type",
               "game_family", "league", "theme", "difficulty", "format", "venue",
               "address", "price", "currency", "url")


def discover_cities(data_dir):
    """Every directory under ``data_dir`` that holds scraped games."""
    if not os.path.isdir(data_dir):
        return []
    cities = []
    for name in sorted(os.listdir(data_dir)):
        if os.path.isdir(os.path.join(data_dir, name, "games")):
            cities.append(name)
    return cities


def _load_records(city_dir):
    games_dir = os.path.join(city_dir, "games")
    records = []
    for name in sorted(os.listdir(games_dir)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(games_dir, name), encoding="utf-8") as handle:
            try:
                records.append(json.load(handle))
            except ValueError as error:
                print("  skipping unreadable %s: %s" % (name, error), file=sys.stderr)
    return records


def _load_standings(city_dir):
    path = os.path.join(city_dir, "standings.csv")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        row["team_key"] = team_key(row.get("team"))
        for field in ("points", "games", "position"):
            value = row.get(field)
            try:
                row[field] = float(value) if "." in (value or "") else int(value)
            except (TypeError, ValueError):
                row[field] = None
    return rows


def _distinct(values):
    """Sorted distinct non-empty values -- the app's filter options."""
    return sorted({value for value in values if value})


def _venues(games):
    """One entry per venue, with its coordinates checked before use.

    The source's coordinates are not always usable: a few venues carry a
    longitude in the latitude field and no longitude at all. Rather than plot
    those somewhere wrong, they are marked `has_coords: false` and the app lists
    them beside the map instead.
    """
    venues = {}
    for game in games:
        name = game.venue
        if not name:
            continue
        entry = venues.setdefault(name, {
            "title": name, "address": game.address, "lat": None, "lon": None,
            "games": 0, "scored": 0, "first": None, "last": None,
        })
        entry["games"] += 1
        if game.results:
            entry["scored"] += 1
        if game.date:
            entry["first"] = min(entry["first"] or game.date, game.date)
            entry["last"] = max(entry["last"] or game.date, game.date)
        if entry["lat"] is None and game.lat is not None and game.lon is not None:
            entry["lat"], entry["lon"] = game.lat, game.lon
        entry["address"] = entry["address"] or game.address

    for entry in venues.values():
        lat, lon = entry["lat"], entry["lon"]
        entry["has_coords"] = (
            isinstance(lat, (int, float)) and isinstance(lon, (int, float))
            and -90 <= lat <= 90 and -180 <= lon <= 180
        )
        if not entry["has_coords"]:
            entry["lat"] = entry["lon"] = None

    return sorted(venues.values(), key=lambda entry: (-entry["games"], entry["title"]))


def _variants_by_family(games):
    """Which format names sit under each family, for the dependent filter."""
    variants = {}
    for game in games:
        if not game.game_family:
            continue
        variants.setdefault(game.game_family, set()).add(game.game_type)
    return dict((family, sorted(names)) for family, names in sorted(variants.items()))


def _game_payload(game):
    payload = dict((field, getattr(game, field)) for field in GAME_FIELDS)
    payload["rounds"] = game.rounds
    payload["round_labels"] = ["Раунд %s" % name.split("_")[1] for name in game.rounds]
    payload["teams_count"] = game.teams_count
    payload["best_total"] = game.best_total
    payload["worst_total"] = game.worst_total
    payload["mean_total"] = game.mean_total
    payload["has_results"] = bool(game.results)
    # Where the scoreboard came from: the JSON endpoint (recent games, with team
    # ids) or the published .xlsx (the back catalogue, without them).
    payload["results_source"] = game.results_source
    return payload


def _row_payload(game, row):
    extras = row.extras or {}
    return {
        "game_id": game.id,
        "team": row.team,
        "team_key": row.team_key,
        "team_ids": row.team_ids,
        "position": row.position,
        "total": row.total,
        "percentile": extras.get("percentile"),
        "score_pct": extras.get("score_pct"),
        "gap_to_best": extras.get("gap_to_best"),
        "gap_to_mean": extras.get("gap_to_mean"),
        "rank": row.rank,
        "rank_title": row.rank_title,
        "attributes": row.attributes or None,
        # Aligned with the game's `rounds` list so the app never has to match keys.
        "rounds": [row.rounds.get(name) for name in game.rounds],
        "round_pct": [extras.get("round_pct", {}).get(name) for name in game.rounds],
    }


def _team_payload(games_by_id, rows):
    """Teams discovered from the scoreboards, with their display names."""
    teams = {}
    for row in rows:
        entry = teams.setdefault(row["team_key"], {
            "key": row["team_key"], "name": row["team"], "names": set(),
            "ids": set(), "games": 0, "first": None, "last": None,
        })
        entry["names"].add(row["team"])
        entry["ids"].update(row["team_ids"])
        entry["games"] += 1
        date = games_by_id[row["game_id"]]["date"]
        if date:
            entry["first"] = min(entry["first"] or date, date)
            entry["last"] = max(entry["last"] or date, date)

    result = []
    for entry in teams.values():
        names = sorted(entry["names"])
        # Prefer the most common spelling; ties fall back to the longest.
        entry["name"] = max(names, key=lambda name: (names.count(name), len(name)))
        entry["names"] = names
        entry["ids"] = sorted(entry["ids"])
        result.append(entry)
    result.sort(key=lambda entry: (-entry["games"], entry["name"].casefold()))
    return result


def build_city(slug, data_dir="data", out_dir=os.path.join("app", "data")):
    """Build one city's dataset file; returns its index entry."""
    city_dir = os.path.join(data_dir, slug)
    records = _load_records(city_dir)
    games = [annotate_game(game) for game in build_dataset(records)]
    scored = [game for game in games if game.results]

    game_payloads = [_game_payload(game) for game in games]
    games_by_id = dict((payload["id"], payload) for payload in game_payloads)
    rows = [_row_payload(game, row) for game in scored for row in game.results]
    teams = _team_payload(games_by_id, rows)

    first = next((game.date for game in games if game.date), None)
    last = next((game.date for game in reversed(games) if game.date), None)
    sample = next((game for game in games if game.city), None)

    dataset = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "city": {
            "slug": slug,
            "name": (sample.city if sample else slug),
            "currency": next((game.currency for game in games if game.currency), None),
        },
        "coverage": {
            "games_total": len(games),
            "games_with_results": len(scored),
            "result_rows": len(rows),
            "teams": len(teams),
            "date_from": first,
            "date_to": last,
            "scored_from": scored[0].date if scored else None,
            "scored_to": scored[-1].date if scored else None,
        },
        "filters": {
            # Families first: the site names a format once and then adds an
            # edition per night, so the raw list is mostly one-offs.
            "families": _distinct(game.game_family for game in scored),
            "variants_by_family": _variants_by_family(scored),
            "game_types": _distinct(game.game_type for game in scored),
            "leagues": _distinct(game.league for game in scored),
            "seasons": _distinct(game.season for game in scored),
            "venues": _distinct(game.venue for game in scored),
            "themes": _distinct(game.theme for game in scored),
            "difficulties": _distinct(game.difficulty for game in scored),
        },
        "venues": _venues(games),
        "games": game_payloads,
        "rows": rows,
        "teams": teams,
        "standings": _load_standings(city_dir),
    }

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "%s.json" % slug)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(dataset, handle, ensure_ascii=False, separators=(",", ":"))

    return {
        "slug": slug,
        "name": dataset["city"]["name"],
        "file": "%s.json" % slug,
        "games": len(games),
        "games_with_results": len(scored),
        "teams": len(teams),
        "date_from": dataset["coverage"]["scored_from"],
        "date_to": dataset["coverage"]["scored_to"],
        "bytes": os.path.getsize(path),
    }


def build_all(data_dir="data", out_dir=os.path.join("app", "data")):
    entries = [build_city(slug, data_dir, out_dir) for slug in discover_cities(data_dir)]
    entries.sort(key=lambda entry: -entry["games_with_results"])
    index = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "cities": entries,
    }
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "index.json"), "w", encoding="utf-8") as handle:
        json.dump(index, handle, ensure_ascii=False, indent=1)
    return index


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m dashboard.build",
                                     description=__doc__.split("\n")[0])
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--out-dir", default=os.path.join("app", "data"))
    args = parser.parse_args(argv)

    index = build_all(args.data_dir, args.out_dir)
    if not index["cities"]:
        print("no cities found under %s" % args.data_dir, file=sys.stderr)
        return 1
    for entry in index["cities"]:
        print("%-12s %4d games (%3d scored) %4d teams %6.1f KB"
              % (entry["slug"], entry["games"], entry["games_with_results"],
                 entry["teams"], entry["bytes"] / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
