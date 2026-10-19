// Regression for the IME double-send bug: an Enter fired while a Korean syllable
// is still composing (isComposing=true) must NOT send — only the real Enter
// after composition ends sends, exactly once, with the full text.
//   NODE_PATH=.. CHROME=.. SELF_NAME=<row> node webui_ime.js <base>
const { chromium } = require('playwright-core');
const BASE = process.argv[2];
const SELF = process.env.SELF_NAME;
function die(m){ console.error('FAIL: '+m); process.exit(1); }
(async () => {
  const b = await chromium.launch({ executablePath: process.env.CHROME, headless: true });
  const p = await (await b.newContext({ viewport:{width:1200,height:760} })).newPage();
  p.setDefaultTimeout(60000);
  try {
    await p.goto(BASE, { waitUntil:'domcontentloaded' });
    await p.waitForFunction(()=>document.querySelectorAll('#list .row').length>0, null, {timeout:60000});
    await p.evaluate(async (self)=>{ await fetch('/api/open',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({room:self})}); }, SELF);
    await p.waitForTimeout(1500);
    // spy on /api/send without actually sending to the device
    await p.evaluate(()=>{
      window.__sends=[];
      const orig=window.fetch;
      window.fetch=(u,o)=>{ if(typeof u==='string' && u.startsWith('/api/send')){
        try{ window.__sends.push(JSON.parse(o.body).text); }catch(e){}
        return Promise.resolve(new Response('{"ok":true}',{status:200})); }
        return orig(u,o); };
    });
    // type text, then fire Enter *while composing* (should NOT send), then Enter after
    const result = await p.evaluate(()=>{
      const inp=document.getElementById('input');
      inp.focus(); inp.value='가나다';
      inp.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',isComposing:true,bubbles:true,cancelable:true}));
      const during = window.__sends.length;
      inp.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',isComposing:false,bubbles:true,cancelable:true}));
      const after = window.__sends.slice();
      return {during, after};
    });
    if (result.during !== 0) die('sent during composition ('+result.during+')');
    if (result.after.length !== 1 || result.after[0] !== '가나다')
      die('expected one send of "가나다", got '+JSON.stringify(result.after));
    console.log('IME guard OK: no send while composing, one full send after');
    console.log('PASS'); await b.close(); process.exit(0);
  } catch(e){ await b.close().catch(()=>{}); die(e.message); }
})();
