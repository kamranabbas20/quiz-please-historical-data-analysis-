/* Headless smoke test: loads the dashboard, walks every view, and reports any
 * console error or unhandled rejection. Run with: node tests/browser/smoke.mjs */
import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs';

const BASE = process.env.APP_URL || 'http://127.0.0.1:8765/';
const problems = [];
const shots = process.env.SHOT_DIR || '/tmp/qp-shots';

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
const TILE_NOISE = /tile\.openstreetmap|ERR_TUNNEL_CONNECTION_FAILED|Failed to load resource/;
page.on('console', (msg) => {
  // Basemap tiles come from a third-party host; the map is built to survive
  // them not loading, so their failure is not a page error.
  if (msg.type() === 'error' && !TILE_NOISE.test(msg.text())) problems.push(`console: ${msg.text()}`);
});
page.on('pageerror', (error) => problems.push(`pageerror: ${error.message}`));

await page.goto(BASE, { waitUntil: 'networkidle' });
await page.waitForSelector('.kpi .value');

const report = {};
report.city = await page.$eval('#city', (el) => el.options.length);
report.cityValue = await page.$eval('#city', (el) => el.value);
report.teams = await page.$eval('#team', (el) => el.options.length);
report.team = await page.$eval('#team', (el) => el.options[el.selectedIndex].textContent);
report.coverage = await page.$eval('#coverage', (el) => el.textContent);
report.kpis = await page.$$eval('.kpi', (nodes) => nodes.map((node) => [
  node.querySelector('.label').textContent, node.querySelector('.value').textContent,
]));
report.charts = await page.$$eval('.chart svg', (nodes) => nodes.length);

await page.screenshot({ path: `${shots}/overview.png`, fullPage: true });

for (const view of ['games', 'types', 'compare']) {
  await page.click(`.tab[data-view="${view}"]`);
  await page.waitForTimeout(250);
  report[`${view}_cards`] = await page.$$eval('.card', (nodes) => nodes.length);
  report[`${view}_charts`] = await page.$$eval('.chart svg', (nodes) => nodes.length);
  await page.screenshot({ path: `${shots}/${view}.png`, fullPage: true });
}

// Game detail: click the first row of the games table.
await page.click('.tab[data-view="games"]');
await page.waitForSelector('tbody tr.selectable');
await page.click('tbody tr.selectable');
await page.waitForSelector('#game-detail');
report.detailHeading = await page.$eval('#game-detail .section-title', (el) => el.textContent);
report.detailCharts = await page.$$eval('#game-detail .chart svg', (nodes) => nodes.length);
await page.screenshot({ path: `${shots}/detail.png`, fullPage: true });

// Tooltip on hover over the timeline.
await page.click('.tab[data-view="overview"]');
await page.waitForSelector('.chart svg');
const box = await page.$eval('.chart svg rect[fill="transparent"]', (el) => {
  const r = el.getBoundingClientRect();
  return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
});
await page.mouse.move(box.x, box.y);
await page.waitForTimeout(150);
report.tooltipVisible = await page.$eval('.tooltip', (el) => !el.hidden);
report.tooltipText = await page.$eval('.tooltip', (el) => el.textContent.slice(0, 80));

await browser.close();
console.log(JSON.stringify({ report, problems }, null, 1));
process.exit(problems.length ? 1 : 0);
