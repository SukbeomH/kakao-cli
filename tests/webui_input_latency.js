// Exercise the real input handlers with delayed HTTP, without touching Android.
const assert=require('node:assert/strict'), vm=require('node:vm');
const page=require('node:fs').readFileSync(0,'utf8');
const calls=[], nodes={}, win={}, doc={};
function node(id){return nodes[id]??=( {tagName:'CANVAS',width:928,height:1728,handlers:{},
  addEventListener(n,f){this.handlers[n]=f;}, focus(){},blur(){this.handlers.blur?.();},setPointerCapture(){},
  getBoundingClientRect(){return {left:0,top:0,width:464,height:864};},
  classList:{add(){},remove(){}}});}
let release=null,textDelay=25,failText=false;
const context=vm.createContext({$:node,window:{addEventListener(n,f){win[n]=f;}},
 document:{addEventListener(n,f){doc[n]=f;}}, renderer:'wc', Date, Math,
 setTimeout,clearTimeout,setInterval,clearInterval,AbortSignal,setStatus(){},
 fetch:async(path,opts)=>{const body=JSON.parse(opts.body);calls.push({path,body});
   if(body.action==='down') await new Promise(r=>release=r);
   if(path.endsWith('/text') || path.endsWith('/composition')){
     const fail=failText;failText=false;
     await new Promise(r=>setTimeout(r,textDelay));
     if(fail)return {ok:false};
   }
   return {ok:true};}, clipToDevice:async(text,paste)=>{
     calls.push({path:'/api/clip',body:{text,paste}});await new Promise(r=>setTimeout(r,25));return true;
   },uploadOne(){}});
const input=page.split('async function scrPost(path, body){')[1].split('// ---- file managers')[0];
const keyboard=page.split('// ---- keyboard → device:')[1].split('// ================= H.264')[0];
vm.runInContext('async function scrPost(path, body){'+input+'\n// ---- keyboard → device:'+keyboard,context);
const wait=ms=>new Promise(r=>setTimeout(r,ms));
const event=(x,y,extra={})=>({button:0,pointerId:1,clientX:x,clientY:y,preventDefault(){this.prevented=true;},...extra});
(async()=>{
 const el=node('vcanvas');
 el.handlers.pointerdown(event(100,200));
 assert.equal(calls.length,1,'down must reach HTTP before pointerup');
 assert.equal(calls[0].body.action,'down');
 for(let i=0;i<20;i++){el.handlers.pointermove(event(100,220+i));await wait(18);}
 assert.equal(calls.length,1,'only one in-flight request on a slow connection');
 el.handlers.pointerup(event(100,250)); release(); await wait(30);
 assert.deepEqual(calls.map(c=>c.body.action),['down','move','up']);
 assert.equal(calls[1].body.cy,239,'keep the newest pending move');
 assert.equal(calls[2].body.cy,250);
 calls.length=0;
 el.handlers.pointerdown(event(100,200)); release(); await wait(5);
 el.handlers.pointermove(event(101,201));win.blur();await wait(25);
 assert.deepEqual(calls.map(c=>c.body.action),['down','cancel'],'blur releases the finger');
 calls.length=0;
 const wheel=event(100,200,{deltaX:0,deltaY:120,deltaMode:0});
 el.handlers.wheel(wheel);await wait(5);
 assert.equal(wheel.prevented,true);assert.equal(calls[0].path,'/api/screen/scroll');
 assert.equal(calls[0].body.dy,-1,'wheel down maps to negative Android vertical axis');
 assert.equal(calls[0].body.dx,0);
 calls.length=0;
 el.handlers.wheel(event(100,200,{deltaX:120,deltaY:0,deltaMode:0}));await wait(5);
 el.handlers.wheel(event(100,200,{deltaX:120,deltaY:1,deltaMode:0}));await wait(5);
 el.handlers.wheel(event(100,200,{deltaX:0,deltaY:120,deltaMode:0,shiftKey:true}));await wait(5);
 assert.equal(calls.length,0,'horizontal and Shift-wheel must not reach the device');
 const secondary=event(100,200,{button:2});
 el.handlers.pointerdown(secondary);assert.equal(calls.length,0,'right button must not start a finger gesture');
 el.handlers.contextmenu(secondary);await wait(5);
 assert.equal(secondary.prevented,true);
 assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/screen/rightclick');
 assert.equal(calls[0].body.src,'video');
 calls.length=0; vm.runInContext('renderer="png"',context);
 el.handlers.contextmenu(event(10,20,{button:2}));await wait(5);
 assert.equal(calls.length,0,'PNG right-click must not turn into a left click or back');
 el.handlers.pointerdown(event(10,20));assert.equal(calls.length,0);
 el.handlers.pointerup(event(10,20));await wait(5);
 assert.equal(calls[0].path,'/api/screen/tap');assert.equal(calls[0].body.src,'png');
 calls.length=0;
 const kb=node('kbcap');kb.handlers.compositionstart();
 for(const text of ['ㅎ','하','한']){kb.value=text;kb.handlers.input({isComposing:true});}
 kb.handlers.keydown({key:'Enter',isComposing:true,stopPropagation(){},preventDefault(){}});
 await vm.runInContext('keyTail',context);
 assert.deepEqual(calls.map(c=>c.body.phase),['start','update']);
 assert.equal(calls[1].body.text,'한','pending preedit replaces rather than appends');
 kb.handlers.compositionend();
 kb.handlers.input({isComposing:false}); // trailing browser input must not duplicate the commit
 await vm.runInContext('keyTail',context);
 assert.deepEqual(calls.map(c=>c.body.phase),['start','update','commit']);
 assert.equal(calls[2].body.text,'한');
 assert(!calls.some(c=>c.path.endsWith('/text') || c.path.endsWith('/key')),'composition must never append text or submit Enter');
 calls.length=0;
 kb.handlers.compositionstart();kb.value='취소';kb.handlers.input({isComposing:true});
 kb.value='';kb.handlers.compositionend();
 await vm.runInContext('keyTail',context);
 assert.equal(calls.at(-1).body.phase,'commit');assert.equal(calls.at(-1).body.text,'','cancellation removes only the composing span');
 calls.length=0;
 vm.runInContext('renderer="wc"',context);
 kb.handlers.compositionstart();kb.value='한';kb.handlers.input({isComposing:true});
 el.handlers.pointerdown(event(100,200));
 assert(!calls.some(c=>c.body.action==='down'),'a focus-changing tap must wait for composition completion');
 await vm.runInContext('keyTail',context);await wait(5);
 assert.equal(calls.at(-1).body.action,'down');
 assert.equal(calls.at(-2).body.phase,'commit');
 el.handlers.pointerup(event(100,200));release();await wait(10);
 calls.length=0;
 // Fast typing must flush while it is ongoing, not only after a pause.
 for(let i=0;i<8;i++){kb.value='a';kb.handlers.input({isComposing:false});await wait(20);}
 assert.ok(calls.length>=2,'typing must flush while input continues');
 kb.handlers.keydown({key:'Enter',stopPropagation(){},preventDefault(){}});
 await wait(100);
 assert.equal(calls.at(-1).path,'/api/screen/key','Enter follows all acknowledged text');
 await vm.runInContext('keyTail',context);
 calls.length=0;textDelay=120;
 kb.value='first';kb.handlers.input({isComposing:false});
 assert.equal(calls.length,1,'first committed text must dispatch immediately, without a batching timer');
 for(const text of ['2','3','4']){kb.value=text;kb.handlers.input({isComposing:false});}
 kb.handlers.keydown({key:'Enter',stopPropagation(){},preventDefault(){}});
 kb.value='last';kb.handlers.input({isComposing:false});
 assert.equal(calls.length,1,'one request in flight even while typing continues');
 await vm.runInContext('keyTail',context);
 assert.deepEqual(calls.map(c=>c.path),['/api/screen/text','/api/screen/text','/api/screen/key','/api/screen/text']);
 assert.deepEqual(calls.filter(c=>c.body.text).map(c=>c.body.text),['first','234','last'],'coalesce pending text, never across Enter');
 calls.length=0;failText=true;
 kb.value='uncertain';kb.handlers.input({isComposing:false});
 kb.value='pending';kb.handlers.input({isComposing:false});
 kb.handlers.keydown({key:'Enter',stopPropagation(){},preventDefault(){}});
 assert.equal(await vm.runInContext('keyTail',context),false);
 assert.equal(calls.length,1,'failed text must cancel unsent text and Enter without replay');
 kb.value='recovery';kb.handlers.input({isComposing:false});
 assert.equal(await vm.runInContext('keyTail',context),true,'a later explicit input can recover');
 calls.length=0;
 kb.value='before';kb.handlers.input({isComposing:false});
 kb.handlers.paste({clipboardData:{files:[],getData:()=> 'clipboard'},preventDefault(){}});
 kb.value='after';kb.handlers.input({isComposing:false});
 kb.handlers.keydown({key:'Enter',stopPropagation(){},preventDefault(){}});
 await vm.runInContext('keyTail',context);
 assert.deepEqual(calls.map(c=>c.path),['/api/screen/text','/api/clip','/api/screen/text','/api/screen/key']);
 assert.deepEqual(calls.slice(0,3).map(c=>c.body.text),['before','clipboard','after'],'paste stays between typed text and Enter');
 assert.equal(calls[1].body.paste,true);
 calls.length=0;
 kb.handlers.compositionstart();kb.value='조합';kb.handlers.input({isComposing:true});
 kb.handlers.paste({clipboardData:{files:[],getData:()=> '붙임'},preventDefault(){}});
 await vm.runInContext('keyTail',context);
 assert.equal(calls.at(-2).body.phase,'commit');
 assert.equal(calls.at(-1).path,'/api/clip','paste must finish the current composition first');
 console.log('PASS: pointer input, IME, immediate text, one-flight coalescing, Enter/paste barriers and failure without replay');
})().catch(e=>{console.error(e);process.exitCode=1;});
