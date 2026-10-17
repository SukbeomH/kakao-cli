// Stage-A feature e2e: 2-line room rows, quick switcher (/), textarea newline
// (Shift+Enter), then open the self-chat via the switcher and send MSG (Enter).
// e2e_webui.sh does the adb readback of MSG afterwards.
//   NODE_PATH=.. CHROME=.. SELF_NAME=<row> node webui_features.js <base> <msg>
const { chromium } = require('playwright-core');
const BASE = process.argv[2];
const MSG = process.argv[3] || ('e2e-feat-' + Date.now());
const SELF = process.env.SELF_NAME;
function die(m){ console.error('FAIL: ' + m); process.exit(1); }
(async () => {
  const b = await chromium.launch({ executablePath: process.env.CHROME, headless: true });
  const ctx = await b.newContext({ viewport: { width: 1200, height: 760 } });
  const p = await ctx.newPage();
  p.setDefaultTimeout(60000);
  const errs = []; p.on('pageerror', e => errs.push(e.message));
  try {
    await p.goto(BASE, { waitUntil: 'domcontentloaded' });
    await p.waitForFunction(() => document.querySelectorAll('#list .row').length > 0,
                            null, { timeout: 60000 });

    // 1) two-line rows: each row has a name line and a preview line
    const twoLine = await p.evaluate(() => {
      const r = document.querySelector('#list .row');
      return !!(r && r.querySelector('.r1 .nm') && r.querySelector('.r2 .pv'));
    });
    if (!twoLine) die('rows are not two-line (.r1 .nm / .r2 .pv missing)');
    console.log('two-line rows OK');

    // 2) quick switcher via "/": filter to self and Enter opens it
    await p.keyboard.press('/');
    await p.waitForFunction(() => document.getElementById('switcher').classList.contains('show'),
                            null, { timeout: 10000 });
    await p.type('#qin', SELF);
    await p.waitForFunction((s) => {
      const el = document.querySelector('#qres .row .nm');
      return el && el.textContent.includes(s);
    }, SELF, { timeout: 10000 });
    await p.keyboard.press('Enter');
    await p.waitForFunction(() =>
      /^—.*—$/.test(document.getElementById('titletext').textContent.trim()),
      null, { timeout: 60000 });
    console.log('quick switcher opened room');

    // 3) textarea newline with Shift+Enter
    await p.click('#input');
    await p.type('#input', 'line1');
    await p.keyboard.down('Shift'); await p.keyboard.press('Enter'); await p.keyboard.up('Shift');
    await p.type('#input', 'line2');
    const val = await p.inputValue('#input');
    if (val !== 'line1\nline2') die('Shift+Enter did not insert newline: ' + JSON.stringify(val));
    console.log('textarea newline OK');

    // 4) send MSG with Enter (single line) — readback happens in the shell wrapper
    await p.fill('#input', MSG);
    await p.press('#input', 'Enter');
    await p.waitForFunction(() => /sent/i.test(document.getElementById('status').textContent),
                            null, { timeout: 60000 });
    console.log('status: send reported');

    if (errs.length) die('page errors: ' + errs.join(' | '));
    console.log('PASS');
    await b.close(); process.exit(0);
  } catch (e) {
    if (errs.length) console.error('page errors: ' + errs.join(' | '));
    await b.close().catch(() => {});
    die(e.message);
  }
})();
