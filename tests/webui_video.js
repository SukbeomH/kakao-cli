// Console video e2e (V/V2): H.264 renderer on a scrcpy virtual display.
//  - frames arrive on the chosen renderer (WC over a secure origin, MSE on http)
//  - KakaoTalk is resumed on the virtual display (not display 0), lock is held
//  - tap reflection: click Friends/Chats on the streamed video → the device's
//    dump (in virtual-display pixels) shows that tab selected; tap→pixel-change
//    latency is measured in the page
//  - CLI UI commands fail with console_busy while the VD is up   (desktop)
//  - flex: a smaller window resizes the VD (session size follows)  (desktop)
//  - manual toggle to mirror (app back on display 0, lock cleared) and back
//  - forced client fallback: the stream endpoint fails → renderer falls to PNG
// Prints "METRIC {json}" lines for the report.
//   NODE_PATH=.. CHROME=.. STATE_CMD=<prints vd_state.py JSON> CLI_CMD=<kakao rooms --json>
//   MOBILE=1 RENDER=wc|mse SHOT=.. node webui_video.js <base>
const { chromium } = require('playwright-core');
const { execSync } = require('child_process');
const BASE = process.argv[2];
const MOBILE = !!process.env.MOBILE, RENDER = process.env.RENDER || 'auto';
// privacy: blur the device screen body (names/messages) but keep header/tab bars
const MASK_CSS = '.scrwrap::after{content:"";position:absolute;left:0;right:0;top:13%;bottom:11%;' +
  'backdrop-filter:blur(9px);-webkit-backdrop-filter:blur(9px);pointer-events:none}';
function die(m){ console.error('FAIL: '+m); process.exit(1); }
const state = () => JSON.parse(execSync(process.env.STATE_CMD, {encoding:'utf8'}).trim().split('\n').pop());
const sleep = ms => new Promise(r=>setTimeout(r, ms));
const info = p => p.evaluate(async()=> (await fetch('/api/video/info')).json());
async function until(fn, ms, what){
  const t = Date.now();
  for(;;){ const v = await fn(); if(v) return v; if(Date.now()-t > ms) die('timeout: '+what); await sleep(400); }
}
const mediaSel = r => r==='wc' ? '#vcanvas' : r==='mse' ? '#vvideo' : '#scr';
// device (stream) pixel → page point on the letterboxed media element
async function pagePoint(p, x, y){
  return p.evaluate(([x,y])=>{
    const el = document.getElementById(renderer==='wc'?'vcanvas':renderer==='mse'?'vvideo':'scr');
    const iw = el.tagName==='CANVAS'?el.width:el.tagName==='VIDEO'?el.videoWidth:el.naturalWidth;
    const ih = el.tagName==='CANVAS'?el.height:el.tagName==='VIDEO'?el.videoHeight:el.naturalHeight;
    const r = el.getBoundingClientRect(), s = Math.min(r.width/iw, r.height/ih);
    return [r.left + (r.width-iw*s)/2 + (x+0.5)*s, r.top + (r.height-ih*s)/2 + (y+0.5)*s, iw, ih];
  }, [x,y]);
}
// in-page pixel watcher: time from pointerdown to the first changed rendered frame
async function armLatency(p){
  await p.evaluate(()=>{
    const c = document.createElement('canvas'); c.width = c.height = 48;
    const g = c.getContext('2d', {willReadFrequently:true});
    const sig = ()=>{ const el = document.getElementById(renderer==='wc'?'vcanvas':renderer==='mse'?'vvideo':'scr');
      try{ g.drawImage(el, 0, 0, 48, 48); }catch(_){ return null; }
      const d = g.getImageData(0,0,48,48).data; let s = 0; for(let i=0;i<d.length;i+=4) s += d[i]*3+d[i+1]*5+d[i+2]*7; return s; };
    window.__lat = null; window.__base = sig(); window.__t0 = null;
    addEventListener('pointerdown', ()=>{ window.__t0 = performance.now(); window.__base = sig(); }, {capture:true, once:true});
    const loop = ()=>{ if(window.__t0 !== null){ const s = sig();
        if(s !== null && window.__base !== null && Math.abs(s - window.__base) > window.__base*0.002){
          window.__lat = performance.now() - window.__t0; return; } }
      requestAnimationFrame(loop); };
    requestAnimationFrame(loop);
  });
}
async function tapTab(p, name){
  const st = state(), tab = st.tabs[name];
  if(!tab) die('tab not in dump: '+name+' '+JSON.stringify(st));
  await armLatency(p);
  const [px, py] = await pagePoint(p, tab.cx, tab.cy);
  if(MOBILE) await p.touchscreen.tap(px, py); else await p.mouse.click(px, py);
  const s2 = await until(()=>{ const s = state(); return s.tabs[name] && s.tabs[name].sel ? s : null; }, 15000,
                         name+' tab selected on the device');
  const lat = await until(()=>p.evaluate(()=>window.__lat), 5000, 'rendered frame change after tap');
  console.log('tap '+name+' at VD('+tab.cx+','+tab.cy+') → selected on display '+s2.kakao_display+
              ', pixels changed after '+Math.round(lat)+' ms');
  return lat;
}
async function sample(p, ms){
  await sleep(ms); return info(p);
}
(async () => {
  const b = await chromium.launch({ executablePath: process.env.CHROME, headless: true,
    args: ['--autoplay-policy=no-user-gesture-required'] });
  const ctx = MOBILE
    ? await b.newContext({ viewport:{width:390,height:844}, deviceScaleFactor:2, isMobile:true, hasTouch:true })
    : await b.newContext({ viewport:{width:1400,height:860} });
  const p = await ctx.newPage(); p.setDefaultTimeout(60000);
  const errs = []; p.on('pageerror', e=>errs.push(e.message));
  try {
    const t0 = Date.now();
    await p.goto(BASE + (RENDER==='auto' ? '' : '?video='+RENDER), {waitUntil:'domcontentloaded'});
    const r = await until(()=>p.evaluate(()=>{ const el = document.getElementById(
        renderer==='wc'?'vcanvas':renderer==='mse'?'vvideo':'scr');
      const ok = renderer==='wc' ? +el.dataset.frames>0 : renderer==='mse' ? el.videoWidth>0 && el.readyState>=2 : false;
      return ok ? renderer : null; }), 60000, 'first H.264 frame');
    if (RENDER!=='auto' && r!==RENDER) die('renderer '+r+' != '+RENDER);
    const first = Date.now()-t0;
    let inf = await until(async()=>{ const i = await info(p); return i.w ? i : null; }, 20000, 'video info');
    const badge = await p.textContent('#vmode');
    console.log('renderer '+r+' first frame '+first+' ms, badge "'+badge+'", stream '+inf.w+'x'+inf.h+' vd='+JSON.stringify(inf.vd));
    if (inf.mode!=='virtual' || !badge.includes('가상 '+inf.w+'x'+inf.h+' flex')) die('not on a flex virtual display');
    if (MOBILE ? !(inf.h > inf.w) : !(inf.w > inf.h)) die('VD orientation does not follow the window');
    let st = await until(()=>{ const s = state(); return s.kakao_display && s.lock ? s : null; }, 15000,
                         'KakaoTalk on the virtual display + console lock');
    console.log('KakaoTalk resumed on display '+st.kakao_display+' (display 0 top: '+st.displays['0']+'); lock "'+st.lock+'"');
    // tap reflection both ways (restores the original tab)
    const orig = st.tabs.chats && st.tabs.chats.sel ? 'chats' : 'friends';
    const other = orig==='chats' ? 'friends' : 'chats';
    const l1 = await tapTab(p, other), l2 = await tapTab(p, orig);
    const idle = await sample(p, 3000);
    // activity: drag-scroll the list a few times and sample fps/kbps
    const mid = await pagePoint(p, Math.round(inf.w*0.25), Math.round(inf.h*0.5));
    const t1 = Date.now();
    for (let i=0; i<4; i++){
      await p.mouse.move(mid[0], mid[1]+60); await p.mouse.down();
      await p.mouse.move(mid[0], mid[1]-60, {steps:8}); await p.mouse.up(); await sleep(350);
      await p.mouse.move(mid[0], mid[1]-60); await p.mouse.down();
      await p.mouse.move(mid[0], mid[1]+60, {steps:8}); await p.mouse.up(); await sleep(350);
    }
    const busy = await info(p);
    const cfps = await p.textContent('#cfps');
    console.log('METRIC '+JSON.stringify({view: MOBILE?'mobile':'desktop', renderer: r, size: inf.w+'x'+inf.h,
      first_frame_ms: first, tap_to_pixels_ms: [Math.round(l1), Math.round(l2)],
      idle: {fps: idle.fps, kbps: idle.kbps}, active: {fps: busy.fps, kbps: busy.kbps, client: cfps},
      active_s: Math.round((Date.now()-t1)/1000)}));
    if (process.env.SHOT) { await p.addStyleTag({content: MASK_CSS}); await p.screenshot({path: process.env.SHOT}); }
    if (!MOBILE) {
      // CLI/TUI must refuse while the console holds KakaoTalk on its VD
      let out = '', rc = 0;
      try { out = execSync(process.env.CLI_CMD, {encoding:'utf8'}); } catch(e){ rc = e.status; out = (e.stdout||'')+(e.stderr||''); }
      if (rc !== 7 || !out.includes('console_busy')) die('CLI not refused (rc '+rc+'): '+out.slice(0,200));
      console.log('CLI refused while the console is live: rc 7 console_busy');
      // flex: shrink the window → the VD follows (RESIZE_DISPLAY, same dpi)
      await p.setViewportSize({width:1000, height:700});
      const inf2 = await until(async()=>{ const i = await info(p); return i.w && i.w !== inf.w ? i : null; }, 15000, 'VD resize');
      await until(()=>p.evaluate(([w,h])=>{ const el = document.getElementById(renderer==='wc'?'vcanvas':'vvideo');
        return (renderer==='wc'?el.width:el.videoWidth)===w && (renderer==='wc'?el.height:el.videoHeight)===h; },
        [inf2.w, inf2.h]), 15000, 'rendered size follows the VD');
      console.log('flex: window 1400x860 → 1000x700, VD '+inf.w+'x'+inf.h+' → '+inf2.w+'x'+inf2.h+' (rendered at the new size)');
      st = state(); const tab = st.tabs[orig];
      if (!tab) die('tabs missing after resize');
      await tapTab(p, other); await tapTab(p, orig);            // input still maps after resize
      // manual toggle → mirror: VD destroyed, app back on display 0, lock cleared
      await p.selectOption('#dsel', 'mirror');
      const mi = await until(async()=>{ const i = await info(p); return i.mode==='mirror' && i.w ? i : null; }, 20000, 'mirror stream');
      await until(()=>p.evaluate(()=>document.getElementById('vmode').textContent.includes('미러')), 15000, 'mirror badge');
      st = await until(()=>{ const s = state(); return s.kakao_display===0 && !s.lock ? s : null; }, 15000,
                       'app on display 0 + lock cleared in mirror mode');
      console.log('mirror: stream '+mi.w+'x'+mi.h+', KakaoTalk on display 0, lock cleared, badge "'+await p.textContent('#vmode')+'"');
      await p.selectOption('#dsel', 'virtual');
      await until(async()=>{ const i = await info(p); return i.mode==='virtual' && i.w ? i : null; }, 20000, 'back to virtual');
      await until(()=>{ const s = state(); return s.kakao_display && s.kakao_display !== 0 ? s : null; }, 15000, 'app on the VD again');
      console.log('toggled back to the virtual display');
    }
    // forced client fallback: the stream endpoint breaks → renderer falls through to PNG
    await p.route('**/api/video/stream*', route => route.fulfill({status: 502, body: 'x'}));
    await p.evaluate(()=>startRenderer(renderer));
    await until(()=>p.evaluate(()=>renderer==='png' && document.getElementById('scr').naturalWidth>0
                                && !document.getElementById('scr').hidden), 40000, 'PNG fallback');
    console.log('forced fallback: stream 502 → '+(await p.textContent('#status')).trim()+' → PNG frames');
    if (errs.length) die('page errors: '+errs.join(' | '));
    console.log('PASS'); await b.close(); process.exit(0);
  } catch(e){ if(errs.length) console.error('page errors: '+errs.join(' | ')); await b.close().catch(()=>{}); die(e.message); }
})();
