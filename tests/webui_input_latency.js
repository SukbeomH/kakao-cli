// Exercise the real input handlers with delayed HTTP, without touching Android.
const assert=require('node:assert/strict'), vm=require('node:vm');
const page=require('node:fs').readFileSync(0,'utf8');
const calls=[], nodes={}, win={}, doc={};
function node(id){return nodes[id]??=( {tagName:'CANVAS',width:928,height:1728,handlers:{},
  addEventListener(n,f){this.handlers[n]=f;}, focus(){},setPointerCapture(){},
  getBoundingClientRect(){return {left:0,top:0,width:464,height:864};},
  classList:{add(){},remove(){}}});}
let release=null;
const context=vm.createContext({$:node,window:{addEventListener(n,f){win[n]=f;}},
 document:{addEventListener(n,f){doc[n]=f;}}, renderer:'wc', Date, Math,
 setTimeout,clearTimeout,setInterval,clearInterval,AbortSignal,setStatus(){},
 fetch:async(path,opts)=>{const body=JSON.parse(opts.body);calls.push({path,body});
   if(body.action==='down') await new Promise(r=>release=r);
   if(path.endsWith('/text')) await new Promise(r=>setTimeout(r,25));
   return {ok:true};}, clipToDevice:async()=>{},uploadOne(){}});
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
 calls.length=0; vm.runInContext('renderer="png"',context);
 el.handlers.pointerdown(event(10,20));assert.equal(calls.length,0);
 el.handlers.pointerup(event(10,20));await wait(5);
 assert.equal(calls[0].path,'/api/screen/tap');assert.equal(calls[0].body.src,'png');
 calls.length=0;
 const kb=node('kbcap');kb.value='한';kb.handlers.input({isComposing:true});await wait(80);
 assert.equal(calls.length,0,'never transmit uncommitted IME text');
 kb.handlers.compositionend();await wait(70);
 assert.equal(calls[0].body.text,'한');
 calls.length=0;
 // Fast typing must flush while it is ongoing, not only after a pause.
 for(let i=0;i<8;i++){kb.value='a';kb.handlers.input({isComposing:false});await wait(20);}
 assert.ok(calls.length>=2,'bounded 60ms batching must flush continuous input');
 kb.handlers.keydown({key:'Enter',stopPropagation(){},preventDefault(){}});
 await wait(100);
 assert.equal(calls.at(-1).path,'/api/screen/key','Enter follows all acknowledged text');
 console.log('PASS: immediate drag, coalescing, cancellation, wheel, PNG, IME and bounded ordered typing');
})().catch(e=>{console.error(e);process.exitCode=1;});
