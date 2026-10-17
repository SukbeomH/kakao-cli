// Stage-C e2e: open the live screen overlay, confirm frames load & advance, then
// close it. MOBILE=1 uses touch emulation. Pairs with e2e_screen.sh (adb tap check).
const { chromium } = require('playwright-core');
const BASE = process.argv[2];
const MOBILE = !!process.env.MOBILE;
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
    await p.waitForFunction(()=>document.querySelectorAll('#list .row').length>0, null, {timeout:60000});
    // open the screen overlay via the title-bar button
    await (MOBILE ? p.tap('#scrbtn2') : p.click('#scrbtn2'));
    await p.waitForFunction(()=>document.getElementById('screenovl').classList.contains('show'), null, {timeout:10000});
    // the <img> gets a real frame (naturalWidth>0) — can take a bit on first load
    await p.waitForFunction(()=>{ const i=document.getElementById('scr'); return i && i.naturalWidth>0; }, null, {timeout:60000});
    const dim = await p.evaluate(()=>{ const i=document.getElementById('scr'); return [i.naturalWidth,i.naturalHeight]; });
    console.log('frame loaded '+dim[0]+'x'+dim[1]+(MOBILE?' (mobile)':''));
    await p.waitForTimeout(2500);
    const fps = await p.evaluate(()=>document.getElementById('fps').textContent);
    console.log('fps='+(fps||'(static)')+' (real frame-change verified by e2e_screen.sh)');
    // close
    await (MOBILE ? p.tap('#scrclose') : p.click('#scrclose'));
    await p.waitForFunction(()=>!document.getElementById('screenovl').classList.contains('show'), null, {timeout:10000});
    console.log('closed');
    if (errs.length) die('page errors: '+errs.join(' | '));
    console.log('PASS'); await b.close(); process.exit(0);
  } catch(e){ if(errs.length) console.error('page errors: '+errs.join(' | ')); await b.close().catch(()=>{}); die(e.message); }
})();
