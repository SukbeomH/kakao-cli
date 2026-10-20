// Console IME regression: keyboard capture (kbcap) must not send anything while
// a Korean syllable is composing — no text and no Enter keycode — and must send
// the committed text exactly once after compositionend (the old "last syllable
// sent twice" bug). Network calls are spied, nothing reaches the device.
//   NODE_PATH=.. CHROME=.. node webui_ime.js <base>
const { chromium } = require('playwright-core');
const BASE = process.argv[2];
function die(m){ console.error('FAIL: '+m); process.exit(1); }
(async () => {
  const b = await chromium.launch({ executablePath: process.env.CHROME, headless: true });
  const p = await (await b.newContext({ viewport:{width:1200,height:760} })).newPage();
  p.setDefaultTimeout(60000);
  try {
    await p.goto(BASE, { waitUntil:'domcontentloaded' });
    await p.waitForSelector('#kbcap');
    await p.evaluate(()=>{
      window.__calls=[]; const orig=window.fetch;
      window.fetch=(u,o)=>{ if(typeof u==='string' && (u==='/api/screen/text'||u==='/api/screen/key')){
        window.__calls.push([u, JSON.parse(o.body)]); return Promise.resolve(new Response('{"ok":true}')); }
        return orig(u,o); };
    });
    const r = await p.evaluate(async ()=>{
      const k=document.getElementById('kbcap'); k.focus();
      k.dispatchEvent(new CompositionEvent('compositionstart',{data:''}));
      k.value='가나다';
      k.dispatchEvent(new InputEvent('input',{data:'다',isComposing:true,bubbles:true}));
      k.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',isComposing:true,bubbles:true,cancelable:true}));
      await new Promise(res=>setTimeout(res,300));
      const during = window.__calls.length;
      k.dispatchEvent(new CompositionEvent('compositionend',{data:'가나다'}));
      k.dispatchEvent(new InputEvent('input',{data:'',isComposing:false,bubbles:true}));  // Firefox order
      await new Promise(res=>setTimeout(res,400));
      return {during, calls: window.__calls};
    });
    if (r.during !== 0) die('sent while composing: '+JSON.stringify(r.calls));
    const texts = r.calls.filter(c=>c[0]==='/api/screen/text').map(c=>c[1].text);
    const keys = r.calls.filter(c=>c[0]==='/api/screen/key');
    if (texts.length!==1 || texts[0]!=='가나다' || keys.length) die('expected one "가나다", got '+JSON.stringify(r.calls));
    console.log('IME OK: nothing while composing, one "가나다" after, no stray Enter');
    console.log('PASS'); await b.close(); process.exit(0);
  } catch(e){ await b.close().catch(()=>{}); die(e.message); }
})();
