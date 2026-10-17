// Scroll-persistence e2e: with the reader scrolled up, a newly-arrived message
// must NOT yank the view to the bottom — the position is kept and a "새 메시지"
// pill appears. Uses a short viewport so the self-chat overflows.
//   NODE_PATH=<playwright-core> CHROME=<chrome> SELF_NAME=<row> node webui_scroll.js <base>
const { chromium } = require('playwright-core');
const BASE = process.argv[2];
const SELF = process.env.SELF_NAME;
function die(m){ console.error('FAIL: ' + m); process.exit(1); }
(async () => {
  const b = await chromium.launch({ executablePath: process.env.CHROME, headless: true });
  const ctx = await b.newContext({ viewport: { width: 900, height: 240 } });
  const p = await ctx.newPage();
  p.setDefaultTimeout(60000);
  try {
    await p.goto(BASE, { waitUntil: 'domcontentloaded' });
    await p.waitForFunction(() => document.querySelectorAll('#list .row').length > 0,
                            null, { timeout: 60000 });
    await p.evaluate(async (self) => {
      await fetch('/api/open', { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ room: self }) });
    }, SELF);
    // let want_open's clear propagate, then wait for a stable open (title + msgs)
    await p.waitForTimeout(1800);
    await p.waitForFunction(
      () => /^—.*—$/.test(document.getElementById('titletext').textContent.trim())
            && document.querySelectorAll('#msgs .msg').length > 3,
      null, { timeout: 60000 });
    await p.waitForTimeout(1500);
    const overflowNow = () => p.evaluate(() => {
      const m = document.getElementById('msgs');
      return m.scrollHeight - m.clientHeight;
    });
    // the message log is the *visible* device window, so its length varies —
    // top it up with filler sends until it overflows the short viewport
    for (let i = 0; i < 8 && (await overflowNow()) < 40; i++) {
      await p.evaluate(async (t) => {
        await fetch('/api/send', { method: 'POST',
          headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: t }) });
      }, 'fill-' + Date.now() + '-' + i);
      await p.waitForTimeout(2500);
    }
    if ((await overflowNow()) < 40) die('log did not overflow; cannot test scroll');
    await p.evaluate(() => { document.getElementById('msgs').scrollTop = 0; });
    const top0 = await p.evaluate(() => document.getElementById('msgs').scrollTop);

    // a new message arrives while we are scrolled up
    const MSG = 'scrolltest-' + Date.now();
    await p.evaluate(async (t) => {
      await fetch('/api/send', { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: t }) });
    }, MSG);
    await p.waitForFunction((t) => [...document.querySelectorAll('#msgs .msg')]
        .some(e => e.textContent.includes(t)), MSG, { timeout: 60000 });
    await p.waitForTimeout(500);

    const after = await p.evaluate(() => {
      const m = document.getElementById('msgs');
      const bottom = m.scrollHeight - m.clientHeight;
      return { top: m.scrollTop, bottom,
               pill: document.getElementById('newmsg').classList.contains('show') };
    });
    console.log(`top0=${top0} afterTop=${after.top} bottom=${after.bottom} pill=${after.pill}`);
    if (after.top > 60) die('scroll was yanked (top=' + after.top + ')');
    if (!after.pill) die('"새 메시지" pill not shown');
    console.log('PASS');
    await b.close();
    process.exit(0);
  } catch (e) {
    await b.close().catch(() => {});
    die(e.message);
  }
})();
