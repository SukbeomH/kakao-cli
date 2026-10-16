// Screenshot helper: load the web UI, open the self-chat, capture a PNG.
//   NODE_PATH=<playwright-core> CHROME=<chrome> SELF_NAME=<row> \
//   node webui_shot.js <base-url> <out.png>
const { chromium } = require('playwright-core');
const BASE = process.argv[2];
const OUT = process.argv[3] || '/tmp/webui.png';
const SELF = process.env.SELF_NAME;
(async () => {
  const b = await chromium.launch({ executablePath: process.env.CHROME, headless: true });
  const p = await b.newPage({ viewport: { width: 1200, height: 760 } });
  p.setDefaultTimeout(60000);
  await p.goto(BASE, { waitUntil: 'domcontentloaded' });
  await p.waitForFunction(() => document.querySelectorAll('#list .row').length > 0,
                          null, { timeout: 60000 });
  if (SELF) {
    await p.evaluate(async (self) => {
      await fetch('/api/open', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ room: self }) });
    }, SELF);
    await p.waitForFunction(
      () => /^—.*—$/.test(document.getElementById('title').textContent.trim()),
      null, { timeout: 60000 });
    await p.waitForTimeout(2500);
  }
  await p.screenshot({ path: OUT });
  await b.close();
  console.log('shot: ' + OUT);
})();
