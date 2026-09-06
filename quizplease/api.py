"""Thin client for the public api.quizplease.com endpoints the site itself uses.

The site's own bundles call these routes, so they return exactly what the
pages render:

    GET /api/cities/short                     -> every city, with country/currency
    GET /api/games/finished/{city_id}         -> paginated list of finished games
    GET /api/games/view/{game_id}             -> the full record for one game
    GET /api/games/{game_id}/results          -> scoreboard, including team ids

The scoreboard endpoint is preferable to the published .xlsx: it carries team
ids, which is what makes it possible to follow a team across games.
"""

import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request

__all__ = ["ApiError", "QuizPleaseApi"]

BASE_URL = "https://api.quizplease.com"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 (quiz-please-historical-data-analysis)"
)


class ApiError(RuntimeError):
    """Raised when an endpoint fails or answers with something unexpected."""


class QuizPleaseApi(object):
    def __init__(self, base_url=BASE_URL, timeout=40, retries=4, delay=0.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.delay = delay  # polite pause between calls, in seconds

    # -- plumbing --------------------------------------------------------
    def get(self, path, params=None):
        url = "%s/%s" % (self.base_url, path.lstrip("/"))
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        request = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        })

        last_error = None
        for attempt in range(self.retries):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
                    if response.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.decompress(raw)
                payload = json.loads(raw.decode("utf-8"))
                if self.delay:
                    time.sleep(self.delay)
                return payload
            except urllib.error.HTTPError as error:
                # 4xx other than rate limiting will not get better on a retry.
                if error.code < 500 and error.code != 429:
                    raise ApiError("%s -> HTTP %d" % (url, error.code))
                last_error = error
            except (urllib.error.URLError, OSError, ValueError) as error:
                last_error = error
            if attempt < self.retries - 1:
                time.sleep(2 ** attempt)
        raise ApiError("%s failed after %d attempts: %s" % (url, self.retries, last_error))

    @staticmethod
    def _data(payload, path):
        if not isinstance(payload, dict) or "data" not in payload:
            raise ApiError("unexpected response shape from %s" % path)
        return payload["data"]

    # -- endpoints -------------------------------------------------------
    def cities(self):
        """Every city the site knows about (id/slug/country/currency)."""
        data = self._data(self.get("/api/cities/short"), "cities")
        if isinstance(data, dict):          # the endpoint nests one level
            data = data.get("data") or []
        return data

    def city_by_slug(self, slug):
        slug = slug.lower()
        for city in self.cities():
            if (city.get("slug") or "").lower() == slug:
                return city
        raise ApiError("no city with slug %r" % slug)

    def finished_games_page(self, city_id, page=1, per_page=100):
        payload = self.get("/api/games/finished/%s" % city_id, {
            "per_page": per_page,
            "order": "-date",
            "page": page,
        })
        data = self._data(payload, "finished games")
        return data.get("data") or [], data.get("pagination") or {}

    def finished_games(self, city_id, per_page=100, max_pages=None):
        """Yield every finished game for a city, newest first."""
        page = 1
        while True:
            games, pagination = self.finished_games_page(city_id, page, per_page)
            for game in games:
                yield game
            total_pages = pagination.get("total_pages") or 1
            if max_pages:
                total_pages = min(total_pages, max_pages)
            if page >= total_pages or not games:
                return
            page += 1

    def game(self, game_id):
        """The full record for one game -- same object the game page renders."""
        return self._data(self.get("/api/games/view/%s" % game_id), "game view")

    def results(self, game_id):
        """Scoreboard rows for a finished game; empty when none was published."""
        try:
            data = self._data(self.get("/api/games/%s/results" % game_id), "game results")
        except ApiError:
            return []
        return data.get("results") or []
