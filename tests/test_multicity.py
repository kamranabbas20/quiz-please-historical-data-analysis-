"""Checks that the app is really driven by the dataset, not by Baku.

Builds a throwaway two-city dataset (one of them a fixture with its own teams
and quiz formats), serves the same app files against it, and drives a headless
browser: switching city must swap the team list, the filters and the coverage
line. This is also the test for "new data appears automatically" -- nothing is
edited but the files under data/.

Run: python3 tests/test_multicity.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dashboard.build import build_all   # noqa: E402


def game(game_id, date, title, template, city, slug, results):
    return {
        "id": game_id, "date": date, "title": title, "game_number": "1",
        "city": {"id": 1, "name": city, "slug": slug},
        "country": {"id": 1, "name": "Страна"},
        "place": {"id": 1, "title": "Бар", "address": "ул. Тестовая, 1"},
        "template": {"id": 1, "title": template, "level": "medium"},
        "format": {"Тема": "обо всём"}, "league": "классика",
        "price": 10, "currency": "₼",
        "url": "https://%s.quizplease.com/game/%s" % (slug, game_id),
        "results": results,
    }


def row(place, team, total, rounds, team_id):
    return {
        "place": place, "team": team, "team_id": team_id, "total": total,
        "rank": "chuck", "rank_title": "Чак Норрис",
        "rounds": {"round_%d" % (i + 1): value for i, value in enumerate(rounds)},
    }


class MultiCityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp()
        cls.app = os.path.join(cls.root, "app")
        shutil.copytree(os.path.join(ROOT, "app"), cls.app,
                        ignore=shutil.ignore_patterns("data"))

        data = os.path.join(cls.root, "data")
        cls._write(data, "baku", [
            game("b1", "2026-07-01T19:30:00", "Квиз, плиз! BAKU", "Квиз, плиз!", "Баку", "baku", [
                row(1, "Колобки", 50, [30, 20], 11), row(2, "Ванси", 40, [20, 20], 12),
            ]),
        ])
        cls._write(data, "testville", [
            game("t1", "2026-07-02T19:30:00", "[тест] TESTVILLE", "[тест]", "Тествилль", "testville", [
                row(1, "Альфа", 60, [30, 30], 21),
                row(2, "Бета", 30, [20, 10], 22),
                row(3, "Гамма", 10, [5, 5], 23),
            ]),
        ])
        build_all(data, os.path.join(cls.app, "data"))

        handler = partial(SimpleHTTPRequestHandler, directory=cls.app)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        shutil.rmtree(cls.root, ignore_errors=True)

    @staticmethod
    def _write(data_dir, slug, games):
        games_dir = os.path.join(data_dir, slug, "games")
        os.makedirs(games_dir, exist_ok=True)
        for entry in games:
            with open(os.path.join(games_dir, "%s.json" % entry["id"]), "w", encoding="utf-8") as handle:
                json.dump(entry, handle, ensure_ascii=False)

    def test_index_lists_both_cities(self):
        with open(os.path.join(self.app, "data", "index.json"), encoding="utf-8") as handle:
            index = json.load(handle)
        self.assertEqual({city["slug"] for city in index["cities"]}, {"baku", "testville"})

    def test_city_switch_swaps_teams_and_filters(self):
        script = os.path.join(ROOT, "tests", "browser", "city_switch.mjs")
        result = subprocess.run(
            ["node", script, "http://127.0.0.1:%d/" % self.port],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)

        self.assertEqual(report["errors"], [])
        self.assertEqual(report["cities"], ["baku", "testville"])
        # Teams come from whichever city is selected, and from nowhere else.
        self.assertTrue(any("Колобки" in team for team in report["teamsFirst"]))
        self.assertTrue(any("Альфа" in team for team in report["teamsSecond"]))
        self.assertFalse(any("Колобки" in team for team in report["teamsSecond"]))
        # So do the quiz formats.
        self.assertTrue(any("Квиз, плиз!" in option for option in report["typesFirst"]))
        self.assertTrue(any("[тест]" in option for option in report["typesSecond"]))
        # And the coverage line.
        self.assertIn("Тествилль", report["coverageSecond"] + report["teamsSecond"][0])
        self.assertEqual(report["kpiGamesSecond"], "1")


if __name__ == "__main__":
    unittest.main()
