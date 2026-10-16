// Headless-browser e2e for webui *receive*: open the self-chat, click a
// downloadable bubble (⤓), and capture the browser download the page triggers
// (export → Save-on-device → pull → /api/download). Verifies the UI path end to
// end, not just the API.
//
//   NODE_PATH=<playwright-core> CHROME=<chrome> SELF_NAME=<row> \
//   MATCH=<substring> OUT=<save path> node webui_recv.js <base-url>
//
// MATCH picks the bubble to click (e.g. ".zip" for a file, "[Photo]" for an image).
const { chromium } = require('playwright-core');
const BASE = process.argv[2];
const SELF = process.env.SELF_NAME;
const MATCH = process.env.MATCH || '[Photo]';
const OUT = process.env.OUT || '/tmp/recv.bin';
function die(m){ console.error('FAIL: ' + m); process.exit(1); }
(async () => {
  const b = await chromium.launch({ executablePath: process.env.CHROME, headless: true });
  const p = await b.newPage({ acceptDownloads: true });
  p.setDefaultTimeout(120000);
  try {
    await p.goto(BASE, { waitUntil: 'domcontentloaded' });
    await p.waitForFunction(() => document.querySelectorAll('#list .row').length > 0,
                            null, { timeout: 60000 });
    await p.evaluate(async (self) => {
      await fetch('/api/open', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ room: self }) });
    }, SELF);
    // wait for a clickable downloadable bubble matching MATCH to render
    await p.waitForFunction((m) => {
      for (const el of document.querySelectorAll('#msgs .msg.dl'))
        if (el.textContent.includes(m)) return true;
      return false;
    }, MATCH, { timeout: 60000 });
    const target = await p.evaluateHandle((m) => {
      for (const el of document.querySelectorAll('#msgs .msg.dl'))
        if (el.textContent.includes(m)) return el;
      return null;
    }, MATCH);
    console.log('clicking downloadable bubble');
    const [download] = await Promise.all([
      p.waitForEvent('download', { timeout: 120000 }),
      target.asElement().click(),
    ]);
    await download.saveAs(OUT);
    console.log('downloaded: ' + (download.suggestedFilename() || '(noname)'));
    console.log('PASS');
    await b.close();
    process.exit(0);
  } catch (e) {
    await b.close().catch(() => {});
    die(e.message);
  }
})();
