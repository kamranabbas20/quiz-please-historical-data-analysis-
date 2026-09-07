/* Reports what the app shows before and after switching city.
 * Usage: node tests/browser/city_switch.mjs <baseUrl> */
import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs';

const BASE = process.argv[2];
const browser = await chromium.launch();
const page = await browser.newPage();
const errors = [];
page.on('console', (msg) => { if (msg.type() === 'error') errors.push(msg.text()); });
page.on('pageerror', (error) => errors.push(error.message));

await page.goto(BASE, { waitUntil: 'networkidle' });
await page.waitForSelector('.kpi .value');

const options = (selector) => page.$$eval(`${selector} option`, (nodes) => nodes.map((n) => n.textContent));
const values = (selector) => page.$$eval(`${selector} option`, (nodes) => nodes.map((n) => n.value));
const kpi = (label) => page.$$eval('.kpi', (nodes, text) => {
  const found = nodes.find((node) => node.querySelector('.label').textContent === text);
  return found ? found.querySelector('.value').textContent : null;
}, label);

const cities = await values('#city');
const report = {
  cities,
  errors,
  teamsFirst: await options('#team'),
  typesFirst: await options('#family'),
  coverageFirst: await page.$eval('#coverage', (el) => el.textContent),
};

await page.selectOption('#city', cities[1]);
await page.waitForTimeout(500);

report.teamsSecond = await options('#team');
report.typesSecond = await options('#family');
report.coverageSecond = await page.$eval('#coverage', (el) => el.textContent);
report.kpiGamesSecond = await kpi('Игр с результатами');
report.selectedCity = await page.$eval('#city', (el) => el.value);

await browser.close();
console.log(JSON.stringify(report));
process.exit(errors.length ? 1 : 0);
