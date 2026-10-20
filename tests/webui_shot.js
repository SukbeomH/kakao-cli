// Console screenshots (device screen body blurred): OUT=<png> [MOBILE=1] [VIDEO=png|wc|mse]
//   node webui_shot.js <base>
const { chromium } = require('playwright-core');
const BASE = process.argv[2], MOBILE = !!process.env.MOBILE;
// privacy: blur the device screen body (names/messages) but keep header/tab bars
const MASK_CSS = '.scrwrap::after{content:"";position:absolute;left:0;right:0;top:13%;bottom:11%;' +
  'backdrop-filter:blur(9px);-webkit-backdrop-filter:blur(9px);pointer-events:none}';
(async () => {
  const b = await chromium.launch({ executablePath: process.env.CHROME, headless: true });
  const ctx = MOBILE
    ? await b.newContext({ viewport:{width:390,height:844}, deviceScaleFactor:2, isMobile:true, hasTouch:true })
    : await b.newContext({ viewport:{width:1400,height:860} });
  const p = await ctx.newPage(); p.setDefaultTimeout(60000);
  await p.goto(BASE + (process.env.VIDEO ? '?video=' + process.env.VIDEO : ''), {waitUntil:'domcontentloaded'});
  await p.waitForFunction(()=>{ const i=document.getElementById('scr'), c=document.getElementById('vcanvas'),
      v=document.getElementById('vvideo');
    return (!i.hidden && i.naturalWidth>0) || (!c.hidden && +c.dataset.frames>0) || (!v.hidden && v.videoWidth>0); },
    null, {timeout:60000});
  await p.waitForFunction(()=>document.querySelectorAll('#flist-android .frow').length>1, null, {timeout:30000});
  await p.waitForTimeout(Number(process.env.SETTLE || 1500));
  if (process.env.MASK !== '0') await p.addStyleTag({content: MASK_CSS});
  await p.screenshot({ path: process.env.OUT || '/tmp/console.png' });
  console.log('shot ' + (process.env.OUT || '/tmp/console.png')); await b.close();
})();
