/* Statistics over a slice of team-game rows.
 *
 * Pure functions only: they take rows and return numbers, so they are unit
 * tested directly (see tests/test_stats.mjs) and reused by every view. Anything
 * that is a property of a game rather than of a selection -- percentile,
 * score %, per-round shares -- was computed once at build time and is only read
 * here.
 */

export const TOP_N = 3;

export function mean(values) {
  const numbers = values.filter(isNumber);
  if (!numbers.length) return null;
  return numbers.reduce((sum, value) => sum + value, 0) / numbers.length;
}

export function median(values) {
  const numbers = values.filter(isNumber).sort((a, b) => a - b);
  if (!numbers.length) return null;
  const middle = Math.floor(numbers.length / 2);
  return numbers.length % 2 ? numbers[middle] : (numbers[middle - 1] + numbers[middle]) / 2;
}

/* Sample standard deviation; undefined for a single game, which is honest --
 * one game says nothing about consistency. */
export function stdev(values) {
  const numbers = values.filter(isNumber);
  if (numbers.length < 2) return null;
  const average = mean(numbers);
  const variance = numbers.reduce((sum, value) => sum + (value - average) ** 2, 0) / (numbers.length - 1);
  return Math.sqrt(variance);
}

export function isNumber(value) {
  return typeof value === 'number' && Number.isFinite(value);
}

/* Least-squares slope of y over its own index, in units per game. Used for the
 * improving/declining read; null when there is nothing to fit. */
export function trendSlope(values) {
  const points = values.map((value, index) => [index, value]).filter(([, value]) => isNumber(value));
  if (points.length < 3) return null;
  const meanX = mean(points.map(([x]) => x));
  const meanY = mean(points.map(([, y]) => y));
  let numerator = 0;
  let denominator = 0;
  for (const [x, y] of points) {
    numerator += (x - meanX) * (y - meanY);
    denominator += (x - meanX) ** 2;
  }
  return denominator ? numerator / denominator : null;
}

export function rollingMean(values, window) {
  const output = [];
  const buffer = [];
  for (const value of values) {
    buffer.push(isNumber(value) ? value : null);
    if (buffer.length > window) buffer.shift();
    output.push(mean(buffer));
  }
  return output;
}

/* The summary a team's dashboard leads with. Every field tolerates missing
 * values: a game with no recorded position still counts as a game played. */
export function summarize(rows) {
  const totals = rows.map((row) => row.total);
  const positions = rows.map((row) => row.position).filter(isNumber);
  const percentiles = rows.map((row) => row.percentile);
  const scorePcts = rows.map((row) => row.score_pct);

  const wins = rows.filter((row) => row.position === 1).length;
  const topN = rows.filter((row) => isNumber(row.position) && row.position <= TOP_N).length;
  const ranked = positions.length;

  const recent = rows.slice(-5);
  const earlier = rows.slice(0, Math.floor(rows.length / 2));
  const later = rows.slice(Math.ceil(rows.length / 2));

  return {
    games: rows.length,
    ranked,
    avgScore: mean(totals),
    medianScore: median(totals),
    bestScore: totals.filter(isNumber).length ? Math.max(...totals.filter(isNumber)) : null,
    worstScore: totals.filter(isNumber).length ? Math.min(...totals.filter(isNumber)) : null,
    avgPosition: mean(positions),
    bestPosition: ranked ? Math.min(...positions) : null,
    worstPosition: ranked ? Math.max(...positions) : null,
    wins,
    topN,
    winRate: ranked ? wins / ranked : null,
    topNRate: ranked ? topN / ranked : null,
    avgPercentile: mean(percentiles),
    avgScorePct: mean(scorePcts),
    consistency: stdev(scorePcts),
    recentPercentile: mean(recent.map((row) => row.percentile)),
    recentGames: recent.length,
    trend: trendSlope(percentiles),
    // A blunter second read on direction: the two halves of the history.
    firstHalfPercentile: mean(earlier.map((row) => row.percentile)),
    secondHalfPercentile: mean(later.map((row) => row.percentile)),
    firstDate: rows.length ? rows[0].date : null,
    lastDate: rows.length ? rows[rows.length - 1].date : null,
    fieldSize: mean(rows.map((row) => row.game && row.game.teams_count)),
  };
}

/* One summary row per quiz format. `level` picks the grain: 'family' groups
 * the site's format families ([music party] and its fifteen editions as one),
 * 'type' keeps every individual format name. */
export function byGameType(rows, level = 'family') {
  const pick = level === 'type'
    ? (game) => game.game_type
    : (game) => game.game_family || game.game_type;

  const groups = new Map();
  for (const row of rows) {
    const key = (row.game && pick(row.game)) || '—';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  }
  return [...groups.entries()]
    .map(([gameType, groupRows]) => ({
      gameType,
      rows: groupRows,
      variants: new Set(groupRows.map((row) => row.game.game_type)).size,
      ...summarize(groupRows),
    }))
    .sort((a, b) => (b.avgPercentile ?? -1) - (a.avgPercentile ?? -1) || b.games - a.games);
}

/* Round-level view of one game for the selected team, against the field. */
export function roundBreakdown(row, fieldRows) {
  const game = row.game;
  const labels = game.round_labels || [];
  return labels.map((label, index) => {
    const others = fieldRows.map((other) => other.rounds[index]).filter(isNumber);
    const value = row.rounds[index];
    return {
      label,
      index,
      value: isNumber(value) ? value : null,
      share: isNumber(row.round_pct[index]) ? row.round_pct[index] : null,
      fieldMean: mean(others),
      fieldBest: others.length ? Math.max(...others) : null,
    };
  });
}

/* Cumulative score after each round, and the position that would imply, for
 * the team and for the whole field. Rounds with no data for a team are carried
 * forward rather than treated as zero. */
export function cumulativeProgress(fieldRows, roundCount) {
  const series = fieldRows.map((row) => {
    const totals = [];
    let running = 0;
    for (let index = 0; index < roundCount; index += 1) {
      const value = row.rounds[index];
      if (isNumber(value)) running += value;
      totals.push(running);
    }
    return { row, totals };
  });

  for (let index = 0; index < roundCount; index += 1) {
    const ordered = [...series].sort((a, b) => b.totals[index] - a.totals[index]);
    let position = 0;
    let previous = null;
    ordered.forEach((entry, order) => {
      if (previous === null || entry.totals[index] < previous) {
        position = order + 1;                  // ties share a position
        previous = entry.totals[index];
      }
      entry.positions = entry.positions || [];
      entry.positions[index] = position;
    });
  }
  return series;
}

/* Games where two or more of the selected teams both played. */
export function headToHead(rowsByTeam) {
  const games = new Map();
  for (const [teamKey, rows] of rowsByTeam) {
    for (const row of rows) {
      if (!games.has(row.game_id)) games.set(row.game_id, { game: row.game, entries: new Map() });
      games.get(row.game_id).entries.set(teamKey, row);
    }
  }
  return [...games.values()]
    .filter((entry) => entry.entries.size > 1)
    .sort((a, b) => (a.game.date || '').localeCompare(b.game.date || ''));
}

export function formatNumber(value, digits = 1) {
  if (!isNumber(value)) return '—';
  const rounded = Number(value.toFixed(digits));
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(digits);
}

export function formatPercent(value, digits = 0) {
  return isNumber(value) ? `${(value * 100).toFixed(digits)}%` : '—';
}

export function formatDate(iso) {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' });
}
