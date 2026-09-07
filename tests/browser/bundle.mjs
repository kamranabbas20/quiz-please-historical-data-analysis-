/* Verifies the single-file build works from disk, with no server at all.
 * Usage: node tests/browser/bundle.mjs [path-to-dashboard.html] */
import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs';
import path from 'node:path';

const file = path.resolve(process.argv[2] || 'dist/dashboard.html');
const checks = [];
const record = (name, ok, detail) => checks.push({ name, ok: Boolean(ok), detail });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 1100 } });
const errors = [];
const TILE_NOISE = /tile\.openstreetmap|ERR_TUNNEL_CONNECTION_FAILED|Failed to load resource/;
page.on('console', (msg) => {
  if (msg.type() === 'error' && !TILE_NOISE.test(msg.text())) errors.push(msg.text());
});
page.on('pageerror', (error) => errors.push(error.message));
// The file must carry its own data. The one thing it may reach for is basemap
// tiles, which are the viewer's browser fetching someone else's images and
// which the map is built to do without.
const requests = [];
page.on('request', (request) => {
  const url = request.url();
  if (!url.startsWith('file://') && !url.includes('tile.openstreetmap.org')) requests.push(url);
});

await page.goto(`file://${file}`, { waitUntil: 'load' });
await page.waitForSelector('.kpi .value', { timeout: 15000 });

record('no network requests beyond basemap tiles', requests.length === 0, requests.slice(0, 3));
record('teams discovered', (await page.$$eval('#team option', (n) => n.length)) > 100);
record('charts rendered', (await page.$$eval('.chart svg', (n) => n.length)) >= 6);
record('coverage line present', (await page.$eval('#coverage', (el) => el.textContent)).includes('258'));

const kpi = (label) => page.$$eval('.kpi', (nodes, text) => {
  const found = nodes.find((node) => node.querySelector('.label').textContent === text);
  return found ? found.querySelector('.value').textContent : null;
}, label);
// Games with an unfilled round are excluded by default, and the toggle brings
// them back: the single file must behave exactly like the served app.
const defaultGames = Number(await kpi('Игр с результатами'));
record('KPI counts the usable games', defaultGames === 185, defaultGames);
await page.check('#includeIncomplete');
await page.waitForTimeout(300);
const withIncomplete = Number(await kpi('Игр с результатами'));
record('the toggle restores the excluded games', withIncomplete === 209, withIncomplete);
await page.uncheck('#includeIncomplete');
await page.waitForTimeout(250);

for (const view of ['games', 'types', 'compare']) {
  await page.click(`.tab[data-view="${view}"]`);
  await page.waitForTimeout(250);
  record(`view ${view} renders`, (await page.$$eval('.card', (n) => n.length)) > 0);
}
await page.click('.tab[data-view="games"]');
await page.waitForSelector('tbody tr.selectable');
await page.click('tbody tr.selectable');
await page.waitForSelector('#game-detail');
record('game detail opens', (await page.$$eval('#game-detail .chart svg', (n) => n.length)) >= 1);

await page.click('.tab[data-view="overview"]');
await page.waitForTimeout(200);
await page.screenshot({ path: '/tmp/qp-shots/bundle.png', fullPage: true });

await browser.close();
const failed = checks.filter((c) => !c.ok);
console.log(JSON.stringify({ checks, errors, failed: failed.length }, null, 1));
process.exit(failed.length || errors.length ? 1 : 0);
