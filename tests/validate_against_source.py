"""End-to-end check: what the dashboard renders vs. the raw scraped files.

Recomputes each figure straight from `data/<city>/games/*.json` -- a path that
shares no code with the browser app -- drives a headless Chromium against the
running app, and compares. Run the app first:

    python3 -m http.server 8765 --directory app &
    python3 tests/validate_against_source.py Колобки Ванси Noldor
"""

import json
import os
import statistics
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dashboard.model import team_key   # noqa: E402  (only for the name key)

DEFAULT_TEAMS = ["Колобки", "Ванси", "Noldor"]


def load_rows(city="baku"):
    """Every (game, scoreboard row) pair, computed from the raw scrape."""
    games_dir = os.path.join(ROOT, "data", city, "games")
    rows = []
    for name in sorted(os.listdir(games_dir)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(games_dir, name), encoding="utf-8") as handle:
            game = json.load(handle)
        results = game.get("results") or []
        if not results:
            continue
        best = max(row["total"] for row in results if row.get("total") is not None)
        for row in results:
            rows.append({
                "team_key": team_key(row.get("team")),
                "team": row.get("team"),
                "date": game["date"],
                "game_type": (game.get("template") or {}).get("title") or game.get("title"),
                "position": row.get("place"),
                "total": row.get("total"),
                "teams_count": len(results),
                "best": best,
            })
    return rows


def expected(rows):
    positions = [row["position"] for row in rows if row["position"] is not None]
    totals = [row["total"] for row in rows if row["total"] is not None]
    percentiles = [
        100.0 if row["teams_count"] == 1
        else round((row["teams_count"] - row["position"]) / (row["teams_count"] - 1) * 100, 2)
        for row in rows if row["position"] is not None
    ]
    wins = sum(1 for position in positions if position == 1)
    top3 = sum(1 for position in positions if position <= 3)
    return {
        "games": len(rows),
        "wins": wins,
        "top3": top3,
        "win_rate": wins / len(positions) if positions else None,
        "top3_rate": top3 / len(positions) if positions else None,
        "avg_position": statistics.fmean(positions) if positions else None,
        "best_position": min(positions) if positions else None,
        "avg_score": statistics.fmean(totals) if totals else None,
        "median_score": statistics.median(totals) if totals else None,
        "best_score": max(totals) if totals else None,
        "worst_score": min(totals) if totals else None,
        "avg_percentile": statistics.fmean(percentiles) if percentiles else None,
    }


def number(text):
    if text in (None, "", "—"):
        return None
    cleaned = text.replace("±", "").replace("%", "").replace(",", ".").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def close(left, right, tolerance=0.06):
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    return abs(left - right) <= tolerance


def main(argv):
    teams = argv or DEFAULT_TEAMS
    rows = load_rows()

    script = os.path.join(ROOT, "tests", "browser", "read_kpis.mjs")
    output = subprocess.run(["node", script] + teams, capture_output=True, text=True, check=True)
    rendered = json.loads(output.stdout)
    if rendered["problems"]:
        print("browser reported console errors:", rendered["problems"])
        return 1

    failures = []
    for team in teams:
        shown = rendered["teams"].get(team, {})
        if "error" in shown:
            failures.append("%s: %s" % (team, shown["error"]))
            continue

        team_rows = [row for row in rows if row["team_key"] == team_key(team)]
        want = expected(team_rows)
        kpis = shown["kpis"]

        checks = [
            ("игр", float(want["games"]), number(kpis.get("Игр с результатами"))),
            ("средний процентиль", want["avg_percentile"], number(kpis.get("Средний процентиль"))),
            ("побед", float(want["wins"]), number(kpis.get("Победы"))),
            ("топ-3", float(want["top3"]), number(kpis.get("Топ-3"))),
            ("среднее место", want["avg_position"], number(kpis.get("Среднее место"))),
            ("средний балл", want["avg_score"], number(kpis.get("Средний балл"))),
        ]
        best_worst = (kpis.get("Лучший / худший балл") or "").split("/")
        if len(best_worst) == 2:
            checks.append(("лучший балл", want["best_score"], number(best_worst[0])))
            checks.append(("худший балл", want["worst_score"], number(best_worst[1])))

        for label, want_value, got_value in checks:
            if not close(want_value, got_value):
                failures.append("%s / %s: источник %s, дашборд %s" % (team, label, want_value, got_value))

        # The games table must list exactly the team's games.
        if len(shown["gameRows"]) != want["games"]:
            failures.append("%s: в таблице %d строк, в источнике %d игр"
                            % (team, len(shown["gameRows"]), want["games"]))

        # Game-type rows must sum back to the same number of games.
        type_games = sum(int(row[1]) for row in shown["typeRows"] if len(row) > 1 and row[1].isdigit())
        if type_games != want["games"]:
            failures.append("%s: по форматам %d игр, всего %d" % (team, type_games, want["games"]))

        print("%-12s игр=%-3d победы=%-2d топ3=%-2d ср.место=%-5.2f ср.процентиль=%-5.2f  ✓"
              % (team, want["games"], want["wins"], want["top3"],
                 want["avg_position"] or 0, want["avg_percentile"] or 0))

    if failures:
        print("\nРасхождения:")
        for failure in failures:
            print(" -", failure)
        return 1
    print("\nВсе значения совпадают с исходными данными.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
