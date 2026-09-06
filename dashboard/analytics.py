"""Per-game derived metrics.

Only facts that are properties of a game itself live here -- they never change
with the dashboard's filters, so they are computed once at build time. Anything
that depends on the current selection (a team's averages, win rate, form) is
computed in the browser against the filtered slice, in `app/js/stats.js`.

The one judgement call: the source publishes no theoretical maximum score, so
"score %" is measured against the best total *actually achieved in that game*.
The winner therefore always scores 100%, and the number answers "how close to
the best team in the room", not "how close to perfect".
"""

__all__ = ["annotate_game", "percentile_of", "round_maxima"]


def percentile_of(position, teams_count):
    """Share of the field a team finished ahead of, 0..100.

    Last place is 0, first place is 100, and a one-team game is 100 by
    convention (there is nobody to beat, and 0 would read as a failure).
    """
    if not position or not teams_count or teams_count < 1:
        return None
    if teams_count == 1:
        return 100.0
    return round((teams_count - position) / (teams_count - 1) * 100, 2)


def round_maxima(results):
    """Best score achieved in each round, used as that round's reference."""
    maxima = {}
    for row in results:
        for name, value in (row.rounds or {}).items():
            if value is None:
                continue
            if name not in maxima or value > maxima[name]:
                maxima[name] = value
    return maxima


def annotate_game(game):
    """Attach field-relative metrics to every scoreboard row of one game.

    Adds, per row: `percentile`, `score_pct` (against the game's best total),
    `gap_to_best`, `gap_to_mean`, and `round_pct` (per round, against that
    round's best). Returns the game.
    """
    results = game.results
    if not results:
        return game

    best = game.best_total
    maxima = round_maxima(results)
    game_round_maxima = maxima

    for row in results:
        row_extras = {}
        row_extras["percentile"] = percentile_of(row.position, game.teams_count)

        if row.total is not None and best:
            row_extras["score_pct"] = round(row.total / best * 100, 2)
            row_extras["gap_to_best"] = round(best - row.total, 3)
        else:
            row_extras["score_pct"] = None
            row_extras["gap_to_best"] = None

        if row.total is not None and game.mean_total is not None:
            row_extras["gap_to_mean"] = round(row.total - game.mean_total, 3)
        else:
            row_extras["gap_to_mean"] = None

        round_pct = {}
        for name, value in (row.rounds or {}).items():
            top = game_round_maxima.get(name)
            if value is not None and top:
                round_pct[name] = round(value / top * 100, 2)
        row_extras["round_pct"] = round_pct

        row.extras = row_extras
    return game
