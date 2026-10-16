// Headless-browser e2e for `kakao webui`: loads the page, clicks the self-chat
// row, then either types Korean + Enter (text mode) or attaches a file via the
// '+' picker (file mode, when FILE is set), and asserts the status bar reports
// a send. Proves the inlined JS actually drives the API (a curl test would not).
// Uses playwright-core + a cached Chromium; no network downloads.
//
//   NODE_PATH=<playwright-core dir> CHROME=<chrome bin> SELF_NAME=<self-chat row>
//   [FILE=<local path>] node webui_browser.js <base-url> <message>
//
// SELF_NAME identifies the self-chat row to click; it is never printed.
const { chromium } = require('playwright-core');

const BASE = process.argv[2] || 'http://127.0.0.1:8800';
const MSG = process.argv[3] || ('e2e-web-' + Date.now());
const SELF = process.env.SELF_NAME;
const CHROME = process.env.CHROME;
const FILE = process.env.FILE;   // set -> file-attach mode instead of text

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
    if (FILE) {
      // Await the open POST first so the sendfile POST is enqueued after it
      // (a human clicks a room then attaches much later; automation races).
      await page.evaluate(async (self) => {
        await fetch('/api/open', { method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ room: self }) });
      }, SELF);
      await page.waitForFunction(
        () => /^—.*—$/.test(document.getElementById('title').textContent.trim()),
        null, { timeout: 60000 });
      // attach a file via the '+' picker (setInputFiles works on hidden inputs)
      await page.setInputFiles('#file', FILE);
      console.log('file selected');
      // MediaStore retries + DocumentsUI make this slow; status ends "sent (…)"
      await page.waitForFunction(
        () => /sent\s*\(/i.test(document.getElementById('status').textContent),
        null, { timeout: 180000 });
      console.log('status: file send reported');
    } else {
      // type Korean into the input and send with Enter (exercises the keydown path)
      await page.focus('#input');
      await page.fill('#input', MSG);
      await page.press('#input', 'Enter');
      await page.waitForFunction(
        () => /sent/i.test(document.getElementById('status').textContent),
        null, { timeout: 60000 });
      console.log('status: send reported');
    }
    console.log('PASS');
    await browser.close();
    process.exit(0);
  } catch (e) {
    await browser.close().catch(() => {});
    die(e.message);
  }
})();
