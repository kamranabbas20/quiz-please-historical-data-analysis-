/* The dashboard: filters, views, rendering.
 *
 * State lives in one object; every change funnels through `update()`, so the
 * KPI cards, charts and tables always describe the same slice of data. Nothing
 * here knows the name of a city, a team or a quiz format -- they all come from
 * the dataset.
 */

import { applyFilters, loadCity, loadIndex, optionsFor, teamRows } from './data.js';
import {
  SERIES, TILE_PROVIDERS, barChart, chartCard, columnChart, groupedColumns, lineChart,
  mapChart, periodBars,
} from './charts.js';
import {
  byGameType, cumulativeProgress, formatDate, formatNumber, formatPercent,
  headToHead, isNumber, mean, roundBreakdown, roundProfile, rollingMean, summarize,
} from './stats.js';

const ROLLING_WINDOW = 5;

const state = {
  index: null,
  dataset: null,
  city: null,
  team: null,
  compare: [],
  filters: {
    family: '', gameType: '', league: '', season: '', venue: '', from: '', to: '',
    includeIncomplete: false,
  },
  teamQuery: '',
  view: 'overview',
  selectedGame: null,
  sort: { games: { key: 'date', dir: 'desc' }, types: { key: 'games', dir: 'desc' } },
  typeLevel: 'family',   // 'family' groups editions together, 'type' splits them
  basemap: 'auto',       // 'auto' tries each tile provider in turn, or a name, or 'none'
};

const dom = {};

export async function start() {
  cacheDom();
  loadLogo();
  wireStaticControls();
  applyStoredTheme();
  applyStoredBasemap();

  try {
    state.index = await loadIndex();
  } catch (error) {
    dom.main.replaceChildren(message(
      'Не удалось загрузить данные',
      `${error.message}. Соберите датасет: python -m dashboard.build, затем откройте приложение через локальный сервер.`,
    ));
    return;
  }

  fillSelect(dom.city, state.index.cities.map((city) => ({
    value: city.slug,
    label: `${city.name} (${city.games_with_results} игр с результатами)`,
  })));
  const first = state.index.cities[0];
  if (!first) {
    dom.main.replaceChildren(message('Нет городов', 'В data/ нет ни одного собранного города.'));
    return;
  }
  await selectCity(first.slug);
}

/* The served app fetches the logo; the single-file build has it inlined
 * already, so this is a no-op there. */
async function loadLogo() {
  const holder = document.querySelector('.brand-logo');
  if (!holder || !holder.dataset.src || holder.childElementCount) return;
  try {
    const response = await fetch(holder.dataset.src);
    if (!response.ok) return;
    const markup = await response.text();
    const parsed = new DOMParser().parseFromString(markup, 'image/svg+xml');
    const svg = parsed.querySelector('svg');
    if (svg) holder.appendChild(document.importNode(svg, true));
  } catch {
    // No logo is better than a broken one; the text credit still names the source.
  }
}

function cacheDom() {
  for (const id of ['city', 'team', 'teamSearch', 'family', 'gameType', 'variantField',
    'league', 'season', 'venue', 'from', 'to', 'includeIncomplete', 'reset', 'main',
    'tabs', 'scopeNote', 'themeToggle', 'coverage']) {
    dom[id] = document.getElementById(id);
  }
}

function wireStaticControls() {
  dom.city.addEventListener('change', () => selectCity(dom.city.value));
  // A city can have hundreds of teams; the search narrows the dropdown rather
  // than making the reader scroll it.
  dom.teamSearch.addEventListener('input', () => {
    state.teamQuery = dom.teamSearch.value;
    fillTeamSelect();
  });
  dom.team.addEventListener('change', () => {
    state.team = dom.team.value || null;
    state.selectedGame = null;
    state.compare = state.compare.filter((key) => key !== state.team);
    refreshDependentFilters();
    update();
  });
  for (const id of ['family', 'gameType', 'league', 'season', 'venue', 'from', 'to']) {
    dom[id].addEventListener('change', () => {
      state.filters[id] = dom[id].value;
      // Picking a different family invalidates the variant chosen under the old one.
      if (id === 'family') state.filters.gameType = '';
      state.selectedGame = null;
      refreshDependentFilters();
      update();
    });
  }
  dom.reset.addEventListener('click', () => {
    state.filters = {
      family: '', gameType: '', league: '', season: '', venue: '', from: '', to: '',
      includeIncomplete: false,
    };
    state.selectedGame = null;
    syncFilterInputs();
    refreshDependentFilters();
    update();
  });
  dom.tabs.addEventListener('click', (event) => {
    const tab = event.target.closest('.tab');
    if (!tab) return;
    state.view = tab.dataset.view;
    for (const button of dom.tabs.querySelectorAll('.tab')) {
      button.setAttribute('aria-selected', String(button === tab));
    }
    update();
  });
  dom.includeIncomplete.addEventListener('change', () => {
    state.filters.includeIncomplete = dom.includeIncomplete.checked;
    state.selectedGame = null;
    refreshDependentFilters();
    update();
  });
  dom.themeToggle.addEventListener('click', toggleTheme);
}

function applyStoredBasemap() {
  try {
    const stored = localStorage.getItem('qp-basemap');
    if (stored) state.basemap = stored;
  } catch { /* private mode */ }
}

function applyStoredTheme() {
  let stored = null;
  try { stored = localStorage.getItem('qp-theme'); } catch { /* private mode */ }
  if (stored === 'dark' || stored === 'light') {
    document.documentElement.setAttribute('data-theme', stored);
  }
  updateThemeLabel();
}

function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme');
  const isDark = current
    ? current === 'dark'
    : window.matchMedia('(prefers-color-scheme: dark)').matches;
  const next = isDark ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  try { localStorage.setItem('qp-theme', next); } catch { /* ignore */ }
  updateThemeLabel();
  update();     // charts read their colours from CSS variables at draw time
}

function updateThemeLabel() {
  const current = document.documentElement.getAttribute('data-theme');
  const isDark = current
    ? current === 'dark'
    : window.matchMedia('(prefers-color-scheme: dark)').matches;
  dom.themeToggle.textContent = isDark ? '☀ Светлая тема' : '☾ Тёмная тема';
}

async function selectCity(slug) {
  if (!slug) return;
  dom.main.classList.add('loading');
  try {
    state.dataset = await loadCity(slug);
  } catch (error) {
    dom.main.classList.remove('loading');
    dom.main.replaceChildren(message('Город не загрузился', error.message));
    return;
  }
  state.city = slug;
  dom.city.value = slug;
  state.compare = [];
  state.selectedGame = null;
  state.filters = {
    family: '', gameType: '', league: '', season: '', venue: '', from: '', to: '',
    includeIncomplete: false,
  };

  // Teams depend on the city, and are discovered from its scoreboards.
  state.teamQuery = '';
  dom.teamSearch.value = '';
  const teams = state.dataset.teams;
  state.team = teams.length ? teams[0].key : null;
  fillTeamSelect();

  syncFilterInputs();
  refreshDependentFilters();
  renderCoverage();
  renderScrapedAt();
  dom.main.classList.remove('loading');
  update();
}

/* Filter options are scoped to the selected team, with counts, so no option
 * can produce an empty view. */
function refreshDependentFilters() {
  const all = state.team ? teamRows(state.dataset, state.team) : [];
  const rows = state.filters.includeIncomplete
    ? all
    : all.filter((row) => !row.game.incomplete);
  const specs = [
    ['family', (row) => row.game.game_family || row.game.game_type, 'Все форматы'],
    ['league', (row) => row.game.league, 'Все лиги'],
    ['season', (row) => row.game.season, 'Все сезоны'],
    ['venue', (row) => row.game.venue, 'Все площадки'],
  ];
  for (const [id, pick, placeholder] of specs) {
    const options = optionsFor(rows, pick);
    fillSelect(dom[id], options.map((option) => ({
      value: option.value, label: `${option.value} (${option.count})`,
    })), placeholder);
    if (state.filters[id] && !options.some((option) => option.value === state.filters[id])) {
      state.filters[id] = '';
    }
    dom[id].value = state.filters[id];
    dom[id].parentElement.hidden = options.length === 0;
  }

  // The variant list only exists inside a family, and only when there is a
  // choice to make -- most formats were run once.
  const inFamily = state.filters.family
    ? rows.filter((row) => (row.game.game_family || row.game.game_type) === state.filters.family)
    : [];
  const variants = optionsFor(inFamily, (row) => row.game.game_type);
  dom.variantField.hidden = variants.length < 2;
  if (variants.length < 2) {
    state.filters.gameType = '';
  } else {
    fillSelect(dom.gameType, variants.map((option) => ({
      value: option.value, label: `${option.value} (${option.count})`,
    })), 'Все варианты');
    if (state.filters.gameType && !variants.some((o) => o.value === state.filters.gameType)) {
      state.filters.gameType = '';
    }
    dom.gameType.value = state.filters.gameType;
  }

  const dates = rows.map((row) => (row.date || '').slice(0, 10)).filter(Boolean);
  if (dates.length) {
    dom.from.min = dates[0];
    dom.from.max = dates[dates.length - 1];
    dom.to.min = dates[0];
    dom.to.max = dates[dates.length - 1];
  }
}

/* The team dropdown, narrowed by the search box. The selected team always stays
 * in the list, so a search can never silently change what is being shown. */
function fillTeamSelect() {
  const query = state.teamQuery.trim().toLocaleLowerCase('ru');
  const teams = state.dataset.teams.filter(
    (team) => !query
      || team.key === state.team
      || team.names.some((name) => name.toLocaleLowerCase('ru').includes(query)),
  );
  fillSelect(dom.team, teams.map((team) => ({
    value: team.key,
    label: `${team.name} — ${team.games} ${plural(team.games, 'игра', 'игры', 'игр')}`,
  })), teams.length ? undefined : 'Ничего не найдено');
  if (state.team) dom.team.value = state.team;
}

function syncFilterInputs() {
  for (const id of ['family', 'gameType', 'league', 'season', 'venue', 'from', 'to']) {
    if (dom[id]) dom[id].value = state.filters[id] || '';
  }
  if (dom.includeIncomplete) dom.includeIncomplete.checked = Boolean(state.filters.includeIncomplete);
}

function renderScrapedAt() {
  const node = document.getElementById('scrapedAt');
  if (!node || !state.dataset.generated_at) return;
  node.textContent = formatDate(state.dataset.generated_at);
}

function renderCoverage() {
  const coverage = state.dataset.coverage;
  const city = state.dataset.city.name;
  dom.coverage.textContent = [
    `${city}: ${coverage.games_total} ${plural(coverage.games_total, 'игра', 'игры', 'игр')} в расписании`,
    `${coverage.games_with_results} с опубликованными результатами`,
    coverage.scored_from
      ? `${formatDate(coverage.scored_from)} — ${formatDate(coverage.scored_to)}`
      : null,
    `${coverage.teams} ${plural(coverage.teams, 'команда', 'команды', 'команд')}`,
    coverage.incomplete_games
      ? `${coverage.incomplete_games} с незаполненными раундами`
      : null,
  ].filter(Boolean).join(' · ');
}

/* ---------------------------------------------------------------- rendering */

function update() {
  if (!state.dataset) return;
  const rows = state.team ? applyFilters(teamRows(state.dataset, state.team), state.filters) : [];
  const allRows = state.team ? teamRows(state.dataset, state.team) : [];

  const dropped = state.team
    ? teamRows(state.dataset, state.team).filter((row) => row.game.incomplete).length
    : 0;
  const parts = [];
  if (state.team) {
    parts.push(`${rows.length} из ${allRows.length} ${plural(allRows.length, 'игры', 'игр', 'игр')} команды после фильтров`);
    if (dropped && !state.filters.includeIncomplete) {
      parts.push(`${dropped} ${plural(dropped, 'игра исключена', 'игры исключены', 'игр исключено')} `
        + 'из-за незаполненных раундов');
    }
  }
  dom.scopeNote.textContent = state.team ? parts.join(' · ') : 'Выберите команду';

  const views = {
    overview: renderOverview,
    games: renderGames,
    rounds: renderRounds,
    types: renderTypes,
    venues: renderVenues,
    compare: renderCompare,
  };
  const container = document.createElement('div');
  views[state.view](container, rows, allRows);
  dom.main.replaceChildren(container);
}

function renderOverview(container, rows) {
  if (!rows.length) {
    container.appendChild(message('Нет игр', 'Ни одна игра не подходит под выбранные фильтры.'));
    return;
  }
  const summary = summarize(rows);
  const team = state.dataset.teamsByKey.get(state.team);

  container.appendChild(kpiGrid(summary, team));
  container.appendChild(sectionTitle('Хронология выступлений'));

  const points = rows.map((row) => ({
    date: row.date,
    label: `${formatDate(row.date)} · ${row.game.game_type}`,
    row,
    extra: [
      { label: 'Место', value: `${row.position} из ${row.game.teams_count}` },
      { label: 'Формат', value: row.game.game_type },
    ],
  }));

  const grid = document.createElement('div');
  grid.className = 'grid charts-2';

  // Percentile is the comparable measure across formats, so it leads.
  const percentiles = rows.map((row) => row.percentile);
  const rolling = rollingMean(percentiles, ROLLING_WINDOW);
  grid.appendChild(chartCard({
    title: 'Процентиль и скользящее среднее',
    note: `Доля соперников позади. Скользящее среднее — по ${ROLLING_WINDOW} последним играм.`,
    legend: [
      { label: 'Процентиль', color: SERIES[0] },
      { label: `Среднее за ${ROLLING_WINDOW}`, color: SERIES[1] },
    ],
    render: (holder, tooltip) => lineChart(holder, tooltip, {
      points,
      yMin: 0,
      yMax: 100,
      series: [
        { label: 'Процентиль', color: SERIES[0], values: percentiles },
        { label: `Среднее за ${ROLLING_WINDOW}`, color: SERIES[1], values: rolling, markers: false },
      ],
      formatValue: (value) => formatNumber(value, 1),
      onSelect: (point) => openGame(point.row),
    }),
    table: () => simpleTable(
      ['Дата', 'Формат', 'Процентиль', `Среднее за ${ROLLING_WINDOW}`],
      rows.map((row, index) => [
        formatDate(row.date), row.game.game_type,
        formatNumber(row.percentile, 1), formatNumber(rolling[index], 1),
      ]),
    ),
  }));

  grid.appendChild(chartCard({
    title: 'Сумма баллов по играм',
    note: 'Абсолютные баллы сравнимы только внутри одного формата — форматы различаются числом раундов.',
    render: (holder, tooltip) => lineChart(holder, tooltip, {
      points,
      yMin: 0,
      series: [{ label: 'Баллы', color: SERIES[0], values: rows.map((row) => row.total) }],
      formatValue: (value) => formatNumber(value, 1),
      onSelect: (point) => openGame(point.row),
    }),
    table: () => simpleTable(
      ['Дата', 'Игра', 'Баллы', 'Лучший результат игры'],
      rows.map((row) => [
        formatDate(row.date), row.game.title,
        formatNumber(row.total, 1), formatNumber(row.game.best_total, 1),
      ]),
    ),
  }));

  grid.appendChild(chartCard({
    title: 'Место в игре',
    note: 'Ось перевёрнута: первое место сверху. Заливка — размер поля соперников.',
    render: (holder, tooltip) => lineChart(holder, tooltip, {
      points,
      invert: true,
      yMin: 1,
      yMax: Math.max(...rows.map((row) => row.game.teams_count || 1), 2),
      band: rows.map((row) => row.game.teams_count || 1),
      integerTicks: true,
      series: [{ label: 'Место', color: SERIES[0], values: rows.map((row) => row.position) }],
      formatValue: (value) => formatNumber(value, 0),
      onSelect: (point) => openGame(point.row),
    }),
    table: () => simpleTable(
      ['Дата', 'Место', 'Команд'],
      rows.map((row) => [formatDate(row.date), String(row.position ?? '—'), String(row.game.teams_count)]),
    ),
  }));

  grid.appendChild(chartCard({
    title: 'Распределение мест',
    render: (holder, tooltip) => {
      const counts = new Map();
      for (const row of rows) {
        if (!isNumber(row.position)) continue;
        counts.set(row.position, (counts.get(row.position) || 0) + 1);
      }
      const items = [...counts.entries()].sort((a, b) => a[0] - b[0]).map(([position, count]) => ({
        label: String(position),
        value: count,
        title: `${position}-е место`,
        rows: [{ label: 'Игр', value: String(count), color: SERIES[0] }],
      }));
      columnChart(holder, tooltip, { items, xLabel: 'Место в игре' });
    },
    table: () => {
      const counts = new Map();
      for (const row of rows) if (isNumber(row.position)) counts.set(row.position, (counts.get(row.position) || 0) + 1);
      return simpleTable(['Место', 'Игр'], [...counts.entries()].sort((a, b) => a[0] - b[0])
        .map(([position, count]) => [String(position), String(count)]));
    },
  }));

  grid.appendChild(chartCard({
    title: 'Распределение результата',
    note: '% от лучшего результата в той же игре.',
    render: (holder, tooltip) => {
      const bins = histogram(rows.map((row) => row.score_pct), 0, 100, 10);
      columnChart(holder, tooltip, {
        items: bins.map((bin) => ({
          label: `${bin.from}`,
          value: bin.count,
          title: `${bin.from}–${bin.to}% от лучшего`,
          rows: [{ label: 'Игр', value: String(bin.count), color: SERIES[0] }],
        })),
        xLabel: '% от лучшего результата',
      });
    },
    table: () => simpleTable(['Диапазон, %', 'Игр'],
      histogram(rows.map((row) => row.score_pct), 0, 100, 10)
        .map((bin) => [`${bin.from}–${bin.to}`, String(bin.count)])),
  }));

  const types = byGameType(rows, 'family');
  grid.appendChild(chartCard({
    title: 'Средний процентиль по форматам',
    note: formatBarsNote(types),
    render: (holder, tooltip) => barChart(holder, tooltip, {
      items: formatBars(types, (entry) => entry.avgPercentile, SERIES[0]),
      max: 100,
      formatValue: (value) => formatNumber(value, 0),
    }),
    table: () => simpleTable(['Формат', 'Игр', 'Средний процентиль'],
      types.map((entry) => [entry.gameType, String(entry.games), formatNumber(entry.avgPercentile, 1)])),
  }));

  container.appendChild(grid);
}

function kpiGrid(summary, team) {
  const grid = document.createElement('div');
  grid.className = 'grid kpis';

  const standings = (state.dataset.standingsByTeam.get(state.team) || [])
    .slice().sort((a, b) => (b.games || 0) - (a.games || 0));
  const lifetimeGames = standings.reduce((sum, entry) => sum + (entry.games || 0), 0);

  const trendCell = () => {
    if (!isNumber(summary.trend)) return { text: 'мало игр', cls: '' };
    const perGame = summary.trend;
    const arrow = perGame > 0.5 ? '▲' : perGame < -0.5 ? '▼' : '→';
    const cls = perGame > 0.5 ? 'up' : perGame < -0.5 ? 'down' : '';
    return { text: `${arrow} ${formatNumber(perGame, 2)}`, cls };
  };
  const trend = trendCell();

  const cards = [
    { label: 'Игр с результатами', value: String(summary.games),
      delta: lifetimeGames ? `${lifetimeGames} за всё время по рейтингу` : null },
    { label: 'Средний процентиль', value: formatNumber(summary.avgPercentile, 1),
      delta: `последние ${summary.recentGames}: ${formatNumber(summary.recentPercentile, 1)}` },
    { label: 'Динамика, п.п. за игру', value: trend.text, deltaClass: trend.cls,
      delta: isNumber(summary.firstHalfPercentile)
        ? `${formatNumber(summary.firstHalfPercentile, 0)} → ${formatNumber(summary.secondHalfPercentile, 0)} процентиль`
        : null },
    { label: 'Победы', value: String(summary.wins), delta: `${formatPercent(summary.winRate, 0)} игр` },
    { label: 'Топ-3', value: String(summary.topN), delta: `${formatPercent(summary.topNRate, 0)} игр` },
    { label: 'Среднее место', value: formatNumber(summary.avgPosition, 1),
      delta: `лучшее: ${formatNumber(summary.bestPosition, 0)} · поле ${formatNumber(summary.fieldSize, 1)}` },
    { label: 'Средний балл', value: formatNumber(summary.avgScore, 1),
      delta: `медиана ${formatNumber(summary.medianScore, 1)}` },
    { label: 'Лучший / худший балл',
      value: `${formatNumber(summary.bestScore, 1)} / ${formatNumber(summary.worstScore, 1)}`, small: true },
    { label: 'Стабильность', value: isNumber(summary.consistency) ? `±${formatNumber(summary.consistency, 1)}` : '—',
      delta: 'σ % от лучшего' },
  ];

  for (const card of cards) {
    const node = document.createElement('div');
    node.className = 'card kpi';
    const label = document.createElement('div');
    label.className = 'label';
    label.textContent = card.label;
    const value = document.createElement('div');
    value.className = card.small ? 'value small' : 'value';
    value.textContent = card.value;
    node.append(label, value);
    if (card.delta) {
      const delta = document.createElement('div');
      delta.className = `delta ${card.deltaClass || ''}`.trim();
      delta.textContent = card.delta;
      node.appendChild(delta);
    }
    grid.appendChild(node);
  }

  if (team && team.names.length > 1) {
    const note = document.createElement('div');
    note.className = 'card kpi';
    note.innerHTML = '<div class="label">Варианты названия</div>';
    const value = document.createElement('div');
    value.className = 'value small';
    value.textContent = team.names.join(' · ');
    note.appendChild(value);
    grid.appendChild(note);
  }
  return grid;
}

/* ------------------------------------------------------------- games view */

function renderGames(container, rows) {
  if (!rows.length) {
    container.appendChild(message('Нет игр', 'Ни одна игра не подходит под выбранные фильтры.'));
    return;
  }
  const columns = [
    { key: 'date', label: 'Дата', get: (row) => row.date, left: true,
      render: (row) => {
        if (!row.game.incomplete) return formatDate(row.date);
        const wrap = document.createElement('span');
        wrap.appendChild(document.createTextNode(`${formatDate(row.date)} `));
        const pill = document.createElement('span');
        pill.className = 'pill pill--warn';
        pill.textContent = 'неполные данные';
        pill.title = `Не заполнены: ${row.game.missing_round_labels.join(', ')}. `
          + 'Сумма и место в этой игре посчитаны по неполным данным.';
        wrap.appendChild(pill);
        return wrap;
      } },
    { key: 'type', label: 'Формат', get: (row) => row.game.game_type, render: (row) => row.game.game_type, left: true },
    { key: 'number', label: '№', get: (row) => Number(row.game.game_number) || 0, render: (row) => row.game.game_number || '—' },
    { key: 'position', label: 'Место', get: (row) => row.position ?? 1e6, render: (row) => String(row.position ?? '—') },
    { key: 'teams', label: 'Команд', get: (row) => row.game.teams_count, render: (row) => String(row.game.teams_count) },
    { key: 'total', label: 'Баллы', get: (row) => row.total ?? -1, render: (row) => formatNumber(row.total, 1) },
    { key: 'score_pct', label: '% от лучшего', get: (row) => row.score_pct ?? -1, render: (row) => formatNumber(row.score_pct, 1) },
    { key: 'percentile', label: 'Процентиль', get: (row) => row.percentile ?? -1, render: (row) => formatNumber(row.percentile, 1) },
    { key: 'venue', label: 'Площадка', get: (row) => row.game.venue || '', render: (row) => row.game.venue || '—', left: true },
    { key: 'source', label: 'Источник', get: () => 0, left: true,
      render: (row) => {
        // Every row can be checked against the page it was taken from.
        if (!row.game.url) return '—';
        const link = document.createElement('a');
        link.href = row.game.url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = 'страница игры';
        link.addEventListener('click', (event) => event.stopPropagation());
        return link;
      } },
  ];

  const sorted = sortRows(rows, columns, state.sort.games);
  const table = buildTable(columns, sorted, {
    sortState: state.sort.games,
    onSort: (key) => { toggleSort(state.sort.games, key); update(); },
    onRowClick: (row) => openGame(row),
    isSelected: (row) => state.selectedGame === row.game_id,
  });

  const wrap = document.createElement('div');
  wrap.className = 'card';
  wrap.id = 'games-table';
  const heading = document.createElement('h3');
  heading.textContent = 'Игры команды — нажмите на строку для разбора';
  wrap.append(heading, table);
  container.appendChild(wrap);

  const selected = sorted.find((row) => row.game_id === state.selectedGame) || sorted[0];
  if (selected) container.appendChild(gameDetail(selected));
}

function openGame(row) {
  state.selectedGame = row.game_id;
  state.view = 'games';
  for (const button of dom.tabs.querySelectorAll('.tab')) {
    button.setAttribute('aria-selected', String(button.dataset.view === 'games'));
  }
  update();
  requestAnimationFrame(() => {
    const detail = document.getElementById('game-detail');
    if (detail) detail.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
}

function gameDetail(row) {
  const game = row.game;
  const fieldRows = state.dataset.rowsByGame.get(game.id) || [];
  const allTeamRows = teamRows(state.dataset, state.team);
  const career = summarize(allTeamRows);
  const breakdown = roundBreakdown(row, fieldRows);
  const scored = breakdown.filter((entry) => isNumber(entry.share));
  const best = scored.length ? scored.reduce((a, b) => (a.share >= b.share ? a : b)) : null;
  const worst = scored.length ? scored.reduce((a, b) => (a.share <= b.share ? a : b)) : null;

  const section = document.createElement('section');
  section.id = 'game-detail';
  container_title(section, `Разбор игры: ${game.title} · ${formatDate(game.date)}`);

  if (game.incomplete) {
    const warn = document.createElement('div');
    warn.className = 'card empty';
    const strong = document.createElement('strong');
    strong.textContent = 'Неполные данные игры';
    const body = document.createElement('div');
    body.textContent = `На сайте не заполнены ${game.missing_round_labels.join(', ')}: `
      + 'ни одна команда не получила в них баллов. Сумма и итоговые места в этой игре '
      + 'посчитаны без них, поэтому игра не учитывается в статистике команды.';
    warn.append(strong, body);
    section.appendChild(warn);
  }

  const kpis = document.createElement('div');
  kpis.className = 'grid kpis';
  const cards = [
    { label: 'Место', value: `${row.position ?? '—'} из ${game.teams_count}` },
    { label: 'Баллы', value: formatNumber(row.total, 1), delta: `лучший в игре: ${formatNumber(game.best_total, 1)}` },
    { label: '% от лучшего', value: formatNumber(row.score_pct, 1),
      delta: `в среднем за карьеру: ${formatNumber(career.avgScorePct, 1)}` },
    { label: 'Процентиль', value: formatNumber(row.percentile, 1),
      delta: `в среднем: ${formatNumber(career.avgPercentile, 1)}` },
    { label: 'Отрыв от среднего', value: formatNumber(row.gap_to_mean, 1), delta: 'баллов к среднему по залу' },
    { label: 'Лучший раунд', value: best ? best.label : '—', small: true,
      delta: best ? `${formatNumber(best.value, 1)} (${formatNumber(best.share, 0)}% от лучшего)` : null },
    { label: 'Слабый раунд', value: worst ? worst.label : '—', small: true,
      delta: worst ? `${formatNumber(worst.value, 1)} (${formatNumber(worst.share, 0)}% от лучшего)` : null },
  ];
  for (const card of cards) {
    const node = document.createElement('div');
    node.className = 'card kpi';
    const label = document.createElement('div');
    label.className = 'label';
    label.textContent = card.label;
    const value = document.createElement('div');
    value.className = card.small ? 'value small' : 'value';
    value.textContent = card.value;
    node.append(label, value);
    if (card.delta) {
      const delta = document.createElement('div');
      delta.className = 'delta';
      delta.textContent = card.delta;
      node.appendChild(delta);
    }
    kpis.appendChild(node);
  }
  section.appendChild(kpis);

  const grid = document.createElement('div');
  grid.className = 'grid charts-2';

  grid.appendChild(chartCard({
    title: 'Раунды: команда против зала',
    legend: [
      { label: row.team, color: SERIES[0], shape: 'rect' },
      { label: 'Среднее по залу', color: SERIES[1], shape: 'rect' },
      { label: 'Лучший в раунде', color: SERIES[2], shape: 'rect' },
    ],
    render: (holder, tooltip) => groupedColumns(holder, tooltip, {
      groups: breakdown.map((entry) => ({
        label: entry.label.replace('Раунд ', 'Р'),
        values: [entry.value, entry.fieldMean, entry.fieldBest],
      })),
      series: [
        { label: row.team, color: SERIES[0] },
        { label: 'Среднее по залу', color: SERIES[1] },
        { label: 'Лучший', color: SERIES[2] },
      ],
      formatValue: (value) => formatNumber(value, 1),
    }),
    table: () => simpleTable(
      ['Раунд', 'Команда', 'Среднее по залу', 'Лучший', '% от лучшего'],
      breakdown.map((entry) => [
        entry.label, formatNumber(entry.value, 1), formatNumber(entry.fieldMean, 1),
        formatNumber(entry.fieldBest, 1), formatNumber(entry.share, 0),
      ]),
    ),
  }));

  if (game.rounds.length > 1 && fieldRows.length > 1) {
    const progress = cumulativeProgress(fieldRows, game.rounds.length);
    const mine = progress.find((entry) => entry.row.team_key === row.team_key);
    const points = game.round_labels.map((label, index) => ({
      date: null, label,
      extra: [{ label: 'Накопленный балл', value: mine ? formatNumber(mine.totals[index], 1) : '—' }],
    }));
    grid.appendChild(chartCard({
      title: 'Место по ходу игры',
      note: 'Позиция после каждого раунда по накопленной сумме.',
      render: (holder, tooltip) => lineChart(holder, tooltip, {
        points: points.map((point, index) => ({ ...point, date: `Р${index + 1}` })),
        invert: true,
        yMin: 1,
        yMax: fieldRows.length,
        integerTicks: true,
        series: [{
          label: 'Место после раунда', color: SERIES[0],
          values: mine ? mine.positions : [],
        }],
        formatValue: (value) => formatNumber(value, 0),
      }),
      table: () => simpleTable(
        ['Раунд', 'Накопленный балл', 'Место после раунда'],
        game.round_labels.map((label, index) => [
          label,
          mine ? formatNumber(mine.totals[index], 1) : '—',
          mine ? String(mine.positions[index]) : '—',
        ]),
      ),
    }));
  }

  const standingsCard = document.createElement('div');
  standingsCard.className = 'card';
  const heading = document.createElement('h3');
  heading.textContent = 'Итоговая таблица игры';
  standingsCard.appendChild(heading);
  standingsCard.appendChild(simpleTable(
    ['Место', 'Команда', 'Баллы', '% от лучшего'].concat(game.round_labels),
    fieldRows.map((entry) => [
      String(entry.position ?? '—'), entry.team, formatNumber(entry.total, 1),
      formatNumber(entry.score_pct, 0),
    ].concat(entry.rounds.map((value) => formatNumber(value, 1)))),
    (rowIndex) => fieldRows[rowIndex].team_key === row.team_key,
  ));
  section.appendChild(grid);
  section.appendChild(standingsCard);
  return section;
}

/* -------------------------------------------------------- game-type view */

function renderTypes(container, rows) {
  if (!rows.length) {
    container.appendChild(message('Нет игр', 'Ни одна игра не подходит под выбранные фильтры.'));
    return;
  }
  const entries = byGameType(rows, state.typeLevel);
  const columns = [
    { key: 'gameType', label: state.typeLevel === 'family' ? 'Формат' : 'Вариант формата',
      get: (entry) => entry.gameType, render: (entry) => entry.gameType, left: true },
    { key: 'variants', label: 'Вариантов', get: (entry) => entry.variants,
      render: (entry) => (entry.variants > 1 ? String(entry.variants) : '—') },
    { key: 'games', label: 'Игр', get: (entry) => entry.games, render: (entry) => String(entry.games) },
    { key: 'avgScore', label: 'Средний балл', get: (entry) => entry.avgScore ?? -1, render: (entry) => formatNumber(entry.avgScore, 1) },
    { key: 'avgPosition', label: 'Среднее место', get: (entry) => entry.avgPosition ?? 1e6, render: (entry) => formatNumber(entry.avgPosition, 1) },
    { key: 'winRate', label: 'Победы', get: (entry) => entry.winRate ?? -1, render: (entry) => `${entry.wins} · ${formatPercent(entry.winRate, 0)}` },
    { key: 'topNRate', label: 'Топ-3', get: (entry) => entry.topNRate ?? -1, render: (entry) => `${entry.topN} · ${formatPercent(entry.topNRate, 0)}` },
    { key: 'avgPercentile', label: 'Средний процентиль', get: (entry) => entry.avgPercentile ?? -1, render: (entry) => formatNumber(entry.avgPercentile, 1) },
    { key: 'consistency', label: 'σ, % от лучшего', get: (entry) => entry.consistency ?? -1, render: (entry) => formatNumber(entry.consistency, 1) },
  ];
  const sorted = sortRows(entries, columns, state.sort.types);

  const card = document.createElement('div');
  card.className = 'card';
  const heading = document.createElement('h3');
  heading.textContent = 'Сравнение форматов';
  const note = document.createElement('div');
  note.className = 'card-note';
  note.textContent = state.typeLevel === 'family'
    ? 'Издания одного формата собраны вместе: «[music party] летние хиты» и «[music party] рок» — это один формат. Процентиль сравним между форматами, абсолютный балл — нет.'
    : 'Каждое издание отдельной строкой. Процентиль сравним между форматами, абсолютный балл — нет.';

  const actions = document.createElement('div');
  actions.className = 'row-actions';
  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.className = 'reset';
  toggle.textContent = state.typeLevel === 'family'
    ? 'Показать отдельные варианты'
    : 'Свернуть в форматы';
  toggle.addEventListener('click', () => {
    state.typeLevel = state.typeLevel === 'family' ? 'type' : 'family';
    update();
  });
  const counts = document.createElement('span');
  counts.className = 'muted';
  counts.textContent = `${entries.length} ${plural(entries.length, 'строка', 'строки', 'строк')}`;
  actions.append(toggle, counts);

  card.append(heading, note, actions, buildTable(columns, sorted, {
    sortState: state.sort.types,
    onSort: (key) => { toggleSort(state.sort.types, key); update(); },
  }));
  container.appendChild(card);

  const grid = document.createElement('div');
  grid.className = 'grid charts-2';
  grid.appendChild(chartCard({
    title: 'Средний процентиль по форматам',
    note: formatBarsNote(entries),
    render: (holder, tooltip) => barChart(holder, tooltip, {
      items: formatBars(entries, (entry) => entry.avgPercentile, SERIES[0]),
      max: 100,
      formatValue: (value) => formatNumber(value, 0),
    }),
    table: () => simpleTable(['Формат', 'Игр', 'Средний процентиль'],
      entries.map((entry) => [entry.gameType, String(entry.games), formatNumber(entry.avgPercentile, 1)])),
  }));
  grid.appendChild(chartCard({
    title: 'Среднее место по форматам',
    note: `Меньше — лучше. ${formatBarsNote(entries)}`,
    render: (holder, tooltip) => barChart(holder, tooltip, {
      items: formatBars(entries, (entry) => entry.avgPosition, SERIES[1]),
      formatValue: (value) => formatNumber(value, 1),
    }),
    table: () => simpleTable(['Формат', 'Игр', 'Среднее место'],
      entries.map((entry) => [entry.gameType, String(entry.games), formatNumber(entry.avgPosition, 1)])),
  }));
  container.appendChild(grid);
}

/* ------------------------------------------------------------ rounds view */

function renderRounds(container, rows) {
  if (!rows.length) {
    container.appendChild(message('Нет игр', 'Ни одна игра не подходит под выбранные фильтры.'));
    return;
  }

  const profile = roundProfile(rows, (gameId) => state.dataset.rowsByGame.get(gameId) || []);
  if (!profile.length) {
    container.appendChild(message('Нет данных по раундам',
      'В выбранных играх не опубликованы результаты по раундам.'));
    return;
  }

  const teamName = (state.dataset.teamsByKey.get(state.team) || {}).name || '';
  const structures = new Set(rows.map((row) => (row.game.round_labels || []).length));

  // Rounds only a handful of formats have (a tenth round in a music party) are
  // no basis for "strongest round": one game at 100% would outrank seven years
  // of the round that actually decides games.
  const busiest = Math.max(...profile.map((entry) => entry.games), 1);
  const threshold = Math.max(3, busiest * 0.2);
  const representative = profile.filter((entry) => isNumber(entry.avgShare) && entry.games >= threshold);
  const scored = representative.length
    ? representative
    : profile.filter((entry) => isNumber(entry.avgShare));
  const strongest = scored.length ? scored.reduce((a, b) => (a.avgShare >= b.avgShare ? a : b)) : null;
  const weakest = scored.length ? scored.reduce((a, b) => (a.avgShare <= b.avgShare ? a : b)) : null;
  const penalties = profile.reduce((sum, entry) => sum + entry.negatives, 0);
  const biggest = profile.reduce((a, b) => ((a.pointsShare ?? 0) >= (b.pointsShare ?? 0) ? a : b));

  const kpis = document.createElement('div');
  kpis.className = 'grid kpis';
  for (const card of [
    { label: 'Сильнейший раунд', value: strongest ? strongest.label : '—', small: true,
      delta: strongest
        ? `${formatNumber(strongest.avgShare, 1)}% от лучшего · зал ${formatNumber(strongest.fieldShare, 1)}% · ${strongest.games} игр`
        : null },
    { label: 'Слабейший раунд', value: weakest ? weakest.label : '—', small: true,
      delta: weakest
        ? `${formatNumber(weakest.avgShare, 1)}% от лучшего · зал ${formatNumber(weakest.fieldShare, 1)}% · ${weakest.games} игр`
        : null },
    { label: 'Больше всего очков', value: biggest.label, small: true,
      delta: `${formatNumber(biggest.pointsShare, 0)}% всех баллов команды` },
    { label: 'Выигранных раундов', value: String(profile.reduce((sum, entry) => sum + entry.wins, 0)),
      delta: 'лучший результат зала в раунде' },
    { label: 'Уходов в минус', value: String(penalties),
      delta: penalties ? 'в раундах со ставкой очки можно потерять' : 'ни одного отрицательного раунда' },
  ]) {
    const node = document.createElement('div');
    node.className = 'card kpi';
    const label = document.createElement('div');
    label.className = 'label';
    label.textContent = card.label;
    const value = document.createElement('div');
    value.className = card.small ? 'value small' : 'value';
    value.textContent = card.value;
    node.append(label, value);
    if (card.delta) {
      const delta = document.createElement('div');
      delta.className = 'delta';
      delta.textContent = card.delta;
      node.appendChild(delta);
    }
    kpis.appendChild(node);
  }
  container.appendChild(kpis);

  if (structures.size > 1) {
    const warn = document.createElement('p');
    warn.className = 'scope-note';
    warn.textContent = `В выборке игры с разным числом раундов (${[...structures].sort((a, b) => a - b).join(', ')}). `
      + 'Раунд №3 в семираундовой игре и в восьмираундовой — это разные раунды; '
      + 'выберите формат в фильтрах, чтобы сравнивать одинаковые.';
    container.appendChild(warn);
  }

  const grid = document.createElement('div');
  grid.className = 'grid charts-2';

  grid.appendChild(chartCard({
    title: 'Профиль по раундам',
    note: 'Доля от лучшего результата в этом раунде — единственная величина, сравнимая между '
      + 'раундами: в классике раунд 7 стоит ~18 баллов, а раунды 1–3 — по 6.',
    legend: [
      { label: teamName || 'Команда', color: SERIES[0], shape: 'rect' },
      { label: 'Средний по залу', color: SERIES[1], shape: 'rect' },
    ],
    render: (holder, tooltip) => groupedColumns(holder, tooltip, {
      groups: profile.map((entry) => ({
        label: entry.label.replace('Раунд ', 'Р'),
        values: [entry.avgShare, entry.fieldShare],
      })),
      series: [
        { label: teamName || 'Команда', color: SERIES[0] },
        { label: 'Средний по залу', color: SERIES[1] },
      ],
      formatValue: (value) => formatNumber(value, 1),
    }),
    table: () => simpleTable(
      ['Раунд', 'Игр', '% от лучшего', 'Зал, % от лучшего'],
      profile.map((entry) => [
        entry.label, String(entry.games),
        formatNumber(entry.avgShare, 1), formatNumber(entry.fieldShare, 1),
      ]),
    ),
  }));

  grid.appendChild(chartCard({
    title: 'Откуда приходят баллы',
    note: 'Доля раунда в сумме, набранной командой за выбранные игры.',
    render: (holder, tooltip) => barChart(holder, tooltip, {
      items: profile.map((entry) => ({
        label: entry.label,
        value: entry.pointsShare ?? 0,
        rows: [
          { label: 'Доля в сумме', value: `${formatNumber(entry.pointsShare, 1)}%`, color: SERIES[0] },
          { label: 'Средний балл', value: formatNumber(entry.avgScore, 1) },
          { label: 'Игр', value: String(entry.games) },
        ],
      })),
      formatValue: (value) => `${formatNumber(value, 0)}%`,
    }),
    table: () => simpleTable(
      ['Раунд', 'Средний балл', 'Доля в сумме, %'],
      profile.map((entry) => [
        entry.label, formatNumber(entry.avgScore, 1), formatNumber(entry.pointsShare, 1),
      ]),
    ),
  }));
  container.appendChild(grid);

  const card = document.createElement('div');
  card.className = 'card';
  const heading = document.createElement('h3');
  heading.textContent = 'Раунды по числам';
  const note = document.createElement('div');
  note.className = 'card-note';
  note.textContent = 'Выигранный раунд — лучший результат зала в нём, включая дележ первого места. '
    + 'В отдельных раундах результат бывает отрицательным: очки в них можно потерять, '
    + 'хотя итог игры ниже нуля не опускается.';
  card.append(heading, note);

  const columns = [
    { key: 'label', label: 'Раунд', get: (entry) => entry.index, render: (entry) => entry.label, left: true },
    { key: 'games', label: 'Игр', get: (entry) => entry.games, render: (entry) => String(entry.games) },
    { key: 'avgScore', label: 'Средний балл', get: (entry) => entry.avgScore ?? -1, render: (entry) => formatNumber(entry.avgScore, 2) },
    { key: 'bestScore', label: 'Лучший', get: (entry) => entry.bestScore ?? -1, render: (entry) => formatNumber(entry.bestScore, 1) },
    { key: 'worstScore', label: 'Худший', get: (entry) => entry.worstScore ?? -1, render: (entry) => formatNumber(entry.worstScore, 1) },
    { key: 'avgShare', label: '% от лучшего', get: (entry) => entry.avgShare ?? -1, render: (entry) => formatNumber(entry.avgShare, 1) },
    { key: 'fieldShare', label: 'Зал, %', get: (entry) => entry.fieldShare ?? -1, render: (entry) => formatNumber(entry.fieldShare, 1) },
    { key: 'pointsShare', label: 'Доля в сумме, %', get: (entry) => entry.pointsShare ?? -1, render: (entry) => formatNumber(entry.pointsShare, 1) },
    { key: 'wins', label: 'Выиграно', get: (entry) => entry.wins, render: (entry) => `${entry.wins} · ${formatPercent(entry.winRate, 0)}` },
    { key: 'negatives', label: 'В минус', get: (entry) => entry.negatives, render: (entry) => String(entry.negatives) },
    { key: 'consistency', label: 'σ, %', get: (entry) => entry.consistency ?? -1, render: (entry) => formatNumber(entry.consistency, 1) },
  ];
  if (!state.sort.rounds) state.sort.rounds = { key: 'label', dir: 'asc' };
  card.appendChild(buildTable(columns, sortRows(profile, columns, state.sort.rounds), {
    sortState: state.sort.rounds,
    onSort: (key) => { toggleSort(state.sort.rounds, key); update(); },
  }));
  container.appendChild(card);
}

/* ------------------------------------------------------------ venues view */

function renderVenues(container, rows) {
  const venues = state.dataset.venues || [];
  if (!venues.length) {
    container.appendChild(message('Нет площадок', 'В датасете нет ни одной площадки.'));
    return;
  }

  // Games the selected team played at each venue, on the current filters.
  const teamGames = new Map();
  for (const row of rows) {
    const name = row.game.venue;
    if (name) teamGames.set(name, (teamGames.get(name) || 0) + 1);
  }
  const teamName = (state.dataset.teamsByKey.get(state.team) || {}).name || '';
  const plotted = venues.filter((venue) => venue.has_coords);
  const missing = venues.filter((venue) => !venue.has_coords);

  const points = plotted.map((venue) => ({
    key: venue.title,
    name: venue.title,
    lat: venue.lat,
    lon: venue.lon,
    value: venue.games,
    color: teamGames.has(venue.title) ? SERIES[0] : SERIES[1],
    dim: !teamGames.has(venue.title),
    label: venue.title,
    rows: [
      { label: 'Игр в городе', value: String(venue.games), color: SERIES[0] },
      { label: `Игр команды ${teamName}`, value: String(teamGames.get(venue.title) || 0) },
      { label: 'Период', value: `${formatDate(venue.first)} — ${formatDate(venue.last)}` },
      { label: 'Адрес', value: venue.address || '—' },
    ],
  }));

  // The map leads this view, so it gets the full width rather than a column.
  const chosen = state.basemap === 'auto'
    ? TILE_PROVIDERS
    : TILE_PROVIDERS.filter((provider) => provider.name === state.basemap);

  const mapCard = chartCard({
    title: 'Где играли в Баку',
    note: 'Размер круга — число игр на площадке. Синие — где играла выбранная команда. '
      + 'Подложка загружается вашим браузером с сервера карт.',
    legend: [
      { label: `Играла ${teamName}`.trim(), color: SERIES[0], shape: 'rect' },
      { label: 'Остальные площадки', color: SERIES[1], shape: 'rect' },
    ],
    render: (holder, tooltip) => mapChart(holder, tooltip, {
      points,
      selectedKey: state.filters.venue,
      providers: chosen,
      noBasemap: state.basemap === 'none',
      onTilesLoaded: (provider) => {
        const note = mapCard.querySelector('.card-note');
        if (note && !note.dataset.credited) {
          note.dataset.credited = '1';
          note.textContent = 'Размер круга — число игр на площадке. Синие — где играла '
            + `выбранная команда. Подложка: ${provider.attribution}.`;
        }
      },
      // Tiles come from a third party, so plan for them not arriving: an
      // offline file or a sandbox that blocks other hosts still gets a map,
      // just without streets under it.
      onTilesFailed: () => {
        const note = mapCard.querySelector('.card-note');
        if (note) {
          note.textContent = 'Размер круга — число игр на площадке. Синие — где играла '
            + 'выбранная команда. Ни один сервер карт не ответил — возможно, они заблокированы '
            + 'в вашей сети. Показаны только координаты площадок: север сверху, масштаб внизу.';
        }
      },
      onSelect: (point) => {
        // Clicking a pin filters everything below to that venue, and clicking
        // the selected one clears it again.
        state.filters.venue = state.filters.venue === point.key ? '' : point.key;
        syncFilterInputs();
        update();
      },
    }),
    table: () => simpleTable(
      ['Площадка', 'Игр в городе', 'Игр команды', 'Широта', 'Долгота'],
      plotted.map((venue) => [
        venue.title, String(venue.games), String(teamGames.get(venue.title) || 0),
        venue.lat.toFixed(5), venue.lon.toFixed(5),
      ]),
    ),
  });

  // Let the reader pick a source: one host may be blocked where another is not.
  const picker = document.createElement('div');
  picker.className = 'row-actions';
  const pickerLabel = document.createElement('label');
  pickerLabel.className = 'muted';
  pickerLabel.setAttribute('for', 'basemap');
  pickerLabel.textContent = 'Подложка:';
  const select = document.createElement('select');
  select.id = 'basemap';
  for (const option of [
    { value: 'auto', label: 'Автоматически' },
    ...TILE_PROVIDERS.map((provider) => ({ value: provider.name, label: provider.name })),
    { value: 'none', label: 'Без подложки' },
  ]) {
    const node = document.createElement('option');
    node.value = option.value;
    node.textContent = option.label;
    select.appendChild(node);
  }
  select.value = state.basemap;
  select.addEventListener('change', () => {
    state.basemap = select.value;
    try { localStorage.setItem('qp-basemap', state.basemap); } catch { /* private mode */ }
    update();
  });
  picker.append(pickerLabel, select);
  mapCard.insertBefore(picker, mapCard.querySelector('.chart'));
  container.appendChild(mapCard);

  const grid = document.createElement('div');
  grid.className = 'grid';

  grid.appendChild(chartCard({
    title: 'Когда работала каждая площадка',
    note: 'От первой игры до последней. Видно, как сцена переезжала по городу.',
    render: (holder, tooltip) => periodBars(holder, tooltip, {
      items: venues.filter((venue) => venue.first && venue.last).map((venue) => ({
        label: venue.title,
        from: venue.first,
        to: venue.last,
        value: venue.games,
        color: teamGames.has(venue.title) ? SERIES[0] : SERIES[1],
      })),
      formatDate,
    }),
    table: () => simpleTable(
      ['Площадка', 'Первая игра', 'Последняя игра', 'Игр'],
      venues.map((venue) => [
        venue.title, formatDate(venue.first), formatDate(venue.last), String(venue.games),
      ]),
    ),
  }));
  container.appendChild(grid);

  const card = document.createElement('div');
  card.className = 'card';
  const heading = document.createElement('h3');
  heading.textContent = 'Площадки';
  const note = document.createElement('div');
  note.className = 'card-note';
  note.textContent = 'Нажмите на строку, чтобы отфильтровать всё по этой площадке.';
  card.append(heading, note);

  const columns = [
    { key: 'title', label: 'Площадка', get: (v) => v.title, render: (v) => v.title, left: true },
    { key: 'address', label: 'Адрес', get: (v) => v.address || '', render: (v) => v.address || '—', left: true },
    { key: 'games', label: 'Игр в городе', get: (v) => v.games, render: (v) => String(v.games) },
    { key: 'scored', label: 'С результатами', get: (v) => v.scored, render: (v) => String(v.scored) },
    { key: 'team', label: 'Игр команды', get: (v) => teamGames.get(v.title) || 0,
      render: (v) => String(teamGames.get(v.title) || 0) },
    { key: 'first', label: 'Первая', get: (v) => v.first || '', render: (v) => formatDate(v.first), left: true },
    { key: 'last', label: 'Последняя', get: (v) => v.last || '', render: (v) => formatDate(v.last), left: true },
  ];
  if (!state.sort.venues) state.sort.venues = { key: 'games', dir: 'desc' };
  card.appendChild(buildTable(columns, sortRows(venues, columns, state.sort.venues), {
    sortState: state.sort.venues,
    onSort: (key) => { toggleSort(state.sort.venues, key); update(); },
    onRowClick: (venue) => {
      state.filters.venue = state.filters.venue === venue.title ? '' : venue.title;
      syncFilterInputs();
      update();
    },
    isSelected: (venue) => state.filters.venue === venue.title,
  }));
  container.appendChild(card);

  if (missing.length) {
    const warn = document.createElement('div');
    warn.className = 'card';
    const warnHeading = document.createElement('h3');
    warnHeading.textContent = 'Без координат на карте';
    const warnNote = document.createElement('div');
    warnNote.className = 'card-note';
    warnNote.textContent = 'У этих площадок в источнике нет пригодных координат '
      + '(долгота отсутствует), поэтому на карту они не нанесены.';
    warn.append(warnHeading, warnNote, simpleTable(
      ['Площадка', 'Адрес', 'Игр'],
      missing.map((venue) => [venue.title, venue.address || '—', String(venue.games)]),
    ));
    container.appendChild(warn);
  }
}

/* -------------------------------------------------------- comparison view */

function renderCompare(container) {
  const picker = document.createElement('div');
  picker.className = 'card';
  const heading = document.createElement('h3');
  heading.textContent = 'Сравнение команд';
  const note = document.createElement('div');
  note.className = 'card-note';
  note.textContent = 'Отметьте до трёх соперников — они сравниваются с выбранной командой на тех же фильтрах.';
  picker.append(heading, note);

  // The results are re-rendered on their own, so toggling a checkbox never
  // rebuilds the picker under the reader's cursor.
  const results = document.createElement('div');

  const line = document.createElement('div');
  line.className = 'checkline';
  const candidates = state.dataset.teams.filter((team) => team.key !== state.team).slice(0, 40);
  for (const team of candidates) {
    const label = document.createElement('label');
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = state.compare.includes(team.key);
    if (box.checked) label.classList.add('on');
    box.addEventListener('change', () => {
      if (box.checked) {
        if (state.compare.length >= 3) { box.checked = false; return; }
        state.compare = [...state.compare, team.key];
      } else {
        state.compare = state.compare.filter((key) => key !== team.key);
      }
      label.classList.toggle('on', box.checked);
      renderComparison(results);
    });
    label.append(box, document.createTextNode(`${team.name} (${team.games})`));
    line.appendChild(label);
  }
  picker.appendChild(line);
  container.append(picker, results);
  renderComparison(results);
}

function renderComparison(container) {
  container.replaceChildren();

  const keys = [state.team, ...state.compare].filter(Boolean);
  if (keys.length < 2) {
    container.appendChild(message('Выберите соперников', 'Отметьте хотя бы одну команду для сравнения.'));
    return;
  }

  const rowsByTeam = new Map(keys.map((key) => [key, applyFilters(teamRows(state.dataset, key), state.filters)]));
  const colorOf = (key) => SERIES[keys.indexOf(key) % SERIES.length];
  const nameOf = (key) => (state.dataset.teamsByKey.get(key) || {}).name || key;

  const columns = [
    { key: 'team', label: 'Команда', get: (entry) => entry.name, left: true,
      render: (entry) => {
        const wrap = document.createElement('span');
        const swatch = document.createElement('span');
        swatch.className = 'swatch';
        swatch.style.background = entry.color;
        wrap.append(swatch, document.createTextNode(entry.name));
        return wrap;
      } },
    { key: 'games', label: 'Игр', get: (entry) => entry.games, render: (entry) => String(entry.games) },
    { key: 'avgPercentile', label: 'Средний процентиль', get: (entry) => entry.avgPercentile ?? -1, render: (entry) => formatNumber(entry.avgPercentile, 1) },
    { key: 'avgPosition', label: 'Среднее место', get: (entry) => entry.avgPosition ?? 1e6, render: (entry) => formatNumber(entry.avgPosition, 1) },
    { key: 'winRate', label: 'Победы', get: (entry) => entry.winRate ?? -1, render: (entry) => `${entry.wins} · ${formatPercent(entry.winRate, 0)}` },
    { key: 'topNRate', label: 'Топ-3', get: (entry) => entry.topNRate ?? -1, render: (entry) => `${entry.topN} · ${formatPercent(entry.topNRate, 0)}` },
    { key: 'avgScore', label: 'Средний балл', get: (entry) => entry.avgScore ?? -1, render: (entry) => formatNumber(entry.avgScore, 1) },
    { key: 'consistency', label: 'σ, % от лучшего', get: (entry) => entry.consistency ?? -1, render: (entry) => formatNumber(entry.consistency, 1) },
  ];
  const summaries = keys.map((key) => ({
    key, name: nameOf(key), color: colorOf(key), ...summarize(rowsByTeam.get(key)),
  }));

  const table = document.createElement('div');
  table.className = 'card';
  table.id = 'compare-table';
  const tableHeading = document.createElement('h3');
  tableHeading.textContent = 'Итоги на выбранных фильтрах';
  table.append(tableHeading, buildTable(columns, summaries, {}));
  container.appendChild(table);

  // Head-to-head: only games the selected teams actually played together.
  const shared = headToHead(rowsByTeam);
  const grid = document.createElement('div');
  grid.className = 'grid charts-2';

  if (shared.length) {
    const points = shared.map((entry) => ({
      date: entry.game.date,
      label: `${formatDate(entry.game.date)} · ${entry.game.game_type}`,
    }));
    grid.appendChild(chartCard({
      title: 'Очные игры: процентиль',
      note: `${shared.length} ${plural(shared.length, 'общая игра', 'общие игры', 'общих игр')}.`,
      legend: summaries.map((entry) => ({ label: entry.name, color: entry.color })),
      render: (holder, tooltip) => lineChart(holder, tooltip, {
        points,
        yMin: 0,
        yMax: 100,
        series: keys.map((key) => ({
          label: nameOf(key), color: colorOf(key),
          values: shared.map((entry) => {
            const row = entry.entries.get(key);
            return row ? row.percentile : null;
          }),
        })),
        formatValue: (value) => formatNumber(value, 1),
      }),
      table: () => simpleTable(
        ['Дата', 'Игра'].concat(keys.map(nameOf)),
        shared.map((entry) => [formatDate(entry.game.date), entry.game.title].concat(
          keys.map((key) => {
            const row = entry.entries.get(key);
            return row ? `${row.position} место · ${formatNumber(row.total, 1)}` : '—';
          }),
        )),
      ),
    }));

    const wins = new Map(keys.map((key) => [key, 0]));
    for (const entry of shared) {
      const contenders = keys.map((key) => entry.entries.get(key)).filter(Boolean);
      const best = contenders.reduce((a, b) => ((a.position ?? 1e6) <= (b.position ?? 1e6) ? a : b));
      wins.set(best.team_key, (wins.get(best.team_key) || 0) + 1);
    }
    grid.appendChild(chartCard({
      title: 'Очные встречи: кто был выше',
      render: (holder, tooltip) => barChart(holder, tooltip, {
        items: keys.map((key) => ({
          label: nameOf(key), value: wins.get(key) || 0, color: colorOf(key),
          rows: [{ label: 'Побед в очных', value: String(wins.get(key) || 0), color: colorOf(key) }],
        })),
        formatValue: (value) => String(value),
      }),
      table: () => simpleTable(['Команда', 'Выше соперника'],
        keys.map((key) => [nameOf(key), String(wins.get(key) || 0)])),
    }));
  } else {
    grid.appendChild(message('Общих игр нет', 'На выбранных фильтрах команды не пересекались.'));
  }

  container.appendChild(grid);
}

/* ------------------------------------------------------------- utilities */

const FORMAT_BAR_LIMIT = 12;

/* Bars for the formats a team actually plays, most-played first.
 *
 * A city runs dozens of one-off formats; 80 bars is a list, not a chart, and
 * ranking them by average percentile puts single-game novelties on top. So the
 * chart shows the formats with the most games and says how many are left; the
 * Форматы tab keeps every one of them in a sortable table. */
function formatBars(entries, pick, color) {
  const ranked = [...entries].sort((a, b) => b.games - a.games || (b.avgPercentile ?? 0) - (a.avgPercentile ?? 0));
  const shown = ranked.slice(0, FORMAT_BAR_LIMIT);
  return shown.map((entry) => ({
    label: `${entry.gameType} · ${entry.games}`,
    value: pick(entry) ?? 0,
    color,
    rows: [
      { label: 'Игр', value: String(entry.games) },
      { label: 'Средний процентиль', value: formatNumber(entry.avgPercentile, 1) },
      { label: 'Среднее место', value: formatNumber(entry.avgPosition, 1) },
      { label: 'Средний балл', value: formatNumber(entry.avgScore, 1) },
    ],
  }));
}

function formatBarsNote(entries) {
  const hidden = entries.length - Math.min(entries.length, FORMAT_BAR_LIMIT);
  const base = 'После названия — число игр в формате.';
  return hidden
    ? `${base} Показаны ${FORMAT_BAR_LIMIT} самых частых из ${entries.length}; остальные — в таблице и на вкладке «Форматы».`
    : base;
}

function histogram(values, min, max, step) {
  const bins = [];
  for (let from = min; from < max; from += step) {
    bins.push({ from, to: from + step, count: 0 });
  }
  for (const value of values) {
    if (!isNumber(value)) continue;
    const index = Math.min(bins.length - 1, Math.max(0, Math.floor((value - min) / step)));
    bins[index].count += 1;
  }
  return bins;
}

function sortRows(rows, columns, sortState) {
  const column = columns.find((candidate) => candidate.key === sortState.key) || columns[0];
  const direction = sortState.dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const left = column.get(a);
    const right = column.get(b);
    if (typeof left === 'string' || typeof right === 'string') {
      return String(left).localeCompare(String(right), 'ru') * direction;
    }
    return ((left ?? 0) - (right ?? 0)) * direction;
  });
}

function toggleSort(sortState, key) {
  if (sortState.key === key) {
    sortState.dir = sortState.dir === 'asc' ? 'desc' : 'asc';
  } else {
    sortState.key = key;
    sortState.dir = 'desc';
  }
}

function buildTable(columns, rows, { sortState, onSort, onRowClick, isSelected }) {
  const wrap = document.createElement('div');
  wrap.className = 'table-wrap';
  const table = document.createElement('table');

  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  for (const column of columns) {
    const th = document.createElement('th');
    th.textContent = column.label;
    if (!column.left) th.classList.add('num');
    if (onSort) {
      th.tabIndex = 0;
      if (sortState && sortState.key === column.key) {
        th.setAttribute('aria-sort', sortState.dir === 'asc' ? 'ascending' : 'descending');
      }
      th.addEventListener('click', () => onSort(column.key));
      th.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSort(column.key); }
      });
    } else {
      th.classList.add('nosort');
    }
    headRow.appendChild(th);
  }
  thead.appendChild(headRow);

  const tbody = document.createElement('tbody');
  for (const row of rows) {
    const tr = document.createElement('tr');
    if (onRowClick) {
      tr.classList.add('selectable');
      tr.tabIndex = 0;
      tr.addEventListener('click', () => onRowClick(row));
      tr.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') onRowClick(row);
      });
    }
    if (isSelected && isSelected(row)) tr.classList.add('is-selected');
    if (row && row.game && row.game.incomplete) tr.classList.add('is-incomplete');
    for (const column of columns) {
      const td = document.createElement('td');
      if (!column.left) td.classList.add('num');
      const value = column.render(row);
      if (value instanceof Node) td.appendChild(value);
      else td.textContent = value;
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }

  table.append(thead, tbody);
  wrap.appendChild(table);
  return wrap;
}

function simpleTable(headers, rows, highlight) {
  const wrap = document.createElement('div');
  wrap.className = 'table-wrap';
  const table = document.createElement('table');
  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  headers.forEach((header, index) => {
    const th = document.createElement('th');
    th.className = index === 0 ? 'left nosort' : 'num nosort';
    th.textContent = header;
    headRow.appendChild(th);
  });
  thead.appendChild(headRow);

  const tbody = document.createElement('tbody');
  rows.forEach((cells, rowIndex) => {
    const tr = document.createElement('tr');
    if (highlight && highlight(rowIndex)) tr.classList.add('is-selected');
    cells.forEach((cell, index) => {
      const td = document.createElement('td');
      if (index > 0) td.classList.add('num');
      td.textContent = cell;
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  table.append(thead, tbody);
  wrap.appendChild(table);
  return wrap;
}

function fillSelect(select, options, placeholder) {
  select.replaceChildren();
  if (placeholder !== undefined) {
    const option = document.createElement('option');
    option.value = '';
    option.textContent = placeholder || 'Все';
    select.appendChild(option);
  }
  for (const item of options) {
    const option = document.createElement('option');
    option.value = item.value;
    option.textContent = item.label;
    select.appendChild(option);
  }
}

function message(title, text) {
  const node = document.createElement('div');
  node.className = 'card empty';
  const heading = document.createElement('strong');
  heading.textContent = title;
  const body = document.createElement('div');
  body.textContent = text;
  node.append(heading, body);
  return node;
}

function sectionTitle(text) {
  const node = document.createElement('h2');
  node.className = 'section-title';
  node.textContent = text;
  return node;
}

function container_title(parent, text) {
  parent.appendChild(sectionTitle(text));
}

function plural(count, one, few, many) {
  const mod100 = Math.abs(count) % 100;
  const mod10 = mod100 % 10;
  if (mod100 >= 11 && mod100 <= 14) return many;
  if (mod10 === 1) return one;
  if (mod10 >= 2 && mod10 <= 4) return few;
  return many;
}
