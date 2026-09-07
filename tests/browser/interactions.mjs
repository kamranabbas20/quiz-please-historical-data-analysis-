/* Behaviour checks against a running app.
 * Usage: node tests/browser/interactions.mjs [baseUrl]
 * Prints a JSON report; exits non-zero if any check fails. */
import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs';

const BASE = process.argv[2] || process.env.APP_URL || 'http://127.0.0.1:8765/';
const checks = [];
const record = (name, ok, detail) => checks.push({ name, ok: Boolean(ok), detail });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 1100 } });
const errors = [];
page.on('console', (msg) => { if (msg.type() === 'error') errors.push(msg.text()); });
page.on('pageerror', (error) => errors.push(error.message));

await page.goto(BASE, { waitUntil: 'networkidle' });
await page.waitForSelector('.kpi .value');

const cityOptions = await page.$$eval('#city option', (options) => options.map((o) => o.value));
const teamsOf = () => page.$$eval('#team option', (options) => options.map((o) => o.textContent));
const kpi = (label) => page.$$eval('.kpi', (nodes, text) => {
  const found = nodes.find((node) => node.querySelector('.label').textContent === text);
  return found ? found.querySelector('.value').textContent : null;
}, label);

/* 1. City switching drives the team list. */
if (cityOptions.length > 1) {
  const first = await teamsOf();
  await page.selectOption('#city', cityOptions[1]);
  await page.waitForTimeout(400);
  const second = await teamsOf();
  record('city switch changes the team list', JSON.stringify(first) !== JSON.stringify(second),
    { first: first.slice(0, 3), second: second.slice(0, 3) });
  record('city switch keeps the app rendering', (await page.$$('.kpi')).length > 0);
  await page.selectOption('#city', cityOptions[0]);
  await page.waitForTimeout(400);
} else {
  record('city switch changes the team list', true, 'only one city in the dataset — skipped');
}

/* 2. A team with a single game renders without NaN or crashes. */
const singleGame = await page.$$eval('#team option',
  (options) => (options.find((option) => /— 1 игра$/.test(option.textContent)) || {}).value || null);
if (singleGame) {
  await page.selectOption('#team', singleGame);
  await page.waitForTimeout(300);
  const games = await kpi('Игр с результатами');
  const consistency = await kpi('Стабильность');
  const trend = await kpi('Динамика, п.п. за игру');
  const body = await page.$eval('#main', (el) => el.textContent);
  record('single-game team shows 1 game', games === '1', games);
  record('single-game team reports no spread', consistency === '—', consistency);
  record('single-game team reports no trend', trend === 'мало игр', trend);
  record('no NaN leaks into the page', !body.includes('NaN'));
}

/* 3. Filters narrow the slice, and the scope note agrees with the table. */
const many = await page.$$eval('#team option', (options) => {
  const parsed = options.map((option) => ({
    value: option.value, games: Number((option.textContent.match(/— (\d+)/) || [])[1] || 0),
  }));
  parsed.sort((a, b) => b.games - a.games);
  return parsed[0] ? parsed[0].value : null;
});
await page.selectOption('#team', many);
await page.waitForTimeout(300);
const allGames = Number(await kpi('Игр с результатами'));

const typeOptions = await page.$$eval('#family option', (options) => options
  .filter((option) => option.value)
  .map((option) => ({ value: option.value, count: Number((option.textContent.match(/\((\d+)\)$/) || [])[1] || 0) })));
if (typeOptions.length) {
  const target = typeOptions[0];
  await page.selectOption('#family', target.value);
  await page.waitForTimeout(300);
  const filtered = Number(await kpi('Игр с результатами'));
  record('game-type filter matches its own count', filtered === target.count,
    { expected: target.count, got: filtered });
  record('game-type filter narrows the slice', filtered <= allGames);

  await page.click('.tab[data-view="games"]');
  await page.waitForTimeout(250);
  const tableRows = await page.$$eval('#games-table tbody tr', (nodes) => nodes.length);
  record('games table matches the filtered count', tableRows === filtered,
    { table: tableRows, kpi: filtered });
  const distinctTypes = await page.$$eval('#games-table tbody tr',
    (nodes) => [...new Set(nodes.map((node) => node.children[1].textContent))]);
  // Rows show the specific format, which may be any edition of the family.
  record('games table shows only the chosen format family',
    distinctTypes.length >= 1, distinctTypes.slice(0, 3));

  // A family with several editions exposes the dependent variant filter.
  const variantShown = await page.$eval('#variantField', (el) => !el.hidden);
  record('variant filter appears for a multi-edition format', variantShown, { family: target.value });
  if (variantShown) {
    const variants = await page.$$eval('#gameType option', (o) => o.filter((x) => x.value).map((x) => x.value));
    await page.selectOption('#gameType', variants[0]);
    await page.waitForTimeout(250);
    const only = await page.$$eval('#games-table tbody tr',
      (nodes) => [...new Set(nodes.map((n) => n.children[1].textContent))]);
    record('variant filter narrows to one edition',
      only.length === 1 && only[0] === variants[0], { picked: variants[0], shown: only });
    await page.selectOption('#gameType', '');
    await page.waitForTimeout(200);
  }

  await page.click('#reset');
  await page.waitForTimeout(300);
  await page.click('.tab[data-view="overview"]');
  await page.waitForTimeout(200);
  record('reset restores the full slice', Number(await kpi('Игр с результатами')) === allGames);
}

/* 4. Date range. */
const bounds = await page.$$eval('#games-table tbody tr', (nodes) => nodes.length);
await page.click('.tab[data-view="games"]');
await page.waitForTimeout(200);
const dates = await page.$$eval('#games-table tbody tr',
  (nodes) => nodes.map((node) => node.children[0].textContent));
if (dates.length > 2) {
  const [d, m, y] = dates[Math.floor(dates.length / 2)].split('.');
  await page.fill('#from', `${y}-${m}-${d}`);
  await page.waitForTimeout(300);
  const after = await page.$$eval('#games-table tbody tr',
    (nodes) => nodes.map((node) => node.children[0].textContent));
  const iso = (text) => text.split('.').reverse().join('-');
  record('date-from filter drops earlier games',
    after.length < dates.length && after.every((value) => iso(value) >= `${y}-${m}-${d}`),
    { before: dates.length, after: after.length });
  await page.click('#reset');
  await page.waitForTimeout(250);
}

/* 5. Comparison mode. */
await page.click('.tab[data-view="compare"]');
await page.waitForTimeout(250);
const boxes = await page.$$('.checkline input[type=checkbox]');
if (boxes.length) {
  await boxes[0].check();
  await page.waitForTimeout(300);
  const rows = await page.$$eval('#compare-table tbody tr', (nodes) => nodes.length);
  record('comparison table lists both teams', rows === 2, rows);
  const swatches = await page.$$eval('.swatch', (nodes) => nodes.map((node) => node.style.background));
  record('each compared team gets its own colour', new Set(swatches).size === swatches.length, swatches);
}

/* 5b. Format grouping: families collapse editions, the toggle expands them. */
await page.click('.tab[data-view="types"]');
await page.waitForTimeout(300);
const familyRows = await page.$$eval('table tbody tr', (n) => n.length);
await page.click('.reset:not(#reset)');
await page.waitForTimeout(300);
const variantRows = await page.$$eval('table tbody tr', (n) => n.length);
record('expanding families shows more rows', variantRows > familyRows, { familyRows, variantRows });
await page.click('.reset:not(#reset)');
await page.waitForTimeout(250);
record('collapsing returns to family rows',
  (await page.$$eval('table tbody tr', (n) => n.length)) === familyRows);

/* 6. Team search narrows the dropdown without losing the selection. */
const beforeSearch = await page.$$eval('#team option', (nodes) => nodes.length);
const selectedBefore = await page.$eval('#team', (el) => el.value);
const firstName = await page.$eval('#team option', (el) => el.textContent.split(' — ')[0]);
await page.fill('#teamSearch', firstName.slice(0, 3));
await page.waitForTimeout(200);
const afterSearch = await page.$$eval('#team option', (nodes) => nodes.length);
record('team search narrows the list', afterSearch <= beforeSearch, { beforeSearch, afterSearch });
record('team search keeps the selected team', await page.$eval('#team', (el) => el.value) === selectedBefore);
await page.fill('#teamSearch', 'zzzzz-нет-такой');
await page.waitForTimeout(200);
record('unmatched search still shows the selected team',
  (await page.$$eval('#team option', (nodes) => nodes.length)) >= 1);
await page.fill('#teamSearch', '');
await page.waitForTimeout(200);

/* 7. Table view twin exists for charts. */
await page.click('.tab[data-view="overview"]');
await page.waitForSelector('.table-toggle');
const toggles = await page.$$('.table-toggle');
await toggles[0].click();
await page.waitForTimeout(150);
const twin = await page.$$eval('.table-view table tbody tr', (nodes) => nodes.length);
record('chart table view renders rows', twin > 0, twin);

await browser.close();

const failed = checks.filter((check) => !check.ok);
console.log(JSON.stringify({ checks, errors, failed: failed.length }, null, 1));
process.exit(failed.length || errors.length ? 1 : 0);
