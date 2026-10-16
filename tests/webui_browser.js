// Headless-browser e2e for `kakao webui`: loads the page, clicks the self-chat
// row, types Korean into the input, presses Enter, and asserts the status bar
// reports a send. Proves the inlined JS actually drives the API (a curl test
// would not). Uses playwright-core + a cached Chromium; no network downloads.
//
//   NODE_PATH=<playwright-core dir> CHROME=<chrome bin> SELF_NAME=<self-chat row>
//   node webui_browser.js <base-url> <message>
//
// SELF_NAME identifies the self-chat row to click; it is never printed.
const { chromium } = require('playwright-core');

const BASE = process.argv[2] || 'http://127.0.0.1:8800';
const MSG = process.argv[3] || ('e2e-web-' + Date.now());
const SELF = process.env.SELF_NAME;
const CHROME = process.env.CHROME;

function die(m) { console.error('FAIL: ' + m); process.exit(1); }

(async () => {
  if (!SELF) die('SELF_NAME env required');
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const page = await browser.newPage();
  page.setDefaultTimeout(60000);
  try {
    await page.goto(BASE, { waitUntil: 'domcontentloaded' });
    // wait for the room list to populate (worker.list_rooms)
    await page.waitForFunction(() => document.querySelectorAll('#list .row').length > 0,
                               null, { timeout: 60000 });
    console.log('rooms rendered: ' + await page.evaluate(
      () => document.querySelectorAll('#list .row').length));
    // click the self-chat row (matched by exact name, never logged)
    const clicked = await page.evaluate((self) => {
      for (const row of document.querySelectorAll('#list .row')) {
        if (row.textContent.replace(/\s*\(\d+\)\s*$/, '').trim() === self) {
          row.click();
          return true;
        }
      }
      return false;
    }, SELF);
    if (!clicked) die('self-chat row not found');
    console.log('clicked self-chat row');
    // wait until the room is confirmed open (title shows "— … —")
    await page.waitForFunction(
      () => /^—.*—$/.test(document.getElementById('title').textContent.trim()),
      null, { timeout: 60000 });
    console.log('room open');
    // type Korean into the input and send with Enter (exercises the keydown path)
    await page.focus('#input');
    await page.fill('#input', MSG);
    await page.press('#input', 'Enter');
    // status bar should report a send within a couple of poll cycles
    await page.waitForFunction(
      () => /sent/i.test(document.getElementById('status').textContent),
      null, { timeout: 60000 });
    console.log('status: send reported');
    console.log('PASS');
    await browser.close();
    process.exit(0);
  } catch (e) {
    await browser.close().catch(() => {});
    die(e.message);
  }
})();
