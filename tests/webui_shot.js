// Console screenshots: OUT=<png> [MOBILE=1] node webui_shot.js <base>
const { chromium } = require('playwright-core');
const BASE = process.argv[2], MOBILE = !!process.env.MOBILE;
(async () => {
  const b = await chromium.launch({ executablePath: process.env.CHROME, headless: true });
  const ctx = MOBILE
    ? await b.newContext({ viewport:{width:390,height:844}, deviceScaleFactor:2, isMobile:true, hasTouch:true })
    : await b.newContext({ viewport:{width:1400,height:860} });
  const p = await ctx.newPage(); p.setDefaultTimeout(60000);
  await p.goto(BASE, {waitUntil:'domcontentloaded'});
  await p.waitForFunction(()=>{ const i=document.getElementById('scr'), v=document.getElementById('vcanvas');
    return (i && i.naturalWidth>0) || (v && v.width>0 && v.dataset.frames>0); }, null, {timeout:60000});
  await p.waitForFunction(()=>document.querySelectorAll('#flist-android .frow').length>1, null, {timeout:30000});
  await p.waitForTimeout(Number(process.env.SETTLE || 1500));
  await p.screenshot({ path: process.env.OUT || '/tmp/console.png' });
  console.log('shot ' + (process.env.OUT || '/tmp/console.png')); await b.close();
})();
