"""Scraper for Quiz Please game pages (e.g. https://baku.quizplease.com/game/<uuid>).

The pages are server-rendered Nuxt, so the whole game record is already in the
`window.__NUXT__` payload -- far more complete and far less brittle than reading
the rendered HTML. Finished games also publish an .xlsx scoreboard, which this
module downloads and flattens into per-team rows.
"""

import datetime
import gzip
import io
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from .jsobj import JsParseError, parse_nuxt_payload
from .xlsx import read_first_sheet

__all__ = [
    "ScrapeError",
    "extract_nuxt_state",
    "fetch",
    "game_url",
    "normalize_game",
    "normalize_results",
    "normalize_state",
    "parse_results_table",
    "scrape_game",
    "scrape_game_from_html",
]

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 (quiz-please-historical-data-analysis)"
)

# Status codes used by the site's own game listing.
GAME_STATUS = {
    0: "draft",
    1: "registration_open",
    2: "registration_closed",
    3: "in_progress",
    4: "finished",
}

PAY_METHOD = {1: "cash", 2: "card", 3: "online"}

# `Ранг` column values in the result tables, in the site's own progression.
RANK_TITLES = {
    "novich": "Новичок",
    "sergeant": "Сержант",
    "lieutenant": "Лейтенант",
    "general": "Генерал",
    "rambo": "Рэмбо",
    "chuck": "Чак Норрис",
    "unattainable": "Недосягаемые",
    "legends1": "Легенды LVL1",
    "legends2": "Легенды LVL2",
    "legends3": "Легенды LVL3",
    "keepers1": "Хранители LVL1",
    "keepers2": "Хранители LVL2",
    "keepers3": "Хранители LVL3",
    "golds1": "Голды LVL1",
    "golds2": "Голды LVL2",
    "golds3": "Голды LVL3",
}

# The .xlsx scoreboards carry rank codes, the API carries Russian titles;
# normalise both to the same pair of fields.
RANK_CODES = dict((title, code) for code, title in RANK_TITLES.items())

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_NUXT_ASSIGNMENT = re.compile(r"window\.__NUXT__\s*=")
_TAGS = re.compile(r"<[^>]+>")


class ScrapeError(RuntimeError):
    """Raised when a page cannot be fetched or does not contain game data."""


def fetch(url, timeout=30, retries=3, binary=False):
    """GET ``url``, following redirects, with a couple of retries."""
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept-Language": "ru,en;q=0.8",
        "Accept-Encoding": "gzip",
    })
    last_error = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                if binary:
                    return raw
                charset = response.headers.get_content_charset() or "utf-8"
                return raw.decode(charset, "replace")
        except (urllib.error.URLError, OSError) as error:
            last_error = error
            if attempt == retries - 1:
                break
            import time
            time.sleep(2 ** attempt)
    raise ScrapeError("failed to fetch %s: %s" % (url, last_error))


def game_url(reference, city="baku"):
    """Accept a full game URL or a bare game id and return a full URL."""
    if reference.startswith("http://") or reference.startswith("https://"):
        return reference
    if _UUID.fullmatch(reference):
        return "https://%s.quizplease.com/game/%s" % (city, reference)
    raise ValueError("not a game URL or game id: %r" % reference)


def extract_nuxt_state(html):
    """Pull the `window.__NUXT__` state out of a rendered page."""
    match = _NUXT_ASSIGNMENT.search(html)
    if not match:
        raise ScrapeError("page contains no __NUXT__ payload")
    end = html.find("</script>", match.end())
    if end == -1:
        raise ScrapeError("unterminated __NUXT__ script block")
    payload = html[match.end():end].strip().rstrip(";")
    try:
        return parse_nuxt_payload(payload)
    except JsParseError as error:
        raise ScrapeError("could not parse __NUXT__ payload: %s" % error)


def _strip_html(value):
    if not value:
        return None
    text = value.replace("</p>", "\n").replace("<br>", "\n").replace("<br/>", "\n")
    text = _TAGS.sub("", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&laquo;", "«").replace("&raquo;", "»")
                .replace("&ndash;", "–").replace("&mdash;", "—")
                .replace("&quot;", '"').replace("&#39;", "'"))
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line) or None


def _parse_block_fields(block_html):
    """Turn the `<strong>Тема</strong>: видеоигры` block into a dict."""
    fields = {}
    for chunk in re.findall(r"<p>(.*?)</p>", block_html or "", re.S):
        match = re.match(r"\s*<strong>(.*?)</strong>\s*:?\s*(.*)", chunk, re.S)
        if not match:
            continue
        key = _strip_html(match.group(1))
        value = _strip_html(match.group(2))
        if key and value:
            fields[key.rstrip(":").strip()] = value
    return fields


def _parse_datetime(value):
    """`01.09.2026 19:30` -> ISO 8601, or None when absent/odd."""
    if not value:
        return None
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return datetime.datetime.strptime(value, fmt).isoformat()
        except ValueError:
            continue
    return None


def _number(value):
    """Scores come through as strings; keep ints as ints."""
    if value is None or value == "":
        return None
    text = str(value).strip().replace(",", ".")
    try:
        number = float(text)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def parse_results_table(rows):
    """Flatten a scoreboard sheet into per-team dicts."""
    if not rows:
        return []
    header = [(cell or "").strip() for cell in rows[0]]
    round_columns = [(index, name) for index, name in enumerate(header)
                     if re.match(r"^Раунд\s*\d+$", name)]

    def column(*titles):
        for title in titles:
            if title in header:
                return header.index(title)
        return None

    place_column = column("Место")
    rank_column = column("Ранг")
    team_column = column("Название команды", "Команда")
    total_column = column("Итого", "Сумма")

    results = []
    for row in rows[1:]:
        name = (row[team_column] or "").strip() if team_column is not None else ""
        if not name:
            continue
        rank = (row[rank_column] or "").strip() if rank_column is not None else None
        rounds = {}
        for index, title in round_columns:
            rounds[title.replace("Раунд ", "round_")] = _number(row[index])
        results.append({
            "place": _number(row[place_column]) if place_column is not None else None,
            "team": name,
            "team_id": None,   # the .xlsx scoreboards do not carry team ids
            "rank": rank or None,
            "rank_title": RANK_TITLES.get(rank),
            "total": _number(row[total_column]) if total_column is not None else None,
            "rounds": rounds,
        })
    results.sort(key=lambda item: (item["place"] is None, item["place"]))
    return results


def normalize_game(game, url, city=None, country=None):
    """Reduce a raw game record to the fields worth keeping.

    ``game`` is the object returned by ``/api/games/view/{id}`` -- the same
    object the game page embeds. ``city``/``country`` supply the human-readable
    names the record itself only references by id.
    """
    if not game:
        raise ScrapeError("no game record to normalize")

    city = city or {}
    country = country or {}
    place = game.get("place") or {}
    template = game.get("template") or {}
    result = game.get("result") or {}

    # Older games carry the format table only on their template, newer ones
    # override it per game; merge so every game exposes the same keys.
    fields = _parse_block_fields(template.get("block"))
    fields.update(_parse_block_fields(game.get("block_with_text")))

    return {
        "id": game.get("id"),
        "url": url,
        "title": game.get("title"),
        "game_number": game.get("game_number"),
        "full_title": " ".join(part for part in [game.get("title"), game.get("game_number")] if part),
        "package_number": game.get("package_number"),
        "date": _parse_datetime(game.get("date")),
        "date_raw": game.get("date"),
        "registration_opens": _parse_datetime(game.get("date_open_registration")),
        "status": GAME_STATUS.get(game.get("status"), game.get("status")),
        "status_code": game.get("status"),
        "is_championship": game.get("is_championship"),
        "city": {
            "id": _number(city.get("id")) or game.get("city_id"),
            "name": city.get("name") or city.get("title"),
            "slug": city.get("slug"),
        },
        "country": {
            "id": _number(country.get("id")) or game.get("country_id"),
            "name": country.get("name") or country.get("title"),
        },
        "place": {
            "id": place.get("id"),
            "title": place.get("title"),
            "address": place.get("address"),
            "lat": place.get("lat"),
            "lon": place.get("lon"),
        },
        "price": _number(game.get("current_price")) or _number(game.get("price")),
        "currency": country.get("currency") if isinstance(country.get("currency"), str) else None,
        "pay_method": PAY_METHOD.get(game.get("pay_method"), game.get("pay_method")),
        "template": {
            "id": template.get("id"),
            "title": template.get("title"),
            "level": template.get("game_level"),
        },
        "format": fields,
        "league": fields.get("Рейтинг"),
        "game_type_code": game.get("game_type"),
        "description": _strip_html(game.get("description")),
        "welcome_inscription": game.get("welcome_inscription"),
        "teams_registered": game.get("team_count"),
        "teams_came": game.get("came_teams"),
        "people_registered": game.get("all_registered_peoples") or game.get("people_count"),
        "people_came": game.get("came_peoples"),
        "max_participants_per_team": game.get("max_participants"),
        "results_table_url": result.get("table"),
        "photos": result.get("photos") or [],
        "created_at": _parse_datetime(game.get("created_at")),
    }


def normalize_state(state, url):
    """Normalize a game straight from a page's `window.__NUXT__` state."""
    game = (state.get("data", {}).get("game") or {}).get("data")
    location = state.get("pinia", {}).get("location", {}) or {}
    city = dict(location.get("city") or {})
    country = dict(location.get("country") or {})
    city.setdefault("name", location.get("cityTitle"))
    city.setdefault("slug", location.get("citySlug"))
    country.setdefault("name", location.get("countryTitle"))
    country.setdefault("currency", location.get("currencyMode"))
    return normalize_game(game, url, city=city, country=country)


def normalize_results(rows):
    """Normalize `/api/games/{id}/results` rows into scoreboard entries."""
    results = []
    for row in rows or []:
        team = row.get("team") or {}
        rank = row.get("rank") or {}
        rank_title = rank.get("title") if isinstance(rank, dict) else rank
        rounds = {}
        for name, value in (row.get("rounds") or {}).items():
            key = "round_%s" % name if not str(name).startswith("round_") else str(name)
            rounds[key] = _number(value)
        results.append({
            "place": _number(row.get("place")),
            "team": (team.get("title") or "").strip() or None,
            "team_id": team.get("id"),
            "rank": RANK_CODES.get(rank_title),
            "rank_title": rank_title,
            "total": _number(row.get("total")),
            "rounds": rounds,
        })
    results.sort(key=lambda item: (item["place"] is None, item["place"]))
    return results


def scrape_game(reference, city="baku", with_results=True, keep_raw=False):
    """Scrape one game page into a dict ready to serialise."""
    url = game_url(reference, city=city)
    state = extract_nuxt_state(fetch(url))
    record = normalize_state(state, url)

    record["results"] = []
    if with_results and record.get("results_table_url"):
        workbook = fetch(record["results_table_url"], binary=True)
        record["results"] = parse_results_table(read_first_sheet(io.BytesIO(workbook)))

    record["scraped_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if keep_raw:
        record["raw"] = state.get("data", {}).get("game", {}).get("data")
    return record


def scrape_game_from_html(html, url, results_xlsx=None):
    """Same as :func:`scrape_game` but from already-downloaded bytes.

    Useful for tests and for re-processing an archived copy of a page.
    """
    record = normalize_state(extract_nuxt_state(html), url)
    record["results"] = []
    if results_xlsx is not None:
        record["results"] = parse_results_table(read_first_sheet(io.BytesIO(results_xlsx)))
    record["scraped_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return record


def results_to_csv_rows(record):
    """Yield header + rows describing one game's scoreboard."""
    round_names = []
    for result in record.get("results", []):
        for name in result["rounds"]:
            if name not in round_names:
                round_names.append(name)
    round_names.sort(key=lambda name: int(name.split("_")[1]))

    yield RESULT_CSV_COLUMNS + round_names
    for row in result_csv_rows(record, round_names):
        yield row


RESULT_CSV_COLUMNS = [
    "game_id", "date", "city", "title", "game_number", "package_number",
    "place_title", "place", "team", "team_id", "rank", "rank_title", "total",
]

GAME_CSV_COLUMNS = [
    "game_id", "date", "city", "country", "title", "game_number", "full_title",
    "package_number", "template_title", "level", "theme", "difficulty", "format",
    "league", "venue", "address", "price", "currency", "status", "teams_registered",
    "teams_came", "people_registered", "team_count_with_results", "url",
]


def round_names_of(records):
    """Round column names across one or more games, ordered numerically."""
    if isinstance(records, dict):
        records = [records]
    names = set()
    for record in records:
        for result in record.get("results", []):
            names.update(result["rounds"])
    return sorted(names, key=lambda name: int(name.split("_")[1]))


def result_csv_rows(record, round_names):
    """Scoreboard rows for one game, each carrying the game's identity."""
    for result in record.get("results", []):
        yield [
            record["id"], record["date"], record["city"]["name"], record["title"],
            record["game_number"], record["package_number"],
            record["place"]["title"], result["place"], result["team"],
            result["team_id"], result["rank"], result["rank_title"], result["total"],
        ] + [result["rounds"].get(name) for name in round_names]


def game_csv_row(record):
    """One row describing a game, without its scoreboard."""
    fields = record.get("format") or {}
    return [
        record["id"], record["date"], record["city"]["name"], record["country"]["name"],
        record["title"], record["game_number"], record["full_title"],
        record["package_number"], record["template"]["title"], record["template"]["level"],
        fields.get("Тема"), fields.get("Сложность"), fields.get("Формат"),
        record.get("league"), record["place"]["title"], record["place"]["address"], record["price"],
        record["currency"], record["status"], record["teams_registered"],
        record["teams_came"], record["people_registered"], len(record.get("results", [])),
        record["url"],
    ]


def dumps(record):
    return json.dumps(record, ensure_ascii=False, indent=2, sort_keys=False)
