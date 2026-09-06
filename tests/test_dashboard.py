import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard.analytics import annotate_game, percentile_of, round_maxima
from dashboard.build import build_all, build_city, discover_cities
from dashboard.model import build_dataset, game_from_record, season_of, team_key


def record(game_id, date, title="Квиз, плиз! BAKU", template="Квиз, плиз!", results=None,
           city="Баку", slug="baku"):
    return {
        "id": game_id,
        "date": date,
        "title": title,
        "game_number": "1",
        "city": {"id": 158, "name": city, "slug": slug},
        "country": {"id": 30, "name": "Азербайджан"},
        "place": {"id": 1, "title": "Paulaner", "address": "Baku"},
        "template": {"id": 1, "title": template, "level": "medium"},
        "format": {"Тема": "обо всём", "Рейтинг": "классика", "Сложность": "нормальная"},
        "league": "классика",
        "price": 15,
        "currency": "₼",
        "url": "https://baku.quizplease.com/game/%s" % game_id,
        "results": results or [],
    }


def result(place, team, total, rounds, team_id=1, rank="chuck", rank_title="Чак Норрис"):
    return {
        "place": place, "team": team, "team_id": team_id, "total": total,
        "rank": rank, "rank_title": rank_title,
        "rounds": {("round_%d" % (index + 1)): value for index, value in enumerate(rounds)},
    }


class ModelTest(unittest.TestCase):
    def test_team_key_groups_spelling_drift(self):
        self.assertEqual(team_key(" Колобки "), team_key("колобки"))
        self.assertEqual(team_key("Ванси\t"), team_key("Ванси"))
        self.assertEqual(team_key("Team Rocket."), team_key("team rocket"))
        self.assertEqual(team_key(None), "")

    def test_season_names_the_year_winter_ends_in(self):
        self.assertEqual(season_of("2026-09-04T19:30:00"), "Осень 2026")
        self.assertEqual(season_of("2026-12-14T19:30:00"), "Зима 2027")
        self.assertEqual(season_of("2026-01-14T19:30:00"), "Зима 2026")
        self.assertIsNone(season_of(None))
        self.assertIsNone(season_of("not a date"))

    def test_variable_round_counts(self):
        game = game_from_record(record("a", "2026-07-01T19:30:00", results=[
            result(1, "A", 20, [5, 5, 10]),
            result(2, "B", 12, [4, 4, 4]),
        ]))
        self.assertEqual(game.rounds, ["round_1", "round_2", "round_3"])
        self.assertEqual(game.teams_count, 2)
        self.assertEqual(game.best_total, 20)
        self.assertEqual(game.worst_total, 12)
        self.assertEqual(game.mean_total, 16)

    def test_duplicate_rows_collapse_to_the_better_finish(self):
        game = game_from_record(record("a", "2026-07-01T19:30:00", results=[
            result(3, "Колобки", 30, [10], team_id=1),
            result(1, "колобки", 40, [20], team_id=2),   # same team, better row
            result(2, "Другая", 35, [15], team_id=3),
        ]))
        self.assertEqual(game.teams_count, 2)
        colobki = [row for row in game.results if row.team_key == "колобки"]
        self.assertEqual(len(colobki), 1)
        self.assertEqual(colobki[0].position, 1)
        # Both league-scoped ids are kept as aliases of the one team.
        self.assertEqual(colobki[0].team_ids, [1, 2])

    def test_game_without_scoreboard_is_kept_but_empty(self):
        games = build_dataset([record("a", "2026-07-01T19:30:00")])
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0].results, [])
        self.assertEqual(games[0].teams_count, 0)
        self.assertIsNone(games[0].best_total)

    def test_dataset_is_ordered_oldest_first(self):
        games = build_dataset([
            record("b", "2026-08-01T19:30:00"),
            record("a", "2026-07-01T19:30:00"),
            record("c", "2026-09-01T19:30:00"),
        ])
        self.assertEqual([game.id for game in games], ["a", "b", "c"])

    def test_records_without_an_id_are_dropped(self):
        self.assertEqual(build_dataset([{"date": "2026-07-01T19:30:00"}]), [])


class AnalyticsTest(unittest.TestCase):
    def test_percentile_endpoints(self):
        self.assertEqual(percentile_of(1, 8), 100.0)
        self.assertEqual(percentile_of(8, 8), 0.0)
        self.assertEqual(percentile_of(2, 8), 85.71)
        self.assertEqual(percentile_of(1, 1), 100.0)     # nobody to beat
        self.assertIsNone(percentile_of(None, 8))
        self.assertIsNone(percentile_of(1, 0))

    def test_round_maxima_ignore_gaps(self):
        game = game_from_record(record("a", "2026-07-01T19:30:00", results=[
            result(1, "A", 9, [5, 4]),
            result(2, "B", 7, [3, 4]),
        ]))
        game.results[1].rounds["round_1"] = None
        self.assertEqual(round_maxima(game.results), {"round_1": 5, "round_2": 4})

    def test_annotate_adds_field_relative_metrics(self):
        game = annotate_game(game_from_record(record("a", "2026-07-01T19:30:00", results=[
            result(1, "A", 50, [30, 20]),
            result(2, "B", 25, [15, 10]),
        ])))
        winner, second = game.results
        self.assertEqual(winner.extras["percentile"], 100.0)
        self.assertEqual(winner.extras["score_pct"], 100.0)
        self.assertEqual(winner.extras["gap_to_best"], 0)
        self.assertEqual(second.extras["score_pct"], 50.0)
        self.assertEqual(second.extras["gap_to_best"], 25)
        self.assertEqual(second.extras["gap_to_mean"], -12.5)
        self.assertEqual(second.extras["round_pct"], {"round_1": 50.0, "round_2": 50.0})

    def test_annotate_is_a_no_op_without_results(self):
        game = annotate_game(game_from_record(record("a", "2026-07-01T19:30:00")))
        self.assertEqual(game.results, [])


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        self.data = os.path.join(self.root, "data")
        self.out = os.path.join(self.root, "app-data")

    def write(self, slug, records, standings=None):
        games_dir = os.path.join(self.data, slug, "games")
        os.makedirs(games_dir, exist_ok=True)
        for entry in records:
            with open(os.path.join(games_dir, "%s.json" % entry["id"]), "w", encoding="utf-8") as handle:
                json.dump(entry, handle, ensure_ascii=False)
        if standings:
            with open(os.path.join(self.data, slug, "standings.csv"), "w", encoding="utf-8") as handle:
                handle.write("team_id,team,league_id,league,league_code,position,points,games,rank_title\n")
                handle.write(standings)

    def test_discovers_cities_from_the_filesystem(self):
        self.write("baku", [record("a", "2026-07-01T19:30:00")])
        self.write("tbilisi", [record("b", "2026-07-02T19:30:00", city="Тбилиси", slug="tbilisi")])
        self.assertEqual(discover_cities(self.data), ["baku", "tbilisi"])
        self.assertEqual(discover_cities(os.path.join(self.root, "missing")), [])

    def test_city_dataset_shape(self):
        self.write("baku", [
            record("a", "2026-07-01T19:30:00", results=[
                result(1, "Колобки", 50, [30, 20], team_id=11),
                result(2, "Ванси", 25, [15, 10], team_id=12),
            ]),
            record("b", "2026-08-01T19:30:00", template="[видеоигры]", results=[
                result(1, "Ванси", 40, [40], team_id=13),
            ]),
            record("c", "2026-09-01T19:30:00"),          # no scoreboard
        ], standings="11,Колобки,1,Классический,classic,1,8395,196,Недосягаемые\n")

        entry = build_city("baku", self.data, self.out)
        with open(os.path.join(self.out, "baku.json"), encoding="utf-8") as handle:
            dataset = json.load(handle)

        self.assertEqual(entry["games"], 3)
        self.assertEqual(entry["games_with_results"], 2)
        self.assertEqual(dataset["coverage"]["games_total"], 3)
        self.assertEqual(dataset["coverage"]["games_with_results"], 2)
        self.assertEqual(dataset["city"]["name"], "Баку")

        # Filter options are discovered, never declared.
        self.assertEqual(set(dataset["filters"]["game_types"]), {"Квиз, плиз!", "[видеоигры]"})
        # Only scored games contribute filter options -- an option that matched
        # nothing but scoreboard-less games would empty every view.
        self.assertEqual(dataset["filters"]["seasons"], ["Лето 2026"])

        # Teams: discovered, ordered by games played, ids kept as aliases.
        names = [team["name"] for team in dataset["teams"]]
        self.assertEqual(names, ["Ванси", "Колобки"])
        vansi = dataset["teams"][0]
        self.assertEqual(vansi["games"], 2)
        self.assertEqual(vansi["ids"], [12, 13])

        # Round scores are aligned with the game's own round list.
        rows = {(row["game_id"], row["team"]): row for row in dataset["rows"]}
        self.assertEqual(rows[("a", "Колобки")]["rounds"], [30, 20])
        self.assertEqual(rows[("b", "Ванси")]["rounds"], [40])
        self.assertEqual(rows[("a", "Ванси")]["percentile"], 0.0)
        self.assertEqual(rows[("a", "Ванси")]["score_pct"], 50.0)

        self.assertEqual(dataset["standings"][0]["games"], 196)
        self.assertEqual(dataset["standings"][0]["team_key"], "колобки")

    def test_index_lists_every_built_city(self):
        self.write("baku", [record("a", "2026-07-01T19:30:00", results=[result(1, "A", 10, [10])])])
        self.write("tbilisi", [record("b", "2026-07-02T19:30:00", city="Тбилиси", slug="tbilisi")])
        index = build_all(self.data, self.out)
        self.assertEqual([city["slug"] for city in index["cities"]], ["baku", "tbilisi"])
        self.assertTrue(os.path.exists(os.path.join(self.out, "index.json")))

    def test_new_games_appear_on_rebuild(self):
        self.write("baku", [record("a", "2026-07-01T19:30:00", results=[result(1, "A", 10, [10])])])
        first = build_city("baku", self.data, self.out)
        self.assertEqual(first["games_with_results"], 1)

        self.write("baku", [record("b", "2026-07-08T19:30:00", results=[
            result(1, "B", 12, [12]), result(2, "A", 8, [8]),
        ])])
        second = build_city("baku", self.data, self.out)
        self.assertEqual(second["games_with_results"], 2)
        self.assertEqual(second["teams"], 2)

    def test_unreadable_game_file_is_skipped(self):
        self.write("baku", [record("a", "2026-07-01T19:30:00", results=[result(1, "A", 10, [10])])])
        with open(os.path.join(self.data, "baku", "games", "broken.json"), "w", encoding="utf-8") as handle:
            handle.write("{not json")
        entry = build_city("baku", self.data, self.out)
        self.assertEqual(entry["games"], 1)


if __name__ == "__main__":
    unittest.main()
