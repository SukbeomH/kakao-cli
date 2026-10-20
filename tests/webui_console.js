// Console e2e (desktop): the console-only page streams the device into the center,
// android pane lists /sdcard/ → Download, push a local file up, pull it back
// down, then click the device's message box on the streamed screen and type
// Korean through the hidden keyboard capture (composition events).
//   NODE_PATH=.. CHROME=.. PUSH_NAME=<file in local root> INPUT_X=<dev px>
//   INPUT_Y=<dev px> KBD_TEXT=<text> node webui_console.js <base>
// Device-side verification (ls / dump) is done by e2e_console.sh.
const { chromium } = require('playwright-core');
const BASE = process.argv[2];
const PUSH = process.env.PUSH_NAME;
const IX = +process.env.INPUT_X, IY = +process.env.INPUT_Y;
const KBD = process.env.KBD_TEXT || 'kbd테스트';
function die(m){ console.error('FAIL: '+m); process.exit(1); }
const rowNames = (p, w) => p.evaluate(w =>
  [...document.querySelectorAll('#flist-'+w+' .frow .fn')].map(e=>e.textContent.replace(/^\S+\s/,'')), w);
async function clickRow(p, w, name){
  const ok = await p.evaluate(([w,n])=>{
    for (const r of document.querySelectorAll('#flist-'+w+' .frow'))
      if (r.querySelector('.fn').textContent.replace(/^\S+\s/,'')===n){ r.click(); return true; }
    return false; }, [w,name]);
  if (!ok) die('row not found in '+w+': '+name);
}
async function waitJob(p){
  await p.waitForFunction(()=>{ const j=[...document.querySelectorAll('#jobs .job')].pop();
    return j && !j.classList.contains('running'); }, null, {timeout:120000});
  const cls = await p.evaluate(()=>[...document.querySelectorAll('#jobs .job')].pop().className);
  const txt = await p.evaluate(()=>[...document.querySelectorAll('#jobs .job')].pop().textContent);
  if (!cls.includes('done')) die('job failed: '+txt);
  return txt;
}
(async () => {
  const b = await chromium.launch({ executablePath: process.env.CHROME, headless: true });
  const p = await (await b.newContext({ viewport:{width:1400,height:860} })).newPage();
  p.setDefaultTimeout(60000);
  const errs=[]; p.on('pageerror', e=>errs.push(e.message));
  try {
    await p.goto(BASE, {waitUntil:'domcontentloaded'});
    // 1) the web UI is console-only: no chat list / mode switch at all
    if (await p.evaluate(()=>!!document.getElementById('list') || !!document.getElementById('m-chat')))
      die('chat UI still present');
    // 2) the single <img> is re-parented into the console center and streams
    await p.waitForFunction(()=>{ const i=document.getElementById('scr');
      return i.parentElement.id==='conwrap' && i.naturalWidth>0; }, null, {timeout:60000});
    console.log('screen streaming in console center');
    // 3) android pane lists /sdcard/ incl. Download; navigate into it
    await p.waitForFunction(()=>document.querySelectorAll('#flist-android .frow').length>2, null, {timeout:30000});
    if (!(await rowNames(p,'android')).includes('Download')) die('Download not listed');
    await clickRow(p, 'android', 'Download');
    await p.waitForFunction(()=>document.getElementById('fpath-android').value==='/sdcard/Download/', null, {timeout:30000});
    console.log('android pane: /sdcard/Download/');
    // 4) push: select the local file, ↑ copy into /sdcard/Download/
    await p.waitForFunction(n=>[...document.querySelectorAll('#flist-local .frow .fn')]
      .some(e=>e.textContent.endsWith(n)), PUSH, {timeout:30000});
    await clickRow(p, 'local', PUSH);
    await p.click('#push');
    console.log('push job: '+await waitJob(p));
    await p.waitForFunction(n=>[...document.querySelectorAll('#flist-android .frow .fn')]
      .some(e=>e.textContent.endsWith(n)), PUSH, {timeout:30000});
    // 5) pull it back down: lands as <stem>-1.<ext> (never overwrites)
    await clickRow(p, 'android', PUSH);
    await p.click('#pull');
    console.log('pull job: '+await waitJob(p));
    const dot = PUSH.lastIndexOf('.'), back = PUSH.slice(0,dot)+'-1'+PUSH.slice(dot);
    await p.waitForFunction(n=>[...document.querySelectorAll('#flist-local .frow .fn')]
      .some(e=>e.textContent.endsWith(n)), back, {timeout:30000});
    console.log('local pane shows '+back);
    // 6) keyboard: click the device's message box on the streamed screen, then type
    if (IX && IY) {
      const pt = await p.evaluate(([ix,iy])=>{ const i=document.getElementById('scr'), r=i.getBoundingClientRect();
        const s=Math.min(r.width/i.naturalWidth, r.height/i.naturalHeight);
        const ox=(r.width-i.naturalWidth*s)/2, oy=(r.height-i.naturalHeight*s)/2;
        return [r.left+ox+ix*s, r.top+oy+iy*s]; }, [IX,IY]);
      await p.mouse.click(pt[0], pt[1]);
      await p.waitForFunction(()=>document.activeElement.id==='kbcap', null, {timeout:5000});
      await p.waitForTimeout(800);
      await p.evaluate(t=>{ const k=document.getElementById('kbcap');
        k.dispatchEvent(new CompositionEvent('compositionstart',{data:''}));
        k.value=t;
        k.dispatchEvent(new InputEvent('input',{data:t,isComposing:true,bubbles:true}));
        k.dispatchEvent(new CompositionEvent('compositionend',{data:t})); }, KBD);
      await p.waitForTimeout(1500);
      console.log('typed via keyboard capture');
    }
    if (errs.length) die('page errors: '+errs.join(' | '));
    await p.screenshot({path: process.env.SHOT || '/tmp/webui_console.png'});
    console.log('PASS'); await b.close(); process.exit(0);
  } catch(e){ if(errs.length) console.error('page errors: '+errs.join(' | ')); await b.close().catch(()=>{}); die(e.message); }
})();
