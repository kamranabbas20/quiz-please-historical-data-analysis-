"""Client for the separate rating API behind the site's team leaderboards.

`rating-api.quizplease.com` serves the all-time standings: one entry per team
per league, with the team's lifetime points, games played and rank. It is the
only public source for a team's history before the site started publishing
per-game scoreboards, so it supplies the "196 games all-time" context next to
the handful of games that have round-level data.
"""

from .api import ApiError, QuizPleaseApi

__all__ = ["RatingApi"]

RATING_BASE_URL = "https://rating-api.quizplease.com"


class RatingApi(QuizPleaseApi):
    def __init__(self, base_url=RATING_BASE_URL, **kwargs):
        super(RatingApi, self).__init__(base_url=base_url, **kwargs)

    @staticmethod
    def _result(payload, path):
        if not isinstance(payload, dict) or "result" not in payload:
            raise ApiError("unexpected response shape from %s" % path)
        return payload["result"]

    def leagues(self, city_slug):
        """The rating tables a city runs (classic, film & music, streams, ...)."""
        return self._result(self.get("/api/external/rating", {"city": city_slug}), "leagues")

    def standings_page(self, city_slug, league_id, page=1, per_page=100, by_season=False):
        payload = self.get("/api/external/team", {
            "city": city_slug,
            "rating": league_id,
            "bySeason": "true" if by_season else "false",
            "page": page,
            "perPage": per_page,
            "order": "points",
            "orderBy": "desc",
        })
        return self._result(payload, "standings"), payload.get("totalCount") or 0

    def standings(self, city_slug, league_id, per_page=100, by_season=False):
        """Every team in one league's table for a city, best first.

        Only the first page reports `totalCount`; later pages send 0, so the
        total is remembered from the first response.
        """
        page = 1
        seen = 0
        total = None
        while True:
            teams, reported = self.standings_page(city_slug, league_id, page, per_page, by_season)
            if total is None:
                total = reported
            if not teams:
                return
            for team in teams:
                yield team
            seen += len(teams)
            if total and seen >= total:
                return
            page += 1

    def city_standings(self, city_slug, per_page=100):
        """All leagues at once, flattened, with the league attached to each row."""
        rows = []
        for league in self.leagues(city_slug):
            if not league.get("is_loaded"):
                continue    # the league exists but has no table for this city
            for team in self.standings(city_slug, league["id"], per_page=per_page):
                rank = team.get("rank") or {}
                rows.append({
                    "team_id": team.get("id"),
                    "team": (team.get("title") or "").strip(),
                    "league_id": league["id"],
                    "league": league.get("title"),
                    "league_code": league.get("code"),
                    "position": team.get("index"),
                    "points": team.get("points"),
                    "games": team.get("games"),
                    "rank_title": rank.get("title") if isinstance(rank, dict) else None,
                })
        return rows
