/* Screenshots in dark mode and at phone width, to eyeball layout. */
import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs';
const BASE = process.env.APP_URL || 'http://127.0.0.1:8765/';
const shots = process.env.SHOT_DIR || '/tmp/qp-shots';
const browser = await chromium.launch();

const dark = await browser.newPage({ viewport: { width: 1280, height: 1000 }, colorScheme: 'dark' });
await dark.goto(BASE, { waitUntil: 'networkidle' });
await dark.waitForSelector('.kpi .value');
await dark.screenshot({ path: `${shots}/dark.png`, fullPage: true });

const phone = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
await phone.goto(BASE, { waitUntil: 'networkidle' });
await phone.waitForSelector('.kpi .value');
const overflow = await phone.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
await phone.screenshot({ path: `${shots}/phone.png`, fullPage: true });

await browser.close();
console.log(JSON.stringify({ horizontalOverflowOnPhone: overflow }));
process.exit(overflow ? 1 : 0);
