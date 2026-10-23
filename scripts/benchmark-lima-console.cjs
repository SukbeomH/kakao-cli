#!/usr/bin/env node
// Local Lima/Chrome Settings-only A/B; never sends a chat message.
// Dependencies: playwright-core and sharp (resolve with NODE_PATH if needed).
// Usage: node scripts/benchmark-lima-console.cjs before.html after.html [before,after,after,before]
// Uses the running kakao VM and kakao-webui-1. Temporarily opens Android Settings,
// records compositor frames only in memory, then restores Kakao in finally.
// HTML snapshots override only GET /; both variants use the same live backend.
// Default screen/info behavior must remain compatible for this comparison.
const {chromium}=require('playwright-core'),sharp=require('sharp');
const {execFileSync}=require('node:child_process'),fs=require('node:fs');
const adb=cmd=>execFileSync('limactl',['shell','kakao','sudo','docker','exec','kakao-webui-1','adb','-s','redroid:5555','shell',cmd],{encoding:'utf8'});
const pause=ms=>new Promise(r=>setTimeout(r,ms));
const origin='http://127.0.0.1:8800';
const stats=a=>{a=[...a].sort((a,b)=>a-b);return {n:a.length,p50:a[Math.ceil(a.length*.5)-1],p95:a[Math.ceil(a.length*.95)-1],max:a.at(-1)};};
(async()=>{
 const files={before:process.argv[2],after:process.argv[3]};
 if(!files.before||!files.after)throw Error('Provide before.html and after.html snapshots');
 const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
 let display;
 const results=[];
 try{
  for(const variant of (process.argv[4]||'before,after,after,before').split(',')){
   const html=fs.readFileSync(files[variant],'utf8');
   const startup=html.match(/\/\/ ---- startup:[\s\S]*?fetch\("([^"]+)"/)[1];
   const context=await browser.newContext({viewport:{width:475,height:988},deviceScaleFactor:2});
   const page=await context.newPage();const errors=[];
   page.on('pageerror',e=>errors.push(e.message));
   await page.route(origin+'/?video=wc',r=>r.fulfill({status:200,contentType:'text/html',body:html}));
   await page.addInitScript(()=>{
    window.drawStats={n:0,ms:0};const draw=CanvasRenderingContext2D.prototype.drawImage;
    CanvasRenderingContext2D.prototype.drawImage=function(...args){
     if(this.canvas.id!=='vcanvas')return draw.apply(this,args);
     const t=performance.now();try{return draw.apply(this,args);}finally{window.drawStats.n++;window.drawStats.ms+=performance.now()-t;}
    };
   });
   await page.goto(origin+'/?video=wc');
   await page.waitForFunction(()=>+document.getElementById('vcanvas').dataset.frames>0,null,{timeout:30000});
   await pause(2500);
   display=Number(adb('dumpsys display').match(/displayId=(\d+), uniqueId='virtual:com.android.shell/)[1]);
   adb('am force-stop com.android.settings');
   adb(`am start --display ${display} -a android.settings.SETTINGS`);
   await pause(1800);
   await page.addStyleTag({content:'#cfps{visibility:hidden}'});
   const info=await (await fetch(origin+'/api/video/info')).json();
   // Replay this variant's capability check while the video and Worker are warm.
   const keys=await page.evaluate(async startup=>{
    await fetch(startup);const values=[];
    for(let i=0;i<16;i++){
     const start=performance.now();const r=await fetch('/api/screen/key',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:19,src:'video'})});
     const j=await r.json();if(!r.ok||!j.ok)throw Error('key HTTP '+r.status);
     values.push(performance.now()-start);await new Promise(r=>setTimeout(r,40));
    }return values;
   },startup);
   await pause(2500);
   // Reset after DPAD focus. Screencast begins only once Settings is active.
   adb('am force-stop com.android.settings');adb(`am start --display ${display} -a android.settings.SETTINGS`);
   await pause(1600);
   if(!(adb('dumpsys activity activities').match(/(?:topResumedActivity=|m?ResumedActivity:)[^\n]*/g)||[]).some(line=>line.includes('com.android.settings/')))throw Error('Settings is not foreground');
   const box=await page.locator('#vcanvas').boundingBox();
   await page.mouse.move(box.x+box.width*.5,box.y+box.height*.55);
   const cdp=await context.newCDPSession(page);let chain=Promise.resolve(),latest=null,pending=null;
   const latencies=[],diffs=[];
   cdp.on('Page.screencastFrame',event=>{
    cdp.send('Page.screencastFrameAck',{sessionId:event.sessionId}).catch(()=>{});
    chain=chain.then(async()=>{
     const {data,info:image}=await sharp(Buffer.from(event.data,'base64')).removeAlpha().raw().toBuffer({resolveWithObject:true});
     // Compare center list area only: no header FPS, controls or scroll thumb.
     const scale=image.width/475;
     const left=Math.round((box.x+box.width*.08)*scale),right=Math.round((box.x+box.width*.85)*scale);
     const top=Math.round((box.y+box.height*.22)*scale),bottom=Math.round((box.y+box.height*.80)*scale);
     const pixels=[];
     for(let y=top;y<bottom;y++)for(let x=left;x<right;x++){
      const i=(y*image.width+x)*image.channels;pixels.push(data[i],data[i+1],data[i+2]);
     }
     const now=event.metadata.timestamp*1000;
     if(pending&&pending.at&&now>=pending.at&&pending.base){
      let changed=0;
      for(let i=0;i<pixels.length;i+=3){if(Math.abs(pixels[i]-pending.base[i])+Math.abs(pixels[i+1]-pending.base[i+1])+Math.abs(pixels[i+2]-pending.base[i+2])>90)changed++;}
      const ratio=changed/(pixels.length/3);
      if(ratio>.005){latencies.push(now-pending.at);diffs.push(ratio);pending.resolve(true);pending=null;}
     }
     latest=pixels;
    }).catch(e=>{errors.push(e.message);});
   });
   await page.exposeFunction('markWheel',at=>{if(pending)pending.at=at;});
   await page.evaluate(()=>document.getElementById('conwrap').addEventListener('wheel',()=>{
    if(window.armWheel){window.armWheel=false;window.markWheel(performance.timeOrigin+performance.now());}
   },{capture:true}));
   await cdp.send('Page.startScreencast',{format:'jpeg',quality:50,maxWidth:240,maxHeight:500,everyNthFrame:1});
   await pause(600);await chain;
   const draws0=await page.evaluate(()=>({...window.drawStats})),began=Date.now();let missed=0;
   for(let i=0;i<20;i++){
    let resolve;const complete=new Promise(r=>resolve=r);
    pending={at:null,base:latest,resolve};
    await page.evaluate(()=>window.armWheel=true);
    await page.mouse.wheel(0,i%2===0?420:-420);
    const ok=await Promise.race([complete,pause(1600).then(()=>false)]);
    if(!ok){missed++;pending=null;}
    await pause(650);
   }
   const elapsed=(Date.now()-began)/1000,draws1=await page.evaluate(()=>({...window.drawStats}));
   await cdp.send('Page.stopScreencast');await chain;await cdp.detach();
   const end=await (await fetch(origin+'/api/video/info')).json();
   const result={variant,viewport:[475,988],dpr:2,size:[info.w,info.h],endSize:[end.w,end.h],startup,keyMs:stats(keys),wheelToCompositorMs:stats(latencies),missed,draws:draws1.n-draws0.n,drawMs:draws1.ms-draws0.ms,elapsed,latencies,keys,errors};
   if(info.w!==end.w||info.h!==end.h)throw Error('geometry changed');
   console.log(JSON.stringify(result));results.push(result);
   await context.close();await pause(2500);
  }
 }finally{
  // Stop all browser input/observation before restoring the private app.
  await browser.close();
  if(display)adb(`am start --display ${display} -n com.kakao.talk/.activity.main.MainActivity`);
 }
})().catch(e=>{console.error(e);process.exitCode=1;});
