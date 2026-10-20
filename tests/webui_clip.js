// Clipboard e2e (browser side). Phase 1: wait for the scrcpy bridge, print
// READY, then expect a device-copy toast whose text equals EXPECT (the harness
// long-presses + copies a self-chat message meanwhile). Phase 2: click the
// device message box on the streamed screen and Ctrl+V-paste PASTE_TEXT (text →
// device paste) and then an image (→ /sdcard/Download upload). Nothing is sent.
//   NODE_PATH=.. CHROME=.. EXPECT=.. PASTE_TEXT=.. INPUT_X=.. INPUT_Y=.. PNG=<path>
//   node webui_clip.js <base>
const fs = require('fs');
const { chromium } = require('playwright-core');
const BASE = process.argv[2];
function die(m){ console.error('FAIL: '+m); process.exit(1); }
(async () => {
  const b = await chromium.launch({ executablePath: process.env.CHROME, headless: true });
  const p = await (await b.newContext({ viewport:{width:1400,height:860} })).newPage();
  p.setDefaultTimeout(60000);
  const errs=[]; p.on('pageerror', e=>errs.push(e.message));
  try {
    await p.goto(BASE, {waitUntil:'domcontentloaded'});
    await p.waitForFunction(()=>document.getElementById('clipbadge').textContent.includes('연동'), null, {timeout:90000});
    await p.waitForFunction(()=>{ const i=document.getElementById('scr'); return i && i.naturalWidth>0; }, null, {timeout:60000});
    await p.waitForTimeout(2500);                 // let the first poll record the baseline clip_seq
    console.log('READY');
    await p.waitForFunction(()=>!document.getElementById('cliptoast').hidden, null, {timeout:120000});
    const got = await p.evaluate(()=>window.clipText !== undefined ? window.clipText : document.getElementById('clipprev').textContent);
    const prev = await p.evaluate(()=>document.getElementById('clipprev').textContent);
    if (prev.trim() !== process.env.EXPECT.trim()) die('toast text mismatch (len '+prev.length+' vs '+process.env.EXPECT.length+')');
    console.log('device→browser toast OK (len '+prev.length+')');
    // phase 2: focus the device message box through the streamed screen
    const pt = await p.evaluate(([ix,iy])=>{ const i=document.getElementById('scr'), r=i.getBoundingClientRect();
      const s=Math.min(r.width/i.naturalWidth, r.height/i.naturalHeight);
      return [r.left+(r.width-i.naturalWidth*s)/2+ix*s, r.top+(r.height-i.naturalHeight*s)/2+iy*s]; },
      [+process.env.INPUT_X, +process.env.INPUT_Y]);
    await p.mouse.click(pt[0], pt[1]);
    await p.waitForFunction(()=>document.activeElement.id==='kbcap', null, {timeout:5000});
    await p.waitForTimeout(800);
    await p.evaluate(t=>{ const dt=new DataTransfer(); dt.setData('text/plain', t);
      document.getElementById('kbcap').dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true})); },
      process.env.PASTE_TEXT);
    await p.waitForFunction(()=>/기기에 붙여넣음/.test(document.getElementById('status').textContent), null, {timeout:20000});
    console.log('browser→device paste sent');
    const b64 = fs.readFileSync(process.env.PNG).toString('base64');
    await p.evaluate(b64=>{ const bin=Uint8Array.from(atob(b64),c=>c.charCodeAt(0));
      const dt=new DataTransfer(); dt.items.add(new File([bin],'image.png',{type:'image/png'}));
      document.getElementById('kbcap').dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true})); }, b64);
    await p.waitForFunction(()=>{ const j=[...document.querySelectorAll('#jobs .job')].pop();
      return j && j.classList.contains('done'); }, null, {timeout:120000});
    const job = await p.evaluate(()=>[...document.querySelectorAll('#jobs .job')].pop().textContent);
    const m = job.match(/→ (\/sdcard\/Download\/\S+)/);
    if (!m) die('image paste job has no device path: '+job);
    console.log('IMAGE_PATH '+m[1]);
    if (errs.length) die('page errors: '+errs.join(' | '));
    console.log('PASS'); await b.close(); process.exit(0);
  } catch(e){ if(errs.length) console.error('page errors: '+errs.join(' | ')); await b.close().catch(()=>{}); die(e.message); }
})();
