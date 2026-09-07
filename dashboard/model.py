"""The internal data model the dashboard is built on.

Everything downstream (the build step, the browser app) sees these two record
types and nothing of the scraper's shape, so a new source only has to produce
them. Scraped games vary -- different round counts, missing scoreboards, teams
that carry one id per rating league -- and this module is where that is made
uniform.

Two vocabulary notes, because the source overloads both words:

* `place` in the source is a venue in a game record and a finishing position in
  a scoreboard row. Here they are `venue` and `position`.
* A team has one id *per league*, so ids do not identify a team across formats.
  Teams are keyed by their normalised name, with the ids kept as aliases.
"""

import datetime
import re
import unicodedata

__all__ = [
    "SEASONS", "Game", "TeamGame", "assign_families", "build_dataset",
    "format_candidate", "season_of", "team_key",
]

_WHITESPACE = re.compile(r"\s+")

# Northern-hemisphere meteorological seasons, which is how the site names its
# season-opening games (ОТКРЫТИЕ ОСЕННЕГО СЕЗОНА and friends).
SEASONS = {
    12: "Зима", 1: "Зима", 2: "Зима",
    3: "Весна", 4: "Весна", 5: "Весна",
    6: "Лето", 7: "Лето", 8: "Лето",
    9: "Осень", 10: "Осень", 11: "Осень",
}


def team_key(name):
    """A stable identity for a team name.

    Case, spacing and stray punctuation drift between games; the visible name is
    kept separately, this is only for grouping.
    """
    if not name:
        return ""
    text = unicodedata.normalize("NFKC", name).replace(" ", " ")
    text = _WHITESPACE.sub(" ", text).strip(" \t\r\n.,;:!-–—_")
    return text.casefold()


_YEAR_SUFFIX = re.compile(r"\s+(19|20)\d{2}$")


def format_candidate(title):
    """The family a format name belongs to, from the name's own shape.

    Quiz Please names formats by convention, and the convention carries the
    grouping:

    * `[music party] летние хиты` -> `[music party]` — a bracketed tag at the
      front is the format; whatever follows is that night's edition.
    * `Гарри Поттер [Хогвартс] 3 курс` -> `Гарри Поттер` — when the name starts
      with words instead, those words are the format and the bracket is the
      edition.
    * `[HELLO 2025]` -> `[HELLO]` — a bare year at the end of the tag is an
      edition marker too. `[я из 2000-х]` keeps its year: it is part of the
      name, not an edition.

    Nothing here knows any particular format's name, so a city running formats
    this one has never seen groups them the same way.
    """
    if not title:
        return ""
    text = " ".join(str(title).replace("\xa0", " ").split())

    if text.startswith("[") and "]" in text:
        tag = text[1:text.index("]")]
        return "[%s]" % _YEAR_SUFFIX.sub("", tag).strip()
    if "[" in text:
        head = text[:text.index("[")].strip()
        if head:
            return head
    return text


def _series_key(name):
    """First and last word, for spotting `ОТКРЫТИЕ <season> СЕЗОНА`-style runs."""
    words = name.split()
    return (words[0].casefold(), words[-1].casefold()) if len(words) >= 3 else None


def assign_families(titles, min_series=3):
    """Map every format name to its family, using the whole corpus.

    Two passes need to see all the names at once:

    * a name that is another name plus more words is an edition of it
      (`Квиз, плиз! Ru/Az` under `Квиз, плиз!`), and
    * three or more names sharing their first and last word are a series
      (the four `ОТКРЫТИЕ … СЕЗОНА` openings), which collapse to those two
      words.
    """
    candidates = dict((title, format_candidate(title)) for title in titles)
    distinct = sorted(set(candidates.values()), key=len)

    merged = {}
    for name in distinct:
        for shorter in distinct:
            if shorter != name and name.startswith(shorter + " "):
                merged[name] = shorter
                break

    by_series = {}
    for name in distinct:
        key = _series_key(merged.get(name, name))
        if key:
            by_series.setdefault(key, set()).add(merged.get(name, name))
    series = {}
    for members in by_series.values():
        if len(members) >= min_series:
            sample = sorted(members)[0].split()
            label = "%s %s" % (sample[0], sample[-1])
            for member in members:
                series[member] = label

    families = {}
    for title, candidate in candidates.items():
        candidate = merged.get(candidate, candidate)
        families[title] = series.get(candidate, candidate)
    return families


def season_of(iso_date):
    """`2026-01-14T19:30` -> `Зима 2026`. Winter is named for the year it ends."""
    if not iso_date:
        return None
    try:
        moment = datetime.datetime.fromisoformat(iso_date)
    except ValueError:
        return None
    year = moment.year + (1 if moment.month == 12 else 0)
    return "%s %d" % (SEASONS[moment.month], year)


class TeamGame(object):
    """One team's outcome in one game -- the grain the whole app works at."""

    __slots__ = ("game_id", "team", "team_key", "team_ids", "position", "total",
                 "rounds", "rank_title", "rank", "attributes", "extras")

    def __init__(self, game_id, team, position, total, rounds, rank=None, rank_title=None,
                 team_ids=(), attributes=None):
        self.game_id = game_id
        self.team = team
        self.team_key = team_key(team)
        self.team_ids = sorted(set(team_id for team_id in team_ids if team_id))
        self.position = position
        self.total = total
        self.rounds = rounds or {}
        self.rank = rank
        self.rank_title = rank_title
        # Per-format columns some scoreboards add (a Hogwarts house, say).
        self.attributes = attributes or {}
        self.extras = {}   # filled in by dashboard.analytics.annotate_game


class Game(object):
    """One game plus its scoreboard, with the field-level facts precomputed."""

    __slots__ = ("id", "city", "city_slug", "date", "season", "title", "game_number",
                 "game_type", "league", "theme", "difficulty", "format", "venue",
                 "address", "price", "currency", "url", "rounds", "results",
                 "teams_count", "best_total", "worst_total", "mean_total",
                 "results_source", "game_family", "lat", "lon")

    def __init__(self, **fields):
        for name in self.__slots__:
            setattr(self, name, fields.get(name))
        self.results = fields.get("results") or []
        self.rounds = fields.get("rounds") or []


def _round_names(results):
    """Round columns actually present in a scoreboard, in numeric order."""
    names = set()
    for row in results:
        names.update(row.get("rounds") or {})
    return sorted(names, key=lambda name: int(name.split("_")[1]))


def _dedupe(results):
    """Drop repeated rows for the same team, keeping the better finish.

    Duplicate scoreboard entries do occur; two rows for one team would otherwise
    double-count every average.
    """
    best = {}
    for row in results:
        key = team_key(row.get("team"))
        if not key:
            continue
        current = best.get(key)
        if current is None or _sort_key(row) < _sort_key(current):
            best[key] = row
    return list(best.values())


def _sort_key(row):
    position = row.get("place")
    total = row.get("total")
    return (position if position is not None else 10 ** 6,
            -(total if total is not None else -(10 ** 6)))


def game_from_record(record):
    """Turn one scraped game JSON into a :class:`Game`."""
    fields = record.get("format") or {}
    city = record.get("city") or {}
    venue = record.get("place") or {}
    template = record.get("template") or {}

    results = _dedupe(record.get("results") or [])
    results.sort(key=_sort_key)

    totals = [row["total"] for row in results if row.get("total") is not None]
    game = Game(
        id=record.get("id"),
        city=city.get("name"),
        city_slug=city.get("slug"),
        date=record.get("date"),
        season=season_of(record.get("date")),
        title=record.get("title"),
        game_number=record.get("game_number"),
        # The template is the quiz format ("[видеоигры]", "Квиз, плиз!");
        # the title repeats it with the city appended.
        game_type=template.get("title") or record.get("title"),
        league=record.get("league") or fields.get("Рейтинг"),
        theme=fields.get("Тема"),
        difficulty=fields.get("Сложность"),
        format=fields.get("Формат"),
        venue=venue.get("title"),
        address=venue.get("address"),
        lat=venue.get("lat"),
        lon=venue.get("lon"),
        price=record.get("price"),
        currency=record.get("currency"),
        url=record.get("url"),
        results_source=record.get("results_source"),
        rounds=_round_names(results),
        teams_count=len(results),
        best_total=max(totals) if totals else None,
        worst_total=min(totals) if totals else None,
        mean_total=round(sum(totals) / len(totals), 3) if totals else None,
    )

    # Collect ids from the rows as scraped, not the deduplicated ones: a team
    # carries one id per rating league, and dropping a duplicate row would
    # otherwise drop the alias it came with.
    ids_by_team = {}
    for row in record.get("results") or []:
        ids_by_team.setdefault(team_key(row.get("team")), set()).add(row.get("team_id"))

    game.results = [
        TeamGame(
            game_id=game.id,
            team=(row.get("team") or "").strip(),
            position=row.get("place"),
            total=row.get("total"),
            rounds=row.get("rounds") or {},
            rank=row.get("rank"),
            rank_title=row.get("rank_title"),
            team_ids=ids_by_team.get(team_key(row.get("team")), ()),
            attributes=row.get("extras"),
        )
        for row in results
    ]
    return game


def build_dataset(records):
    """Every scraped record -> games with scoreboards, oldest first.

    Games with no published scoreboard carry no team rows; they are kept so the
    app can say how much of a city's history has results at all.
    """
    games = [game_from_record(record) for record in records if record.get("id")]
    games.sort(key=lambda game: (game.date or "", game.id))

    # Families are a property of the whole catalogue, not of one game.
    families = assign_families({game.game_type for game in games if game.game_type})
    for game in games:
        game.game_family = families.get(game.game_type) or game.game_type
    return games
