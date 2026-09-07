/* The basemap must degrade, not disappear.
 *
 * Every tile host is unreachable in CI, so this exercises the whole cascade:
 * each provider is tried in turn, and when none answers the map still draws its
 * venues and says why there is no basemap under them.
 *
 * Usage: node tests/browser/basemap_fallback.mjs [baseUrl] */
import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs';

const BASE = process.argv[2] || process.env.APP_URL || 'http://127.0.0.1:8765/';
const checks = [];
const record = (name, ok, detail) => checks.push({ name, ok: Boolean(ok), detail });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 1200 } });
const hosts = [];
page.on('request', (request) => {
  const url = request.url();
  if (/\/\d+\/\d+\/\d+\.png/.test(url)) {
    const host = new URL(url).host;
    if (hosts[hosts.length - 1] !== host) hosts.push(host);
  }
});

await page.goto(BASE, { waitUntil: 'networkidle' });
await page.waitForSelector('.kpi .value');
await page.click('.tab[data-view="venues"]');
await page.waitForTimeout(6000);        // let the cascade run out

record('every provider was tried', hosts.length >= 2, hosts);
record('venues are still drawn without a basemap',
  (await page.$$eval('.map-pin', (n) => n.length)) > 1);
record('the map says why it has no basemap',
  (await page.$eval('.chart svg', (el) => el.textContent)).includes('не загрузилась'));
record('no stale attribution is left behind',
  !(await page.$eval('.chart svg', (el) => el.textContent)).includes('CARTO'));
record('the scale bar survives', (await page.$eval('.chart svg', (el) => el.textContent)).includes('км'));

// The picker lets a reader force one source, or turn the basemap off.
await page.selectOption('#basemap', 'none');
await page.waitForTimeout(400);
record('turning the basemap off requests no tiles',
  (await page.$$eval('.map-tiles image', (n) => n.length)) === 0);
record('venues remain with the basemap off',
  (await page.$$eval('.map-pin', (n) => n.length)) > 1);

await page.selectOption('#basemap', 'CARTO light');
await page.waitForTimeout(600);
const forced = await page.$$eval('.map-tiles image', (n) => n.map((i) => i.getAttribute('href')));
record('forcing a provider uses only that host',
  forced.length > 0 && forced.every((url) => url.includes('cartocdn')), forced.slice(0, 2));

await page.selectOption('#basemap', 'auto');
await page.waitForTimeout(300);
await browser.close();

const failed = checks.filter((check) => !check.ok);
console.log(JSON.stringify({ checks, failed: failed.length }, null, 1));
process.exit(failed.length ? 1 : 0);
