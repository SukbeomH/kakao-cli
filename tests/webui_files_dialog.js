// Browser regression with fixture files/screen only; no Android input or file writes.
// python -c 'from kakao_cli.webui import PAGE; print(PAGE)' | NODE_PATH=... CHROME=... node tests/webui_files_dialog.js
const assert=require('node:assert/strict');
const {chromium}=require('playwright-core');
const source=require('node:fs').readFileSync(0,'utf8');
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
 const b=await chromium.launch({executablePath:process.env.CHROME,headless:true});
 try{
  for(const [width,height] of [[1200,800],[390,844],[640,360]]){
   const p=await b.newPage({viewport:{width,height}}), calls=[], scrollBodies=[], errors=[];
   p.on('pageerror',e=>errors.push(e.message));
   await p.route('http://console.test/**',async route=>{
    const u=new URL(route.request().url());
    if(u.pathname==='/')return route.fulfill({contentType:'text/html',body:source});
    if(u.pathname==='/api/screen/frame')return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="360" height="640"><rect width="360" height="640" fill="#1f2941"/><text x="90" y="320" fill="white">Device screen</text></svg>'});
    if(route.request().method()==='POST')calls.push(u.pathname);
    if(u.pathname==='/api/screen/scroll')scrollBodies.push(route.request().postDataJSON());
    let json={ok:true};
    if(u.pathname==='/api/screen/info')json={enabled:true,seq:1,w:360,h:640};
    if(u.pathname==='/api/state')json={seq:1,status:'ready',scrcpy:true,clip_seq:0};
    if(u.pathname==='/api/video/info')json={mode:'virtual',max_side:1280};
    if(u.pathname.startsWith('/api/fs/'))json={path:u.searchParams.get('path')||'',parent:null,
     items:Array.from({length:100},(_,i)=>({name:'fixture-'+String(i).padStart(3,'0')+'.txt',path:'/fixture-'+i,type:'file',size:i}))};
    return route.fulfill({json});
   });
   await p.goto('http://console.test/?video=png');
   await p.waitForFunction(()=>document.querySelectorAll('#flist-local .frow').length===100);
   assert.equal(await p.locator('#files-dialog').isVisible(),false);
   const original=await p.locator('#conwrap').boundingBox();
   assert.ok(original.y+original.height<=height,'device screen must fit without scrolling to files');
   // Exercise the actual wheel handler with the video renderer; all API calls are mocked.
   await p.evaluate(()=>{pngWanted=false;renderer='wc';showRenderer('wc');const c=document.getElementById('vcanvas');c.width=360;c.height=640;});
   await sleep(400);
   const cv=await p.locator('#vcanvas').boundingBox();
   await p.mouse.move(cv.x+cv.width/2,cv.y+cv.height/2);
   const scrolled=p.waitForResponse(r=>new URL(r.url()).pathname==='/api/screen/scroll');
   await p.mouse.wheel(0,180);await scrolled;
   assert.equal(calls.filter(n=>n==='/api/screen/scroll').length,1,JSON.stringify({width,height,errors,calls,cv,state:await p.evaluate(()=>({renderer,pd,hit:document.elementFromPoint(innerWidth/2,innerHeight/2)?.id}))}));
   assert.equal(scrollBodies[0].dx,0);
   await p.mouse.wheel(200,0);await sleep(100);
   await p.mouse.wheel(200,1);await sleep(100);
   assert.equal(scrollBodies.length,1,'horizontal wheel must be ignored');
   await p.keyboard.down('Shift');await p.mouse.wheel(0,180);await p.keyboard.up('Shift');await sleep(100);
   assert.equal(scrollBodies.length,1,'Shift-wheel must be ignored');
   assert.equal(await p.locator('#console').evaluate(e=>e.scrollTop),0);
   await p.click('#files-open');
   assert.equal(await p.locator('#files-open').getAttribute('aria-expanded'),'true');
   await p.waitForSelector('#files-dialog:modal');
   assert.deepEqual(await p.locator('#conwrap').boundingBox(),original,'opening files must not resize the device');
   const dialog=await p.locator('#files-dialog').boundingBox();
   assert.ok(dialog.x>=0&&dialog.y>=0&&dialog.x+dialog.width<=width&&dialog.y+dialog.height<=height);
   // A native dialog traps keyboard focus and shields the underlying device.
   for(let i=0;i<14;i++){
    await p.keyboard.press('Tab');
    assert.equal(await p.evaluate(()=>document.activeElement===document.body || document.getElementById('files-dialog').contains(document.activeElement)),true);
   }
   await p.evaluate(()=>document.getElementById('ckback').focus());
   assert.notEqual(await p.evaluate(()=>document.activeElement.id),'ckback','background controls must be inert');
   const count=calls.filter(n=>n==='/api/screen/scroll').length;
   const list=p.locator('#flist-local'), box=await list.boundingBox();
   await p.mouse.move(box.x+box.width/2,box.y+box.height/2);await p.mouse.wheel(0,400);await sleep(200);
   assert.ok(await list.evaluate(e=>e.scrollTop)>0,'wheel must scroll the file list');
   await list.evaluate(e=>e.scrollTop=e.scrollHeight);await p.mouse.wheel(0,500);await sleep(100);
   assert.equal(calls.filter(n=>n==='/api/screen/scroll').length,count,'file wheel must never reach Android');
   assert.equal(await p.locator('#files-dialog').evaluate(e=>e.scrollTop),0);
   assert.equal(await p.locator('#console').evaluate(e=>e.scrollTop),0);
   if(width===390) await p.screenshot({path:'/tmp/kakao-files-dialog.png'});
   await p.focus('#fpath-local');await p.keyboard.press('Escape');
   assert.equal(await p.locator('#files-dialog').isVisible(),false);
   await p.waitForFunction(()=>document.getElementById('files-open').getAttribute('aria-expanded')==='false');
   assert.equal(await p.locator('#files-open').getAttribute('aria-expanded'),'false');
   assert.equal(await p.evaluate(()=>document.activeElement.id),'files-open');
   await p.click('#files-open');await p.click('#files-close');
   assert.equal(await p.locator('#files-dialog').isVisible(),false);
   await p.click('#files-open');await p.mouse.click(3,3);
   assert.equal(await p.locator('#files-dialog').isVisible(),false,'backdrop closes dialog');
   assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
   assert.deepEqual(errors,[]);
   console.log('PASS '+width+'x'+height+': screen isolation, list wheel, focus trap, Escape, close and backdrop');
   await p.close();
  }
 }finally{await b.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
