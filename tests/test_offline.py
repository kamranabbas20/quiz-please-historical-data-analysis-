"""Offline tests: exercise parsing/normalising/dedup/export without network."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qpscrape import extract, game_page, normalize, scrape, validate  # noqa: E402

UUID_A = "01a04287-57c0-70c9-a6db-e2a8f574355e"
UUID_B = "11b04287-57c0-70c9-a6db-e2a8f574999f"

PAGE_JSONLD = """
<html><head>
<meta property="og:title" content="Квиз, плиз! BAKU #128">
<meta property="og:image" content="/img/128.jpg">
<script type="application/ld+json">
{"@type":"Event","name":"Квиз, плиз! BAKU #128","startDate":"2025-04-12T19:00:00+04:00",
 "description":"Классическая игра","image":["https://cdn.q/1.jpg"],
 "location":{"@type":"Place","name":"Bar Baku","address":{"streetAddress":"ул. Низами 12","addressLocality":"Баку"}},
 "offers":{"price":"15","priceCurrency":"AZN"}}
</script></head>
<body><a href="/game/%s">игра</a><a href="/results/128">Результаты</a>
<p>Стоимость: 15 AZN</p><p>Начало: 19:00</p></body></html>
""" % UUID_A

PAGE_HYDRATION = """
<html><head><script>window.__NUXT__={"data":[{"game":{"id":"%s",
"title":"Новогодний квиз","gameNumber":"12.3","date":"2023-12-28","time":"20:00",
"place":{"name":"Loft Cafe","address":"пр. Азадлыг 5"},"price":20,"currency":"AZN",
"type":"Новый год","rounds":7,"duration":"2 часа 30 мин","difficulty":"средняя",
"status":"finished","image":"/img/ny.jpg","description":"Праздничная игра"}}]};</script>
</head><body></body></html>
""" % UUID_B


def test_jsonld_page():
    rec = game_page.parse(PAGE_JSONLD, f"https://baku.quizplease.com/game/{UUID_A}")
    row = scrape._normalise(rec, {})
    assert row["game_id"] == UUID_A, row["game_id"]
    assert row["date"] == "2025-04-12", row["date"]
    assert row["start_time"] == "19:00", row["start_time"]
    assert row["venue"] == "Bar Baku", row["venue"]
    assert "Низами" in (row["venue_address"] or ""), row["venue_address"]
    assert row["price"] == 15.0 and row["currency"] == "AZN", row
    assert row["results_url"].endswith("/results/128"), row["results_url"]
    return row


def test_hydration_page():
    rec = game_page.parse(PAGE_HYDRATION, f"https://baku.quizplease.com/game/{UUID_B}")
    row = scrape._normalise(rec, {})
    assert row["game_id"] == UUID_B, row["game_id"]
    assert row["date"] == "2023-12-28", row["date"]
    assert row["start_time"] == "20:00", row["start_time"]
    assert row["venue"] == "Loft Cafe", row["venue"]
    assert row["price"] == 20.0, row["price"]
    assert row["rounds"] == 7 and row["duration_minutes"] == 150, row
    assert row["game_number"] == "12.3", row["game_number"]
    assert row["category"] == "Новый год", row["category"]
    return row


def test_api_node_projection():
    body = {"data": {"items": [
        {"id": UUID_A, "name": "Игра A", "date": "2024-06-01", "time": "19:30",
         "place": "Bar X", "address": "Street 1", "price": "18 AZN"},
    ], "hasMore": False}}
    nodes = extract.find_game_nodes(body)
    assert len(nodes) == 1, nodes
    row = scrape._normalise(game_page.from_node(nodes[0]), {})
    assert row["date"] == "2024-06-01" and row["price"] == 18.0, row
    return row


def test_dedupe_and_export(tmp: Path):
    a = test_jsonld_page()
    b = test_hydration_page()
    dup = dict(a)
    dup["venue"] = None
    dup["difficulty"] = "лёгкая"
    rows, removed = scrape._dedupe([a, b, dup])
    assert len(rows) == 2, rows
    assert len(removed) == 1 and removed[0]["reason"] == "duplicate game_id", removed
    assert rows[0]["difficulty"] == "лёгкая", "duplicate should backfill empty fields"
    scrape._export(rows, tmp / "baku_quizplease_history.csv",
                   tmp / "baku_quizplease_history.json")
    (tmp / "duplicates_removed.json").write_text(json.dumps(removed), encoding="utf-8")
    data = json.loads((tmp / "baku_quizplease_history.json").read_text(encoding="utf-8"))
    assert set(data[0]) == set(scrape.COLUMNS), set(data[0]) ^ set(scrape.COLUMNS)
    return tmp


def test_normalizers():
    assert normalize.norm_date("12 сентября 2024") == "2024-09-12"
    assert normalize.norm_date("05.03.2024") == "2024-03-05"
    assert normalize.norm_date(1712937600) == "2024-04-12"
    assert normalize.norm_date("30 fevral 2024") is None, "invalid date must be rejected"
    assert normalize.norm_time("Начало в 19.30") == "19:30"
    assert normalize.norm_price("20 ₼") == (20.0, "AZN")
    assert normalize.norm_duration_minutes("2 часа") == 120
    assert normalize.norm_game_number("Игра #45") == "45"


def test_game_link_harvest():
    html = f'<a href="/game/{UUID_A}">x</a><a href="https://baku.quizplease.com/game/{UUID_B}?a=1">y</a>'
    links = extract.game_links(html, "https://baku.quizplease.com/schedule-past")
    assert len(links) == 2, links


if __name__ == "__main__":
    import tempfile

    test_normalizers()
    test_game_link_harvest()
    test_jsonld_page()
    test_hydration_page()
    test_api_node_projection()
    with tempfile.TemporaryDirectory() as td:
        out = test_dedupe_and_export(Path(td))
        print(validate.report(out / "baku_quizplease_history.json",
                              out / "duplicates_removed.json"))
    print("\nALL OFFLINE TESTS PASSED")
