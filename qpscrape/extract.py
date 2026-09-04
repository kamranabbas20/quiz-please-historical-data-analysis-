"""Structure-agnostic extraction helpers.

The QuizPlease front end may serve its data as JSON-LD, as a hydration blob
(``__NEXT_DATA__`` / ``__NUXT__`` / ``__INITIAL_STATE__``), as a plain JSON API
response, or only as rendered DOM.  Rather than hard-coding CSS selectors that
would silently rot, every extractor here is written against *shapes* -- so the
same code path handles an API payload and an embedded blob alike.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Iterator

from bs4 import BeautifulSoup

GAME_URL_RE = re.compile(
    r"/game/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# Hydration globals used by the common SPA frameworks.
HYDRATION_PATTERNS = [
    re.compile(r"__NEXT_DATA__\s*=\s*(\{.*?\})\s*(?:;|</script>)", re.S),
    re.compile(r"window\.__NUXT__\s*=\s*(\{.*?\})\s*(?:;|</script>)", re.S),
    re.compile(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*(?:;|</script>)", re.S),
    re.compile(r"window\.__APOLLO_STATE__\s*=\s*(\{.*?\})\s*(?:;|</script>)", re.S),
    re.compile(r"window\.__data\s*=\s*(\{.*?\})\s*(?:;|</script>)", re.S),
]

# Field aliases seen across QuizPlease-style payloads (ru/en keys both occur).
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "game_id": ("id", "uuid", "game_id", "gameId", "guid", "hash"),
    "title": ("title", "name", "game_name", "gameName", "header", "caption"),
    "game_number": ("number", "game_number", "gameNumber", "num", "game_num",
                    "gameNum", "index"),
    "category": ("category", "type", "game_type", "gameType", "theme", "kind",
                 "package", "packet", "series", "franchise"),
    "date": ("date", "game_date", "gameDate", "start_date", "startDate",
             "datetime", "date_start", "dateStart", "event_date", "when"),
    "start_time": ("time", "start_time", "startTime", "time_start", "timeStart",
                   "begin", "hour"),
    "venue": ("place", "venue", "bar", "restaurant", "location", "place_name",
              "placeName", "venue_name", "venueName", "cafe"),
    "venue_address": ("address", "place_address", "placeAddress", "venue_address",
                      "venueAddress", "street", "full_address", "fullAddress"),
    "price": ("price", "cost", "price_per_person", "pricePerPerson", "amount",
              "ticket_price", "ticketPrice", "sum"),
    "currency": ("currency", "cur", "currency_code", "currencyCode"),
    "duration": ("duration", "length", "duration_minutes", "durationMinutes"),
    "rounds": ("rounds", "rounds_count", "roundsCount", "tours", "tours_count"),
    "difficulty": ("difficulty", "level", "complexity", "hardness"),
    "description": ("description", "text", "announcement", "about", "content",
                    "short_description", "shortDescription", "descr"),
    "status": ("status", "state", "registration_status", "registrationStatus",
               "is_active", "available", "sold_out", "soldOut"),
    "game_url": ("url", "link", "href", "game_url", "gameUrl", "page_url",
                 "permalink", "slug"),
    "results_url": ("results_url", "resultsUrl", "results", "results_link",
                    "rating_url", "ratingUrl", "protocol"),
    "image_url": ("image", "img", "photo", "picture", "preview", "image_url",
                  "imageUrl", "cover", "poster", "thumbnail", "banner"),
    "city": ("city", "city_name", "cityName", "town"),
    "latitude": ("lat", "latitude"),
    "longitude": ("lon", "lng", "longitude"),
}

# Labels used on rendered pages, for the DOM fallback path.
TEXT_LABELS: dict[str, tuple[str, ...]] = {
    "venue_address": ("адрес", "ünvan", "address"),
    "price": ("стоимость", "цена", "qiymət", "price", "cost"),
    "start_time": ("начало", "başlama", "start", "время"),
    "duration": ("длительность", "продолжительность", "duration", "davam"),
    "rounds": ("раунд", "тур", "round", "raund"),
    "difficulty": ("сложность", "çətinlik", "difficulty"),
}


def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


# ---------------------------------------------------------------------------
# JSON discovery inside a page
# ---------------------------------------------------------------------------
def json_ld_blocks(html: str) -> list[Any]:
    """Every parseable ``application/ld+json`` payload on the page."""
    out: list[Any] = []
    for tag in soup_of(html).find_all("script", type=lambda t: t and "ld+json" in t):
        raw = tag.string or tag.get_text() or ""
        try:
            out.append(json.loads(raw))
        except ValueError:
            # Some sites concatenate several objects in one tag.
            for chunk in re.findall(r"\{.*?\}(?=\s*(?:\{|$))", raw, re.S):
                try:
                    out.append(json.loads(chunk))
                except ValueError:
                    pass
    return out


def hydration_blobs(html: str) -> list[Any]:
    """State blobs left by Next/Nuxt/Vue/Apollo hydration."""
    out: list[Any] = []
    for pattern in HYDRATION_PATTERNS:
        for match in pattern.finditer(html):
            blob = _loads_balanced(match.group(1))
            if blob is not None:
                out.append(blob)
    return out


def inline_json_blobs(html: str, min_len: int = 200) -> list[Any]:
    """Large JSON literals inside plain ``<script>`` tags.

    Catches hand-rolled bootstrapping such as ``var games = [...]`` that the
    framework-specific patterns above would miss.
    """
    out: list[Any] = []
    for tag in soup_of(html).find_all("script"):
        if tag.get("type") and "json" not in tag.get("type"):
            continue
        raw = (tag.string or tag.get_text() or "").strip()
        if len(raw) < min_len:
            continue
        blob = _loads_balanced(raw)
        if blob is not None:
            out.append(blob)
            continue
        for match in re.finditer(r"[=:]\s*(\[\s*\{.*?\}\s*\]|\{.*?\})\s*[;,\n]", raw, re.S):
            if len(match.group(1)) < min_len:
                continue
            blob = _loads_balanced(match.group(1))
            if blob is not None:
                out.append(blob)
    return out


def _loads_balanced(text: str) -> Any | None:
    """json.loads, retrying on the longest balanced prefix.

    Regex capture of a JSON literal embedded in JavaScript routinely stops at
    the wrong brace; trimming to the last balanced position recovers it.
    """
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    if not text or text[0] not in "{[":
        return None
    opener, closer = ("{", "}") if text[0] == "{" else ("[", "]")
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[: i + 1])
                except ValueError:
                    return None
    return None


def walk(obj: Any) -> Iterator[Any]:
    """Yield every dict and list node in a nested structure."""
    stack = [obj]
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)


def looks_like_game(node: Any) -> bool:
    """Heuristic: a dict carrying an identifier *and* a date-ish or venue field."""
    if not isinstance(node, dict):
        return False
    keys = {k.lower() for k in node.keys() if isinstance(k, str)}
    has_id = bool(keys & {"id", "uuid", "game_id", "gameid", "guid", "hash"})
    has_when = bool(keys & {
        "date", "game_date", "gamedate", "datetime", "date_start", "datestart",
        "start_date", "startdate", "event_date",
    })
    has_where = bool(keys & {"place", "venue", "bar", "address", "place_address"})
    has_name = bool(keys & {"title", "name", "game_name", "gamename"})
    return has_id and (has_when or (has_where and has_name))


def find_game_nodes(obj: Any) -> list[dict]:
    """All game-shaped dicts anywhere inside an arbitrary JSON structure."""
    seen: list[dict] = []
    for node in walk(obj):
        if looks_like_game(node):
            seen.append(node)
    return seen


# Keys that name the scalar inside a nested object such as
# ``{"place": {"name": ..., "address": ...}}``.
SCALAR_KEYS = ("name", "title", "value", "text", "label", "url", "src")


def _scalarise(value: Any, field: str) -> Any:
    """Reduce a nested value to something printable for ``field``."""
    if isinstance(value, list):
        for item in value:
            scalar = _scalarise(item, field)
            if scalar not in (None, "", [], {}):
                return scalar
        return None
    if isinstance(value, dict):
        lowered = {k.lower(): v for k, v in value.items() if isinstance(k, str)}
        for alias in FIELD_ALIASES.get(field, ()) + SCALAR_KEYS:
            candidate = lowered.get(alias.lower())
            if isinstance(candidate, (str, int, float)) and candidate != "":
                return candidate
        return None
    return value


def pick(node: dict, field: str) -> Any:
    """Read a logical field from a dict using the alias table (case-insensitive)."""
    lowered = {k.lower(): v for k, v in node.items() if isinstance(k, str)}
    for alias in FIELD_ALIASES.get(field, ()):
        if alias.lower() in lowered:
            value = _scalarise(lowered[alias.lower()], field)
            if value not in (None, "", [], {}):
                return value
    # Nested one level: {"place": {"name": ..., "address": ...}}
    for value in node.values():
        if isinstance(value, dict):
            nested = {k.lower(): v for k, v in value.items() if isinstance(k, str)}
            for alias in FIELD_ALIASES.get(field, ()):
                if alias.lower() in nested:
                    scalar = _scalarise(nested[alias.lower()], field)
                    if scalar not in (None, "", [], {}):
                        return scalar
    return None


# ---------------------------------------------------------------------------
# DOM fallbacks
# ---------------------------------------------------------------------------
def game_links(html: str, base: str = "") -> list[str]:
    """Absolute ``/game/<uuid>`` URLs referenced anywhere in the markup."""
    from urllib.parse import urljoin

    found: dict[str, None] = {}
    for match in GAME_URL_RE.finditer(html):
        found.setdefault(urljoin(base or "https://baku.quizplease.com", match.group(0)), None)
    return list(found)


def meta_tags(html: str) -> dict[str, str]:
    """OpenGraph / twitter / name meta tags, keyed by their property name."""
    out: dict[str, str] = {}
    for tag in soup_of(html).find_all("meta"):
        key = tag.get("property") or tag.get("name") or tag.get("itemprop")
        content = tag.get("content")
        if key and content:
            out[key.lower()] = content.strip()
    return out


def labelled_values(html: str) -> dict[str, str]:
    """Values sitting next to a known label word in the rendered text.

    Deliberately crude: it is the last resort when no JSON is available.
    """
    text = soup_of(html).get_text("\n", strip=True)
    lines = [ln for ln in text.split("\n") if ln]
    out: dict[str, str] = {}
    for idx, line in enumerate(lines):
        low = line.lower()
        for field, labels in TEXT_LABELS.items():
            if field in out:
                continue
            for label in labels:
                if label not in low:
                    continue
                after = re.split(r"[:：]\s*", line, maxsplit=1)
                if len(after) == 2 and after[1].strip():
                    out[field] = after[1].strip()
                elif idx + 1 < len(lines):
                    out[field] = lines[idx + 1].strip()
                break
    return out


def tables(html: str) -> list[list[list[str]]]:
    """Every ``<table>`` as a list of rows of cell text (results protocols)."""
    out = []
    for table in soup_of(html).find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
            if any(cells):
                rows.append(cells)
        if rows:
            out.append(rows)
    return out


def uniq(items: Iterable[str]) -> list[str]:
    seen: dict[str, None] = {}
    for item in items:
        seen.setdefault(item, None)
    return list(seen)
