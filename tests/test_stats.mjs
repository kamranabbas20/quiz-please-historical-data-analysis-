/* Unit tests for the browser-side statistics. Run: node --test tests/ */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  byGameType, cumulativeProgress, formatNumber, formatPercent, headToHead,
  mean, median, roundBreakdown, roundProfile, rollingMean, stdev, summarize, trendSlope,
} from '../app/js/stats.js';

/* A tiny fixture in the shape the build step emits. */
function game(id, date, type, teamsCount, rounds = ['round_1', 'round_2']) {
  return {
    id, date, game_type: type, teams_count: teamsCount, rounds,
    round_labels: rounds.map((_, i) => `Раунд ${i + 1}`),
    title: `${type} #${id}`, best_total: 100, mean_total: 50,
  };
}

function row(g, { position, total, percentile, score_pct, rounds = [] }) {
  return {
    game: g, game_id: g.id, date: g.date, team: 'Тест', team_key: 'тест',
    position, total, percentile, score_pct, rounds, round_pct: rounds.map(() => null),
  };
}

test('mean/median/stdev handle gaps and short series', () => {
  assert.equal(mean([2, 4, 6]), 4);
  assert.equal(mean([2, null, 6]), 4);          // nulls are skipped, not zero
  assert.equal(mean([]), null);
  assert.equal(median([3, 1, 2]), 2);
  assert.equal(median([4, 1, 3, 2]), 2.5);
  assert.equal(stdev([5]), null);               // one game says nothing
  assert.equal(Number(stdev([2, 4, 4, 4, 5, 5, 7, 9]).toFixed(4)), 2.1381);
});

test('trendSlope needs three points and signs correctly', () => {
  assert.equal(trendSlope([1, 2]), null);
  assert.equal(trendSlope([10, 20, 30]), 10);
  assert.equal(trendSlope([30, 20, 10]), -10);
  assert.equal(trendSlope([5, 5, 5]), 0);
});

test('rollingMean uses a trailing window', () => {
  assert.deepEqual(rollingMean([1, 2, 3, 4], 2), [1, 1.5, 2.5, 3.5]);
  assert.deepEqual(rollingMean([], 3), []);
});

test('summarize computes the headline stats', () => {
  const g1 = game('a', '2026-01-01T19:00', 'Классика', 10);
  const g2 = game('b', '2026-02-01T19:00', 'Классика', 5);
  const g3 = game('c', '2026-03-01T19:00', '[кино]', 4);
  const rows = [
    row(g1, { position: 1, total: 50, percentile: 100, score_pct: 100 }),
    row(g2, { position: 3, total: 40, percentile: 50, score_pct: 80 }),
    row(g3, { position: 4, total: 30, percentile: 0, score_pct: 60 }),
  ];
  const s = summarize(rows);
  assert.equal(s.games, 3);
  assert.equal(s.wins, 1);
  assert.equal(s.topN, 2);                       // 1st and 3rd
  assert.equal(s.winRate, 1 / 3);
  assert.equal(s.topNRate, 2 / 3);
  assert.equal(s.avgPosition, 8 / 3);
  assert.equal(s.bestPosition, 1);
  assert.equal(s.avgScore, 40);
  assert.equal(s.medianScore, 40);
  assert.equal(s.bestScore, 50);
  assert.equal(s.worstScore, 30);
  assert.equal(s.avgPercentile, 50);
  assert.equal(s.trend, -50);                    // declining
  assert.equal(s.fieldSize, 19 / 3);
});

test('summarize survives a single game and missing positions', () => {
  const g = game('a', '2026-01-01T19:00', 'Классика', 6);
  const one = summarize([row(g, { position: 2, total: 30, percentile: 80, score_pct: 90 })]);
  assert.equal(one.games, 1);
  assert.equal(one.winRate, 0);
  assert.equal(one.consistency, null);           // no spread from one game
  assert.equal(one.trend, null);

  const unranked = summarize([row(g, { position: null, total: 30, percentile: null, score_pct: null })]);
  assert.equal(unranked.games, 1);
  assert.equal(unranked.ranked, 0);
  assert.equal(unranked.winRate, null);          // no games with a position: not 0%
  assert.equal(unranked.avgPosition, null);
});

test('byGameType splits and ranks formats', () => {
  const g1 = game('a', '2026-01-01T19:00', 'Классика', 10);
  const g2 = game('b', '2026-02-01T19:00', '[кино]', 10);
  const entries = byGameType([
    row(g1, { position: 1, total: 50, percentile: 100, score_pct: 100 }),
    row(g1, { position: 2, total: 45, percentile: 90, score_pct: 90 }),
    row(g2, { position: 8, total: 20, percentile: 20, score_pct: 40 }),
  ]);
  assert.equal(entries.length, 2);
  assert.equal(entries[0].gameType, 'Классика');
  assert.equal(entries[0].games, 2);
  assert.equal(entries[0].avgPercentile, 95);
  assert.equal(entries[1].avgPercentile, 20);
});

test('roundBreakdown compares a team against the field', () => {
  const g = game('a', '2026-01-01T19:00', 'Классика', 3);
  const mine = row(g, { position: 1, total: 9, percentile: 100, score_pct: 100, rounds: [5, 4] });
  mine.round_pct = [100, 80];
  const field = [mine,
    row(g, { position: 2, total: 7, rounds: [3, 4] }),
    row(g, { position: 3, total: 5, rounds: [2, 3] })];
  const breakdown = roundBreakdown(mine, field);
  assert.equal(breakdown.length, 2);
  assert.equal(breakdown[0].value, 5);
  assert.equal(breakdown[0].fieldBest, 5);
  assert.equal(breakdown[0].fieldMean, 10 / 3);
  assert.equal(breakdown[1].share, 80);
});

test('cumulativeProgress ranks after every round and shares ties', () => {
  const g = game('a', '2026-01-01T19:00', 'Классика', 3);
  const field = [
    row(g, { position: 1, total: 9, rounds: [2, 7] }),
    row(g, { position: 2, total: 8, rounds: [5, 3] }),
    row(g, { position: 3, total: 5, rounds: [2, 3] }),
  ];
  const progress = cumulativeProgress(field, 2);
  assert.deepEqual(progress[0].totals, [2, 9]);
  assert.deepEqual(progress[1].totals, [5, 8]);
  // After round 1: 5 leads, then 2 and 2 tie for second.
  assert.deepEqual(progress.map((entry) => entry.positions[0]), [2, 1, 2]);
  assert.deepEqual(progress.map((entry) => entry.positions[1]), [1, 2, 3]);
});

test('headToHead keeps only shared games, in date order', () => {
  const g1 = game('a', '2026-01-01T19:00', 'Классика', 10);
  const g2 = game('b', '2026-02-01T19:00', 'Классика', 10);
  const rowsByTeam = new Map([
    ['one', [row(g1, { position: 1, total: 1 }), row(g2, { position: 2, total: 2 })]],
    ['two', [row(g2, { position: 3, total: 3 })]],
  ]);
  const shared = headToHead(rowsByTeam);
  assert.equal(shared.length, 1);
  assert.equal(shared[0].game.id, 'b');
  assert.equal(shared[0].entries.size, 2);
});

test('roundProfile aggregates a team across rounds', () => {
  const g1 = game('a', '2026-01-01T19:00', 'Классика', 3);
  const g2 = game('b', '2026-02-01T19:00', 'Классика', 3);
  const mine1 = row(g1, { position: 1, total: 15, rounds: [5, 10] });
  mine1.round_pct = [100, 50];
  const mine2 = row(g2, { position: 2, total: 9, rounds: [3, 6] });
  mine2.round_pct = [60, 60];
  const field = {
    a: [mine1, { ...row(g1, { position: 2, total: 24, rounds: [4, 20] }), round_pct: [80, 100] }],
    b: [mine2, { ...row(g2, { position: 1, total: 15, rounds: [5, 10] }), round_pct: [100, 100] }],
  };

  const profile = roundProfile([mine1, mine2], (id) => field[id]);
  assert.equal(profile.length, 2);

  const [first, second] = profile;
  assert.equal(first.games, 2);
  assert.equal(first.avgScore, 4);              // (5 + 3) / 2
  assert.equal(first.avgShare, 80);             // (100 + 60) / 2
  assert.equal(first.bestScore, 5);
  assert.equal(first.worstScore, 3);
  assert.equal(first.wins, 1);                  // took round 1 in game a only
  assert.equal(first.winRate, 0.5);
  // 8 of the team's 24 points came from round 1.
  assert.equal(Math.round(first.pointsShare), 33);
  assert.equal(Math.round(second.pointsShare), 67);
  assert.equal(first.fieldShare, 85);           // (100+80+60+100)/4
});

test('roundProfile counts negative rounds and tolerates gaps', () => {
  const g = game('a', '2026-01-01T19:00', 'Классика', 4, ['round_1', 'round_2']);
  const mine = row(g, { position: 1, total: 2, rounds: [-3, 5] });
  mine.round_pct = [-60, 100];
  const profile = roundProfile([mine], () => [mine]);
  assert.equal(profile[0].negatives, 1);        // points can be lost in a round
  assert.equal(profile[1].negatives, 0);
  assert.equal(profile[0].avgScore, -3);

  // A round with no data anywhere simply does not appear.
  const empty = row(game('b', '2026-02-01T19:00', 'Классика', 4, ['round_1']), {
    position: 1, total: 0, rounds: [null],
  });
  empty.round_pct = [null];
  assert.equal(roundProfile([empty], () => []).length, 0);
});

test('roundProfile handles a team with no field data', () => {
  const g = game('a', '2026-01-01T19:00', 'Классика', 1);
  const mine = row(g, { position: 1, total: 8, rounds: [3, 5] });
  mine.round_pct = [100, 100];
  const profile = roundProfile([mine], () => []);
  assert.equal(profile.length, 2);
  assert.equal(profile[0].wins, 0);             // nothing to compare against
  assert.equal(profile[0].winRate, null);
  assert.equal(profile[0].fieldShare, null);
});

test('formatters degrade to an em dash rather than NaN', () => {
  assert.equal(formatNumber(52.0), '52');
  assert.equal(formatNumber(52.25, 1), '52.3');
  assert.equal(formatNumber(null), '—');
  assert.equal(formatNumber(undefined), '—');
  assert.equal(formatPercent(0.5), '50%');
  assert.equal(formatPercent(null), '—');
});
