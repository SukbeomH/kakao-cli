// Console screen e2e (browser side): the live device screen loads into the
// console center on desktop and in mobile emulation. Real frame changes and tap
// mapping are verified device-side by webui_screen_check.py (e2e_screen.sh).
//   NODE_PATH=.. CHROME=.. [MOBILE=1] node webui_screen.js <base>
const { chromium } = require('playwright-core');
const BASE = process.argv[2], MOBILE = !!process.env.MOBILE;
function die(m){ console.error('FAIL: '+m); process.exit(1); }
(async () => {
  const b = await chromium.launch({ executablePath: process.env.CHROME, headless: true });
  const ctx = MOBILE
    ? await b.newContext({ viewport:{width:390,height:844}, deviceScaleFactor:2, isMobile:true, hasTouch:true })
    : await b.newContext({ viewport:{width:1200,height:760} });
  const p = await ctx.newPage(); p.setDefaultTimeout(60000);
  const errs=[]; p.on('pageerror',e=>errs.push(e.message));
  try {
    await p.goto(BASE, {waitUntil:'domcontentloaded'});
    await p.waitForFunction(()=>{ const i=document.getElementById('scr'); return i && i.naturalWidth>0; }, null, {timeout:60000});
    const r = await p.evaluate(()=>{ const i=document.getElementById('scr'), c=document.getElementById('conwrap').getBoundingClientRect();
      return {w:i.naturalWidth, h:i.naturalHeight, mode:document.getElementById('vmode').textContent, cw:Math.round(c.width), ch:Math.round(c.height)}; });
    console.log('frame '+r.w+'x'+r.h+' in '+r.cw+'x'+r.ch+' ('+r.mode+')'+(MOBILE?' mobile':''));
    if (errs.length) die('page errors: '+errs.join(' | '));
    console.log('PASS'); await b.close(); process.exit(0);
  } catch(e){ if(errs.length) console.error('page errors: '+errs.join(' | ')); await b.close().catch(()=>{}); die(e.message); }
})();
