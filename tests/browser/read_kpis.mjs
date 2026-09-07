/* Reads what the dashboard actually renders, for cross-checking against the
 * raw source data. Usage: node tests/browser/read_kpis.mjs <team-substring>... */
import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs';

const BASE = process.env.APP_URL || 'http://127.0.0.1:8765/';
const wanted = process.argv.slice(2);

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
const problems = [];
page.on('console', (msg) => { if (msg.type() === 'error') problems.push(msg.text()); });
page.on('pageerror', (error) => problems.push(error.message));
await page.goto(BASE, { waitUntil: 'networkidle' });
await page.waitForSelector('.kpi .value');

const out = { city: await page.$eval('#city', (el) => el.value), teams: {}, problems };

for (const needle of wanted) {
  const value = await page.$$eval('#team option', (options, text) => {
    const found = options.find((option) => option.textContent.includes(text));
    return found ? found.value : null;
  }, needle);
  if (!value) { out.teams[needle] = { error: 'team not in list' }; continue; }

  await page.selectOption('#team', value);
  await page.waitForTimeout(200);

  const kpis = await page.$$eval('.kpi', (nodes) => Object.fromEntries(nodes.map((node) => [
    node.querySelector('.label').textContent, node.querySelector('.value').textContent,
  ])));

  await page.click('.tab[data-view="games"]');
  await page.waitForTimeout(200);
  const rows = await page.$$eval('#games-table tbody tr', (nodes) => nodes.map(
    (node) => [...node.querySelectorAll('td')].map((cell) => cell.textContent),
  ));

  await page.click('.tab[data-view="types"]');
  await page.waitForTimeout(200);
  const types = await page.$$eval('table tbody tr', (nodes) => nodes.map(
    (node) => [...node.querySelectorAll('td')].map((cell) => cell.textContent),
  ));

  await page.click('.tab[data-view="overview"]');
  await page.waitForTimeout(150);
  out.teams[needle] = { key: value, kpis, gameRows: rows, typeRows: types };
}

await browser.close();
console.log(JSON.stringify(out));
