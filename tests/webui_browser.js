// Headless-browser e2e for `kakao webui`. Desktop by default; MOBILE=1 uses
// touch device emulation (iPhone-13-like) and exercises the single-screen
// list->conversation flow with taps and the [보내기]/[← 목록] buttons.
//
//   NODE_PATH=<playwright-core> CHROME=<chrome> SELF_NAME=<self-chat row>
//   [FILE=<path>] [MOBILE=1] node webui_browser.js <base-url> <message>
//
// SELF_NAME identifies the self-chat row to click; it is never printed.
const { chromium } = require('playwright-core');

const BASE = process.argv[2] || 'http://127.0.0.1:8800';
const MSG = process.argv[3] || ('e2e-web-' + Date.now());
const SELF = process.env.SELF_NAME;
const CHROME = process.env.CHROME;
const FILE = process.env.FILE;        // set -> file-attach mode instead of text
const MOBILE = !!process.env.MOBILE;  // set -> touch device emulation

function die(m) { console.error('FAIL: ' + m); process.exit(1); }
const TITLE_OPEN = () =>
  /^—.*—$/.test(document.getElementById('titletext').textContent.trim());

(async () => {
  if (!SELF) die('SELF_NAME env required');
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const ctx = MOBILE
    ? await browser.newContext({
        viewport: { width: 390, height: 844 }, deviceScaleFactor: 3,
        isMobile: true, hasTouch: true,
        userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) ' +
          'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1',
      })
    : await browser.newContext({ viewport: { width: 1200, height: 760 } });
  const page = await ctx.newPage();
  page.setDefaultTimeout(60000);
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));

  async function selfRow() {
    return page.evaluateHandle((self) => {
      for (const row of document.querySelectorAll('#list .row'))
        if (row.dataset.room === self) return row;
      return null;
    }, SELF);
  }

  try {
    await page.goto(BASE, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => document.querySelectorAll('#list .row').length > 0,
                               null, { timeout: 60000 });
    console.log('rooms rendered: ' + await page.evaluate(
      () => document.querySelectorAll('#list .row').length) + (MOBILE ? ' (mobile)' : ''));

    // open the self-chat by tapping/clicking its row
    const row = (await selfRow()).asElement();
    if (!row) die('self-chat row not found');
    if (MOBILE) { await row.tap(); } else { await row.click(); }
    console.log(MOBILE ? 'tapped self-chat row' : 'clicked self-chat row');

    if (MOBILE) {
      // conversation view: list hidden, back button shown
      await page.waitForFunction(() => document.body.dataset.view === 'convo',
                                 null, { timeout: 30000 });
      const listHidden = await page.evaluate(
        () => getComputedStyle(document.getElementById('list')).display === 'none');
      if (!listHidden) die('list not hidden in conversation view');
      console.log('switched to conversation view');
    }
    await page.waitForFunction(TITLE_OPEN, null, { timeout: 60000 });
    console.log('room open');

    if (FILE) {
      await page.evaluate(async (self) => {
        await fetch('/api/open', { method: 'POST',
          headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ room: self }) });
      }, SELF);
      await page.waitForFunction(TITLE_OPEN, null, { timeout: 60000 });
      await page.setInputFiles('#file', FILE);
      console.log('file selected');
      await page.waitForFunction(
        () => /sent\s*\(/i.test(document.getElementById('status').textContent),
        null, { timeout: 180000 });
      console.log('status: file send reported');
    } else {
      // type Korean; send via the button on mobile, via Enter on desktop
      await page.fill('#input', MSG);
      if (MOBILE) { await page.tap('#send'); } else { await page.press('#input', 'Enter'); }
      await page.waitForFunction(
        () => /sent/i.test(document.getElementById('status').textContent),
        null, { timeout: 60000 });
      console.log('status: send reported');
    }

    if (MOBILE && !FILE) {
      // back to the list via the title [←] button
      await page.tap('#back');
      await page.waitForFunction(() => document.body.dataset.view === 'list',
                                 null, { timeout: 30000 });
      const listShown = await page.evaluate(
        () => getComputedStyle(document.getElementById('list')).display !== 'none');
      if (!listShown) die('list not shown after back');
      console.log('returned to list');
    }

    if (errors.length) die('page errors: ' + errors.join(' | '));
    console.log('PASS');
    await browser.close();
    process.exit(0);
  } catch (e) {
    if (errors.length) console.error('page errors: ' + errors.join(' | '));
    await browser.close().catch(() => {});
    die(e.message);
  }
})();
