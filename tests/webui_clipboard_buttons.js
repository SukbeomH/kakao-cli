// Browser fixture: never reads/writes the real host clipboard or any Android device.
// python -c 'from android_console.webui import PAGE; print(PAGE)' | NODE_PATH=... CHROME=... node tests/webui_clipboard_buttons.js
const assert=require('node:assert/strict');
const {chromium}=require('playwright-core');
const source=require('node:fs').readFileSync(0,'utf8');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROME,headless:true});
 try{
  for(const origin of ['http://localhost:18801','http://console.test']){
   const context=await browser.newContext({viewport:{width:390,height:844}});
   const page=await context.newPage(),calls=[],errors=[];
   let device='기기 한글🙂',readStatus=200,putStatus=200;
   page.on('pageerror',e=>errors.push(e.message));
   await page.addInitScript(()=>{
    window.fixtureLocal='로컬 내용';window.denyRead=false;window.denyCopy=false;
    Object.defineProperty(navigator,'clipboard',{configurable:true,value:{
     readText:async()=>{if(window.denyRead)throw Error('denied');return window.fixtureLocal;},
     writeText:async text=>{if(window.denyCopy)throw Error('denied');window.fixtureLocal=text;},
    }});
    document.execCommand=command=>{
     if(command!=='copy'||window.denyCopy)return false;
     window.fixtureLocal=document.activeElement.value;return true;
    };
   });
   await page.route(origin+'/**',async route=>{
    const request=route.request(),url=new URL(request.url());
    if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:source});
    if(url.pathname==='/api/screen/frame')return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="360" height="640"/>'});
    if(request.method()==='POST')calls.push({path:url.pathname,body:request.postDataJSON()});
    if(url.pathname==='/api/clip/read')return route.fulfill({status:readStatus,json:readStatus===200?{available:true,seq:1,text:device}:{error:'read unavailable'}});
    if(url.pathname==='/api/clip'&&request.method()==='POST')return route.fulfill({status:putStatus,json:{ok:putStatus===200,via:'scrcpy',error:'offline'}});
    let json={ok:true};
    if(url.pathname==='/api/state')json={seq:1,status:'ready',scrcpy:true,clip_seq:1};
    if(url.pathname==='/api/screen/info')json={enabled:true,seq:1,w:360,h:640};
    if(url.pathname==='/api/video/info')json={mode:'mirror',max_side:1280};
    if(url.pathname.startsWith('/api/fs/'))json={path:'',parent:null,items:[]};
    return route.fulfill({json});
   });
   await page.goto(origin+'/?video=png');
   await page.waitForFunction(()=>document.querySelector('#scr').naturalWidth===360);
   await page.locator('#ckclipget').click();
   await page.waitForFunction(()=>document.querySelector('#clipboard-hint').textContent.includes('복사했습니다'));
   assert.equal(await page.evaluate(()=>window.fixtureLocal),device);
   assert.equal(await page.locator('#clipboard-text').inputValue(),device);
   assert(await page.locator('#clipboard-text').evaluate(t=>t.readOnly));
   await page.keyboard.press('Escape');
   await page.waitForFunction(()=>!document.querySelector('#clipboard-dialog').open && document.querySelector('#clipboard-text').value==='');
   assert.equal(await page.locator('#clipboard-text').inputValue(),'','clear dialog plaintext on close');
   // An empty device clipboard must not erase the local one.
   device='';await page.locator('#ckclipget').click();
   await page.waitForFunction(()=>document.querySelector('#clipboard-dialog').open);
   assert.equal(await page.evaluate(()=>window.fixtureLocal),'기기 한글🙂');
   assert(await page.locator('#clipboard-apply').isDisabled());
   await page.locator('#clipboard-close').click();
   // Clipboard-only store never becomes typing/paste in the current app.
   await page.evaluate(()=>window.fixtureLocal='로컬 저장 한글');
   await page.locator('#ckclip').click();
   if(origin.includes('console.test')){
    await page.locator('#clipboard-text').fill('로컬 저장 한글');
    await page.locator('#clipboard-apply').click();
   }
   await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('기기 클립보드에 넣음'));
   assert.deepEqual(calls.filter(c=>c.path==='/api/clip').at(-1).body,{text:'로컬 저장 한글',paste:false});
   assert(!calls.some(c=>['/api/screen/text','/api/screen/key'].includes(c.path)));
   // Denied permission / HTTP -> editable manual-paste dialog; retain text on failure.
   await page.evaluate(()=>window.denyRead=true);await page.locator('#ckclip').click();
   await page.locator('#clipboard-text').fill('수동 입력');putStatus=503;
   await page.locator('#clipboard-apply').click();
   await page.waitForFunction(()=>document.querySelector('#clipboard-hint').textContent.includes('전송하지 못했습니다'));
   assert.equal(await page.locator('#clipboard-text').inputValue(),'수동 입력');
   putStatus=200;await page.locator('#clipboard-apply').click();
   await page.waitForFunction(()=>!document.querySelector('#clipboard-dialog').open);
   // Explicit copy click remains available if automatic clipboard writing is denied.
   device='직접 복사';await page.evaluate(()=>window.denyCopy=true);await page.locator('#ckclipget').click();
   await page.waitForFunction(()=>document.querySelector('#clipboard-dialog').open);
   await page.evaluate(()=>window.denyCopy=false);await page.locator('#clipboard-apply').click();
   await page.waitForFunction(()=>document.querySelector('#clipboard-hint').textContent.includes('복사했습니다'));
   assert.equal(await page.evaluate(()=>window.fixtureLocal),device);
   await page.locator('#clipboard-close').click();
   readStatus=504;await page.locator('#ckclipget').click();
   await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('가져오기 실패'));
   assert.equal(await page.evaluate(()=>window.fixtureLocal),device);
   assert.equal(await page.locator('#clipboard-dialog').evaluate(d=>d.open),false);
   // Context menu interception is limited to device surfaces, never file panes.
   const before=calls.length;
   assert.equal(await page.locator('#scr').evaluate(el=>el.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,button:2,clientX:100,clientY:100}))),false);
   assert.equal(calls.length,before,'PNG does not emit a fake left click');
   await page.evaluate(()=>{pngWanted=false;renderer='wc';showRenderer('wc');vcanvas.width=360;vcanvas.height=640;});
   assert.equal(await page.locator('#vcanvas').evaluate(el=>el.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,button:2,clientX:100,clientY:100}))),false);
   await page.waitForTimeout(100);
   assert.equal(calls.at(-1).path,'/api/screen/rightclick');
   assert.equal(calls.at(-1).body.src,'video');
   await page.locator('#files-open').click();
   assert.equal(await page.locator('#fp-local').evaluate(el=>el.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,button:2}))),true);
   assert.deepEqual(errors,[]);
   console.log('PASS '+origin+': clipboard get/store, permission/HTTP fallback, empty/error handling, right-click isolation');
   await context.close();
  }
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
