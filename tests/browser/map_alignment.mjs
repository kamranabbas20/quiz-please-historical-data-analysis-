/* Pins must sit exactly where the tile grid says their coordinates are, or the
 * basemap and the data would disagree with each other.
 *
 * The origin is recovered from a tile's own x/y and its {z}/{x}/{y} href, then
 * every pin's position is predicted independently from the venue's latitude and
 * longitude and compared with what was drawn.
 *
 * Usage: node tests/browser/map_alignment.mjs */
import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs';
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1280, height: 1200 } });
const BASE = process.env.APP_URL || 'http://127.0.0.1:8765/';
await p.goto(BASE, { waitUntil: 'networkidle' });
await p.waitForSelector('.kpi .value');
await p.click('.tab[data-view="venues"]');
await p.waitForTimeout(300);

const result = await p.evaluate(() => {
  const image = document.querySelector('.map-tiles image');
  const [z, tx, ty] = image.getAttribute('href').match(/(\d+)\/(\d+)\/(\d+)\.png/).slice(1).map(Number);
  const originX = Number(image.getAttribute('x')) - tx * 256;
  const originY = Number(image.getAttribute('y')) - ty * 256;

  const lonToX = (lon) => ((lon + 180) / 360) * 2 ** z * 256;
  const latToY = (lat) => {
    const r = (lat * Math.PI) / 180;
    return ((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * 2 ** z * 256;
  };

  const venues = window.__QP_DATA__
    ? window.__QP_DATA__.cities.baku.venues
    : null;
  return { z, originX, originY, venues: venues ? venues.filter((v) => v.has_coords).slice(0, 3) : null,
    pins: [...document.querySelectorAll('.map-pin circle')].filter((c) => c.getAttribute('fill') !== 'transparent')
      .slice(0, 6).map((c) => ({ cx: Number(c.getAttribute('cx')), cy: Number(c.getAttribute('cy')), r: Number(c.getAttribute('r')) })),
    predict: (() => null)() };
});

// Fetch the venue list over HTTP (served app has no embedded data).
const data = await (await fetch(new URL('data/baku.json', BASE))).json();
const located = data.venues.filter((v) => v.has_coords).sort((a, b) => b.games - a.games);
const { z, originX, originY } = result;
const lonToX = (lon) => ((lon + 180) / 360) * 2 ** z * 256;
const latToY = (lat) => {
  const r = (lat * Math.PI) / 180;
  return ((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * 2 ** z * 256;
};

let worst = 0;
for (const [i, venue] of located.slice(0, 6).entries()) {
  const expected = { x: lonToX(venue.lon) + originX, y: latToY(venue.lat) + originY };
  const pin = result.pins[i];
  const dx = Math.abs(expected.x - pin.cx);
  const dy = Math.abs(expected.y - pin.cy);
  worst = Math.max(worst, dx, dy);
  console.log(`${venue.title.padEnd(20)} expected ${expected.x.toFixed(1)},${expected.y.toFixed(1)}  drawn ${pin.cx.toFixed(1)},${pin.cy.toFixed(1)}  off by ${dx.toFixed(2)},${dy.toFixed(2)}px`);
}
console.log('worst offset from the tile grid:', worst.toFixed(3), 'px');
await b.close();
process.exit(worst < 0.01 ? 0 : 1);
