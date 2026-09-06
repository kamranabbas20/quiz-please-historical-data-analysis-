import csv
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quizplease.api import ApiError, QuizPleaseApi
from quizplease.harvest import harvest_city, load_city_games

CITY = {
    "id": 158, "title": "Баку", "slug": "baku",
    "country": {"id": 30, "title": "Азербайджан",
                "currency": {"symbol": "₼", "code": "AZN"}},
}


def _game(game_id, number, date):
    return {
        "id": game_id, "title": "Квиз, плиз! BAKU", "game_number": number,
        "date": date, "status": 4, "price": 15, "current_price": "15",
        "pay_method": 1, "city_id": 158, "country_id": 30,
        "place": {"id": 1, "title": "Paulaner", "address": "Somewhere"},
        "template": {"id": 1, "title": "классика", "game_level": "medium"},
        "block_with_text": "<p><strong>Тема</strong>: обо всём</p>",
        "team_count": 8, "came_teams": 8, "all_registered_peoples": 56,
    }


class FakeApi(object):
    """Stands in for QuizPleaseApi so the tests never touch the network."""

    def __init__(self, games, results, fail_on=()):
        self.games = games
        self.results_by_id = results
        self.fail_on = set(fail_on)
        self.game_calls = []

    def city_by_slug(self, slug):
        if slug != "baku":
            raise ApiError("no city with slug %r" % slug)
        return CITY

    def finished_games(self, city_id, per_page=100, max_pages=None):
        return iter(self.games)

    def game(self, game_id):
        self.game_calls.append(game_id)
        if game_id in self.fail_on:
            raise ApiError("boom")
        return next(game for game in self.games if game["id"] == game_id)

    def results(self, game_id):
        return self.results_by_id.get(game_id, [])


class HarvestTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.api = FakeApi(
            games=[_game("g1", "141", "04.09.2026 19:30"),
                   _game("g2", "140", "28.08.2026 19:30")],
            results={
                "g1": [{"place": 1, "rank": {"title": "Недосягаемые"},
                        "team": {"id": 7, "title": "Noldor"},
                        "rounds": {"1": "5", "2": "6.5"}, "total": "11.5"}],
                "g2": [{"place": 1, "rank": {"title": "Сержант"},
                        "team": {"id": 8, "title": "Ванси"},
                        "rounds": {"1": "4"}, "total": "4"}],
            },
        )

    def test_writes_games_and_tables(self):
        summary = harvest_city("baku", out_dir=self.dir, api=self.api)
        self.assertEqual((summary["scraped"], summary["skipped"], summary["failed"]), (2, 0, 0))
        self.assertEqual(summary["results_rows"], 2)

        games_dir = os.path.join(self.dir, "baku", "games")
        self.assertEqual(sorted(os.listdir(games_dir)), ["g1.json", "g2.json"])

        with open(os.path.join(games_dir, "g1.json"), encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertEqual(record["city"], {"id": 158, "name": "Баку", "slug": "baku"})
        self.assertEqual(record["currency"], "₼")
        self.assertEqual(record["url"], "https://baku.quizplease.com/game/g1")
        self.assertEqual(record["results"][0]["team_id"], 7)

    def test_tables_are_long_format_and_ordered(self):
        harvest_city("baku", out_dir=self.dir, api=self.api)

        with open(os.path.join(self.dir, "baku", "games.csv"), encoding="utf-8") as handle:
            games = list(csv.DictReader(handle))
        self.assertEqual([row["game_number"] for row in games], ["140", "141"])  # oldest first

        with open(os.path.join(self.dir, "baku", "results.csv"), encoding="utf-8") as handle:
            results = list(csv.DictReader(handle))
        self.assertEqual([row["team"] for row in results], ["Ванси", "Noldor"])
        # the union of rounds across games becomes the column set
        self.assertEqual(results[0]["round_2"], "")
        self.assertEqual(results[1]["round_2"], "6.5")

    def test_resumes_without_refetching(self):
        harvest_city("baku", out_dir=self.dir, api=self.api)
        self.api.game_calls = []

        summary = harvest_city("baku", out_dir=self.dir, api=self.api)
        self.assertEqual(self.api.game_calls, [])
        self.assertEqual((summary["scraped"], summary["skipped"]), (0, 2))

        summary = harvest_city("baku", out_dir=self.dir, api=self.api, refresh=True)
        self.assertEqual(sorted(self.api.game_calls), ["g1", "g2"])
        self.assertEqual(summary["scraped"], 2)

    def test_one_bad_game_does_not_stop_the_run(self):
        self.api.fail_on = {"g1"}
        summary = harvest_city("baku", out_dir=self.dir, api=self.api)
        self.assertEqual((summary["scraped"], summary["failed"]), (1, 1))
        self.assertEqual(len(load_city_games(os.path.join(self.dir, "baku"))), 1)

    def test_limit(self):
        summary = harvest_city("baku", out_dir=self.dir, api=self.api, limit=1)
        self.assertEqual(summary["listed"], 1)

    def test_unknown_city(self):
        with self.assertRaises(ApiError):
            harvest_city("atlantis", out_dir=self.dir, api=self.api)


class ApiTest(unittest.TestCase):
    def test_pagination_stops_at_last_page(self):
        pages = {
            1: ([{"id": "a"}], {"total_pages": 2}),
            2: ([{"id": "b"}], {"total_pages": 2}),
        }
        api = QuizPleaseApi()
        api.finished_games_page = lambda city_id, page=1, per_page=100: pages[page]
        self.assertEqual([g["id"] for g in api.finished_games(1)], ["a", "b"])

    def test_max_pages_caps_the_walk(self):
        api = QuizPleaseApi()
        api.finished_games_page = lambda city_id, page=1, per_page=100: (
            [{"id": "p%d" % page}], {"total_pages": 9})
        self.assertEqual([g["id"] for g in api.finished_games(1, max_pages=2)], ["p1", "p2"])

    def test_empty_page_terminates(self):
        api = QuizPleaseApi()
        api.finished_games_page = lambda city_id, page=1, per_page=100: ([], {"total_pages": 5})
        self.assertEqual(list(api.finished_games(1)), [])

    def test_bad_shape_is_an_error(self):
        with self.assertRaises(ApiError):
            QuizPleaseApi._data({"status": "error"}, "test")


if __name__ == "__main__":
    unittest.main()
