/* Loading and indexing the dataset.
 *
 * The app knows nothing about cities, teams or quiz formats ahead of time: it
 * reads data/index.json, then one file per city, and derives every filter
 * option from what is in there. Dropping a rebuilt file in place is enough for
 * new games to appear.
 */

const DATA_DIR = 'data';
const cache = new Map();          // slug -> prepared dataset
let indexPromise = null;

/* The single-file build (dashboard/bundle.py) embeds the datasets instead of
 * fetching them, so the page works from a file:// URL or anywhere it is pasted.
 * Everything downstream is identical either way. */
const embedded = typeof window !== 'undefined' ? window.__QP_DATA__ : null;

export function loadIndex() {
  if (embedded) return Promise.resolve(embedded.index);
  if (!indexPromise) {
    indexPromise = fetchJson(`${DATA_DIR}/index.json`).catch((error) => {
      indexPromise = null;
      throw error;
    });
  }
  return indexPromise;
}

export async function loadCity(slug) {
  if (cache.has(slug)) return cache.get(slug);
  if (embedded) {
    const raw = embedded.cities[slug];
    if (!raw) throw new Error(`no embedded dataset for ${slug}`);
    const dataset = prepare(raw);
    cache.set(slug, dataset);
    return dataset;
  }
  const promise = fetchJson(`${DATA_DIR}/${encodeURIComponent(slug)}.json`).then(prepare);
  cache.set(slug, promise);
  try {
    const dataset = await promise;
    cache.set(slug, dataset);
    return dataset;
  } catch (error) {
    cache.delete(slug);
    throw error;
  }
}

async function fetchJson(url) {
  const response = await fetch(url, { cache: 'no-cache' });
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return response.json();
}

/* Index the raw file once, so every later render is a lookup rather than a scan. */
function prepare(raw) {
  const games = new Map();
  for (const game of raw.games) games.set(game.id, game);

  const rowsByTeam = new Map();
  const rowsByGame = new Map();
  for (const row of raw.rows) {
    const game = games.get(row.game_id);
    if (!game) continue;                       // a result with no game: skip it
    row.game = game;                           // denormalised for convenience
    row.date = game.date;
    if (!rowsByTeam.has(row.team_key)) rowsByTeam.set(row.team_key, []);
    rowsByTeam.get(row.team_key).push(row);
    if (!rowsByGame.has(row.game_id)) rowsByGame.set(row.game_id, []);
    rowsByGame.get(row.game_id).push(row);
  }
  for (const rows of rowsByTeam.values()) rows.sort(byDate);
  for (const rows of rowsByGame.values()) {
    rows.sort((a, b) => (a.position ?? 1e6) - (b.position ?? 1e6));
  }

  const standingsByTeam = new Map();
  for (const entry of raw.standings || []) {
    if (!standingsByTeam.has(entry.team_key)) standingsByTeam.set(entry.team_key, []);
    standingsByTeam.get(entry.team_key).push(entry);
  }

  return {
    ...raw,
    gamesById: games,
    rowsByTeam,
    rowsByGame,
    standingsByTeam,
    teamsByKey: new Map(raw.teams.map((team) => [team.key, team])),
  };
}

function byDate(a, b) {
  return (a.date || '').localeCompare(b.date || '') || (a.game_id || '').localeCompare(b.game_id || '');
}

/* The rows for one team, oldest first. */
export function teamRows(dataset, teamKey) {
  return dataset.rowsByTeam.get(teamKey) || [];
}

/* Filter options are derived from the rows in scope, with counts, so a filter
 * never offers a value that would empty the view. */
export function optionsFor(rows, pick) {
  const counts = new Map();
  for (const row of rows) {
    const value = pick(row);
    if (value === null || value === undefined || value === '') continue;
    counts.set(value, (counts.get(value) || 0) + 1);
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0]), 'ru'))
    .map(([value, count]) => ({ value, count }));
}

export function applyFilters(rows, filters) {
  return rows.filter((row) => {
    const game = row.game;
    if (filters.family && (game.game_family || game.game_type) !== filters.family) return false;
    if (filters.gameType && game.game_type !== filters.gameType) return false;
    if (filters.league && game.league !== filters.league) return false;
    if (filters.season && game.season !== filters.season) return false;
    if (filters.venue && game.venue !== filters.venue) return false;
    if (filters.from && (game.date || '') < filters.from) return false;
    if (filters.to && (game.date || '') > `${filters.to}T23:59:59`) return false;
    return true;
  });
}
