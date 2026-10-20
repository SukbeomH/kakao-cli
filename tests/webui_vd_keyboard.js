// Keyboard on the virtual display (V2 IME check): with KakaoTalk on the console's
// VD (desktop → tablet 2-pane, self-chat in the right pane from the earlier
// steps), click the message box on the streamed video and type Korean through
// the keyboard capture → ADBKeyBoard. Verified with a dump (it follows the VD),
// then cleared (ADB_CLEAR_TEXT) and verified empty. Nothing is sent.
//   CHECK_CMD=<prints {ok,ix,iy} for the VD screen: all visible messages mine>
//   DUMP_CMD=<prints a uiautomator dump>  CLEAR_CMD=<ADB_CLEAR_TEXT>  STATE_CMD=<vd_state.py>
//   KBD_TEXT=.. node webui_vd_keyboard.js <base>
const { chromium } = require('playwright-core');
const { execSync } = require('child_process');
const BASE = process.argv[2], KBD = process.env.KBD_TEXT || 'vd키보드';
const sh = c => execSync(c, {encoding:'utf8'});
const last = out => JSON.parse(out.trim().split('\n').pop());
function die(m){ console.error('FAIL: '+m); process.exit(1); }
const sleep = ms => new Promise(r=>setTimeout(r, ms));
(async () => {
  const b = await chromium.launch({ executablePath: process.env.CHROME, headless: true });
  const p = await (await b.newContext({ viewport:{width:1400,height:860} })).newPage();
  p.setDefaultTimeout(60000);
  try {
    await p.goto(BASE, {waitUntil:'domcontentloaded'});
    await p.waitForFunction(()=>renderer!=='png' && (document.getElementById('vvideo').videoWidth>0
      || +document.getElementById('vcanvas').dataset.frames>0), null, {timeout:60000});
    let st;
    for (let i=0; i<30; i++){ st = last(sh(process.env.STATE_CMD)); if (st.kakao_display) break; await sleep(500); }
    if (!st.kakao_display) die('KakaoTalk not on the virtual display');
    // SAFETY: only type when every visible message on the VD screen is mine
    const chk = last(sh(process.env.CHECK_CMD));
    if (!chk.ok) die('self-chat not confirmed on the virtual display (refusing to type): '+JSON.stringify(chk));
    console.log('VD display '+st.kakao_display+': self-chat confirmed ('+chk.n+' visible messages, all mine), box at VD('+chk.ix+','+chk.iy+')');
    const pt = await p.evaluate(([x,y])=>{
      const el = document.getElementById(renderer==='wc'?'vcanvas':'vvideo');
      const iw = el.tagName==='CANVAS'?el.width:el.videoWidth, ih = el.tagName==='CANVAS'?el.height:el.videoHeight;
      const r = el.getBoundingClientRect(), s = Math.min(r.width/iw, r.height/ih);
      return [r.left + (r.width-iw*s)/2 + (x+0.5)*s, r.top + (r.height-ih*s)/2 + (y+0.5)*s]; }, [chk.ix, chk.iy]);
    await p.mouse.click(pt[0], pt[1]);
    await p.waitForFunction(()=>document.activeElement.id==='kbcap', null, {timeout:5000});
    await sleep(800);
    await p.evaluate(t=>{ const k=document.getElementById('kbcap');
      k.dispatchEvent(new CompositionEvent('compositionstart',{data:''}));
      k.value=t;
      k.dispatchEvent(new InputEvent('input',{data:t,isComposing:true,bubbles:true}));
      k.dispatchEvent(new CompositionEvent('compositionend',{data:t})); }, KBD);
    let got = false;
    for (let i=0; i<8 && !got; i++){ await sleep(1500); got = sh(process.env.DUMP_CMD).includes(KBD); }
    if (!got) { sh(process.env.CLEAR_CMD); die('typed text not found in the VD message box'); }
    console.log('typed "'+KBD+'" into the message box on the virtual display (ADBKeyBoard; not sent)');
    sh(process.env.CLEAR_CMD); await sleep(1200);
    if (sh(process.env.DUMP_CMD).includes(KBD)) die('message box not cleared');
    console.log('message box cleared (0 draft)');
    console.log('PASS'); await b.close(); process.exit(0);
  } catch(e){ try{ sh(process.env.CLEAR_CMD); }catch(_){} await b.close().catch(()=>{}); die(e.message); }
})();
