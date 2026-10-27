// Console IME regression: update preedit without appending or Enter, then commit
// once after compositionend. Every input route is spied; nothing reaches Android.
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
      window.fetch=(u,o)=>{ if(typeof u==='string' && /^\/api\/screen\/(text|key|composition)$/.test(u)){
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
      const during = window.__calls.map(c=>[c[0],{...c[1]}]);
      k.dispatchEvent(new CompositionEvent('compositionend',{data:'가나다'}));
      k.dispatchEvent(new InputEvent('input',{data:'',isComposing:false,bubbles:true}));  // Firefox order
      await new Promise(res=>setTimeout(res,400));
      return {during, calls: window.__calls};
    });
    const phases=r.during.map(c=>c[1].phase);
    if (JSON.stringify(phases)!==JSON.stringify(['start','update'])) die('expected native preedit: '+JSON.stringify(r.during));
    const texts = r.calls.filter(c=>c[0]==='/api/screen/composition'&&c[1].phase==='commit').map(c=>c[1].text);
    const keys = r.calls.filter(c=>c[0]==='/api/screen/key');
    if (texts.length!==1 || texts[0]!=='가나다' || keys.length || r.calls.some(c=>c[0]==='/api/screen/text')) die('expected one native commit, got '+JSON.stringify(r.calls));
    console.log('IME OK: live preedit, one native commit, no duplicate text or stray Enter');
    console.log('PASS'); await b.close(); process.exit(0);
  } catch(e){ await b.close().catch(()=>{}); die(e.message); }
})();
