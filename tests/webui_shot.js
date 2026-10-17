// Screenshot helper for the web UI.
//   Desktop:  OUT=<png>                 node webui_shot.js <base>
//   Mobile:   MOBILE=1 OUT_LIST=<png> OUT_CONVO=<png>  node webui_shot.js <base>
// SELF_NAME picks the self-chat row to open (never printed).
const { chromium } = require('playwright-core');
const BASE = process.argv[2];
const SELF = process.env.SELF_NAME;
const MOBILE = !!process.env.MOBILE;

async function openSelf(p) {
  await p.evaluate(async (self) => {
    await fetch('/api/open', { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ room: self }) });
  }, SELF);
  // let want_open's room-clear propagate to the page before we check (otherwise
  // the page still shows the previously-open room and we'd match the stale state)
  await p.waitForTimeout(1800);
  // wait for the room to be *stably* open: title set AND messages loaded (so we
  // don't capture the brief "(select a room)" gap while want_open re-opens it)
  await p.waitForFunction(
    () => /^—.*—$/.test(document.getElementById('titletext').textContent.trim())
          && document.querySelectorAll('#msgs .msg').length > 0,
    null, { timeout: 60000 });
  await p.waitForTimeout(2000);
}

(async () => {
  const b = await chromium.launch({ executablePath: process.env.CHROME, headless: true });
  const ctx = MOBILE
    ? await b.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2,
        isMobile: true, hasTouch: true })
    : await b.newContext({ viewport: { width: 1200, height: 760 } });
  const p = await ctx.newPage();
  p.setDefaultTimeout(60000);
  await p.goto(BASE, { waitUntil: 'domcontentloaded' });
  await p.waitForFunction(() => document.querySelectorAll('#list .row').length > 0,
                          null, { timeout: 60000 });
  if (MOBILE) {
    await p.waitForTimeout(600);
    await p.screenshot({ path: process.env.OUT_LIST || '/tmp/webui_m_list.png' });
    // open the self-chat (tap the row) -> conversation view
    await p.evaluate((self) => {
      for (const row of document.querySelectorAll('#list .row'))
        if (row.textContent.replace(/\s*\(\d+\)\s*$/, '').trim() === self) { row.click(); return; }
    }, SELF);
    await openSelf(p);
    await p.screenshot({ path: process.env.OUT_CONVO || '/tmp/webui_m_convo.png' });
    console.log('mobile shots done');
  } else {
    await openSelf(p);
    await p.screenshot({ path: process.env.OUT || '/tmp/webui_shot.png' });
    console.log('desktop shot done');
  }
  await b.close();
})();
