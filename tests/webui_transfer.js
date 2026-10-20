// Console transfers e2e: browser → VM upload (button, progress), VM → browser
// download, browser → device upload (drop onto the android pane, staged+pushed),
// device → browser download (streamed via adb exec-out). Hashes are compared by
// e2e_console.sh. MOBILE=1 runs a lighter pass in touch emulation.
//   NODE_PATH=.. CHROME=.. FILE_LOCAL=<path> FILE_ANDROID=<path> OUT=<dir>
//   node webui_transfer.js <base>
const fs = require('fs'), path = require('path');
const { chromium } = require('playwright-core');
const BASE = process.argv[2];
const FL = process.env.FILE_LOCAL, FA = process.env.FILE_ANDROID, OUT = process.env.OUT || '/tmp';
const MOBILE = !!process.env.MOBILE;
function die(m){ console.error('FAIL: '+m); process.exit(1); }
const lastJob = p => p.evaluate(()=>{ const j=[...document.querySelectorAll('#jobs .job')].pop();
  return j ? {cls:j.className, txt:j.textContent} : null; });
async function waitJobDone(p){
  await p.waitForFunction(()=>{ const j=[...document.querySelectorAll('#jobs .job')].pop();
    return j && !j.classList.contains('running'); }, null, {timeout:180000});
  const j = await lastJob(p);
  if (!j.cls.includes('done')) die('job failed: '+j.txt);
  return j.txt;
}
async function rowAction(p, w, name, how){
  const ok = await p.evaluate(([w,n,how])=>{
    for (const r of document.querySelectorAll('#flist-'+w+' .frow'))
      if (r.querySelector('.fn').textContent.replace(/^\S+\s/,'')===n){
        if (how==='dbl') r.dispatchEvent(new MouseEvent('dblclick',{bubbles:true})); else r.click();
        return true; }
    return false; }, [w,name,how]);
  if (!ok) die('row not found in '+w+': '+name);
}
const hasRow = (p, w, n) => p.waitForFunction(([w,n])=>[...document.querySelectorAll('#flist-'+w+' .frow .fn')]
  .some(e=>e.textContent.replace(/^\S+\s/,'')===n), [w,n], {timeout:60000});
(async () => {
  const b = await chromium.launch({ executablePath: process.env.CHROME, headless: true });
  const ctx = MOBILE
    ? await b.newContext({ viewport:{width:390,height:844}, deviceScaleFactor:2, isMobile:true, hasTouch:true, acceptDownloads:true })
    : await b.newContext({ viewport:{width:1400,height:860}, acceptDownloads:true });
  const p = await ctx.newPage(); p.setDefaultTimeout(60000);
  const errs=[]; p.on('pageerror', e=>errs.push(e.message));
  try {
    await p.goto(BASE, {waitUntil:'domcontentloaded'});
    await p.waitForFunction(()=>document.querySelectorAll('#flist-android .frow').length>2, null, {timeout:30000});
    // watch every job text so we can prove progress was reported mid-upload
    await p.evaluate(()=>{ window.__pct=[]; new MutationObserver(()=>{
      for (const j of document.querySelectorAll('#jobs .job')){ const m=j.textContent.match(/(\d+)%/);
        if (m) window.__pct.push(+m[1]); } }).observe(document.getElementById('jobs'),{childList:true,subtree:true,characterData:true}); });

    // 1) browser → VM via the ⇪ button
    const nl = path.basename(FL);
    await p.setInputFiles('#upf-local', FL);
    console.log('upload(VM): '+await waitJobDone(p));
    const pct = await p.evaluate(()=>window.__pct);
    if (!pct.some(v=>v>0)) die('no upload progress reported: '+JSON.stringify(pct.slice(0,8)));
    console.log('progress seen: max '+Math.max(...pct)+'%');
    await hasRow(p, 'local', nl);
    // 2) VM → browser download (double-click the row)
    const [d1] = await Promise.all([ p.waitForEvent('download', {timeout:120000}), rowAction(p,'local',nl,'dbl') ]);
    await d1.saveAs(path.join(OUT, 'dl-local.bin'));
    console.log('download(VM): '+d1.suggestedFilename());
    if (!MOBILE) {
      // 3) browser → device: drop a File onto the android pane (in /sdcard/Download/)
      await rowAction(p, 'android', 'Download', 'click');
      await p.waitForFunction(()=>document.getElementById('fpath-android').value==='/sdcard/Download/', null, {timeout:30000});
      const na = path.basename(FA), b64 = fs.readFileSync(FA).toString('base64');
      await p.evaluate(([n,b64])=>{
        const bin = Uint8Array.from(atob(b64), c=>c.charCodeAt(0));
        const dt = new DataTransfer(); dt.items.add(new File([bin], n));
        document.getElementById('fp-android').dispatchEvent(new DragEvent('drop',{dataTransfer:dt,bubbles:true,cancelable:true}));
      }, [na, b64]);
      console.log('upload(device): '+await waitJobDone(p));
      await hasRow(p, 'android', na);
      // 4) device → browser download (streamed from adb exec-out)
      const [d2] = await Promise.all([ p.waitForEvent('download', {timeout:120000}), rowAction(p,'android',na,'dbl') ]);
      await d2.saveAs(path.join(OUT, 'dl-android.bin'));
      console.log('download(device): '+d2.suggestedFilename());
    }
    if (errs.length) die('page errors: '+errs.join(' | '));
    if (process.env.SHOT) await p.screenshot({path: process.env.SHOT});
    console.log('PASS'); await b.close(); process.exit(0);
  } catch(e){ if(errs.length) console.error('page errors: '+errs.join(' | ')); await b.close().catch(()=>{}); die(e.message); }
})();
