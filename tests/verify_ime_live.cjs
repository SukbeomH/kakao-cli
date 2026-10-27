// Explicit standalone Lima VM + Android Settings only; never touches Kakao or host clipboard.
// NODE_PATH=... CHROME=... node tests/verify_ime_live.cjs /tmp/result.json
const assert=require('node:assert/strict'),fs=require('node:fs');
const {execFileSync}=require('node:child_process'),{chromium}=require('playwright-core');
const wait=ms=>new Promise(r=>setTimeout(r,ms));
function adb(args){return execFileSync('limactl',['shell','--workdir','/','android-console','adb','-s','127.0.0.1:5555',...args],{encoding:'utf8',timeout:30000});}
const shell=cmd=>adb(['shell',cmd]);
function ui(){return adb(['exec-out','uiautomator dump /dev/tty']).match(/<node\b[^>]*>/g)||[];}
function node(id){const n=ui().find(n=>n.includes('resource-id="'+id+'"'));assert(n,'missing '+id);return n;}
const decode=s=>s.replace(/&#(\d+);/g,(_,n)=>String.fromCodePoint(+n)).replace(/&amp;/g,'&').replace(/&quot;/g,'"');
function read(){return decode(node('android:id/search_src_text').match(/text="([^"]*)"/)[1]);}
(async()=>{
 execFileSync('limactl',['shell','--workdir','/','android-console','adb','connect','127.0.0.1:5555']);
 const browser=await chromium.launch({executablePath:process.env.CHROME,headless:true});
 const page=await browser.newPage({viewport:{width:475,height:988}}),errors=[],requests=[],samples=[];
 page.on('pageerror',e=>{errors.push(e.message);console.log('PAGE ERROR',e.message);});page.on('request',r=>{if(r.method()==='POST'&&r.url().includes('/api/screen/'))requests.push({path:new URL(r.url()).pathname,body:r.postDataJSON()});});
 page.setDefaultTimeout(30000);
 try{
 console.log('Opening settings');shell('am force-stop com.android.settings.intelligence');shell('am force-stop com.android.settings');shell('am start -a android.settings.SETTINGS');
 await page.goto('http://127.0.0.1:8801/?video=png');console.log('Page loaded');await page.evaluate(()=>{const original=window.fetch;window.imeReplies=[];window.fetch=async(...args)=>{const r=await original(...args);if(String(args[0]).includes('/api/screen/composition'))window.imeReplies.push({status:r.status,body:await r.clone().text()});return r;};});await page.waitForFunction(()=>document.querySelector('#scr').naturalWidth===720,{},{timeout:30000});
 const n=node('com.android.settings:id/search_action_bar'),m=n.match(/bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"/),pos=[(+m[1]+ +m[3])/2,(+m[2]+ +m[4])/2];
 const b=await page.locator('#scr').boundingBox(),s=Math.min(b.width/720,b.height/1280);
 await page.mouse.click(b.x+(b.width-720*s)/2+pos[0]*s,b.y+(b.height-1280*s)/2+pos[1]*s);await wait(400);
 shell('am broadcast -a ADB_CLEAR_TEXT');
 console.log('Search focused');const cdp=await page.context().newCDPSession(page);
 async function compose(text,expected){const t=Date.now();await cdp.send('Input.imeSetComposition',{text,selectionStart:text.length,selectionEnd:text.length});const ok=await page.evaluate(()=>keyTail);if(ok===false)console.log(await page.evaluate(()=>window.imeReplies));assert.notEqual(ok,false,'composition request failed');const ackMs=Date.now()-t;const value=read();assert.equal(value,expected);samples.push({preedit:text,expected,value,ackMs});}
 for(const text of ['ㅎ','하','한','하','ㅎ','한','한국'])await compose(text,text);
 assert(!requests.some(r=>r.path.endsWith('/text')||r.path.endsWith('/key')),'preedit must not commit append text or Enter');
 await cdp.send('Input.insertText',{text:'한국'});assert.notEqual(await page.evaluate(()=>keyTail),false);assert.equal(read(),'한국');
 const commits=requests.filter(r=>r.body.phase==='commit');assert.equal(commits.length,1);assert.equal(commits[0].body.text,'한국');
 await compose('어','한국어');await compose('','한국'); // cancel only the current composing span
 await cdp.send('Input.insertText',{text:' ABC🙂'});assert.notEqual(await page.evaluate(()=>keyTail),false);assert.equal(read(),'한국 ABC🙂');

 assert.deepEqual(errors,[]);
 const post=body=>page.evaluate(async body=>{const r=await fetch('/api/screen/composition',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});return r.status;},body);
 const a={session:'ownerA:1',phase:'start',text:''};
 assert.equal(await post(a),200);assert.equal(await post({...a,phase:'update',text:'검'}),200);
 assert.equal(await post({...a,session:'ownerB:1'}),503,'another viewer cannot steal a composition');
 assert.equal(await post({...a,phase:'commit',text:'검'}),200);
 assert.equal(await post({...a,phase:'update',text:'stale'}),503,'a retired composition cannot be replayed');
 assert.equal(read(),'한국 ABC🙂검');
 const focus={session:'ownerA:2',phase:'start',text:''};
 assert.equal(await post(focus),200);assert.equal(await post({...focus,phase:'update',text:'입력'}),200);
 shell('am force-stop com.android.settings.intelligence');await wait(200);
 assert.equal(await post({...focus,phase:'update',text:'stale focus'}),503,'focus loss invalidates old input connection');

 const result={browser:'Chrome CDP native composition',renderer:'png',device:'android-console VM, Android Settings search',samples,commitOnce:true,cancelPreservesPrefix:true,plainUnicodeAfterHelper:true,foreignViewerRejected:true,retiredSessionRejected:true,focusLossRejected:true,pageErrors:errors,requests};
 fs.writeFileSync(process.argv[2]||'/tmp/keyboard-composition-live.json',JSON.stringify(result,null,2));console.log(JSON.stringify({samples,commitOnce:true,cancelPreservesPrefix:true,plainUnicodeAfterHelper:true,foreignViewerRejected:true,retiredSessionRejected:true,focusLossRejected:true,errors}));
 }finally{shell('am broadcast -a ADB_CLEAR_TEXT');shell('am start -a android.settings.SETTINGS');await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
