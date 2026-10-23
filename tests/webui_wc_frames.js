// Exercise the real decoder sink with bursts, session replacement and teardown.
const assert=require('node:assert/strict'), vm=require('node:vm');
const source=require('node:fs').readFileSync(0,'utf8');
let next=0, counted=0, keys=0, failDraw=false;
const raf=new Map(), drawn=[], failures=[], decoders=[];
const canvas={width:0,height:0,dataset:{},getContext:()=>({drawImage(f){
  if(failDraw) throw Error('draw failed');
  drawn.push(f.id);
}})};
class VideoDecoder{
  constructor(callbacks){this.cb=callbacks; this.state='unconfigured';this.decodeQueueSize=0;decoders.push(this);}
  configure(){this.state='configured';}
  close(){this.state='closed';}
  reset(){this.state='unconfigured';this.decodeQueueSize=0;}
  decode(chunk){this.chunk=chunk;}
}
const context=vm.createContext({
  $:()=>canvas, VideoDecoder, TextDecoder, Uint8Array, DataView,
  EncodedVideoChunk:class{constructor(o){Object.assign(this,o);}},
  onMeta(){},vframe(){counted++;},fallback:(...x)=>failures.push(x),requestVideoKey(){keys++;},
  requestAnimationFrame(cb){const id=++next;raf.set(id,cb);return id;},
  cancelAnimationFrame(id){raf.delete(id);},
});
vm.runInContext(source+';this.sink=wcSink()',context);
const sink=context.sink;
const meta=()=>sink(1,new TextEncoder().encode(JSON.stringify({codec:'avc1.42C029'})));
const frame=id=>({id,displayWidth:720,displayHeight:1280,closed:0,close(){assert.equal(this.closed++,0,'close once');}});
function tick(){const work=[...raf.values()];raf.clear();work.forEach(cb=>cb());}
meta();const first=decoders[0], a=frame('a'),b=frame('b'),c=frame('c');
first.cb.output(a);first.cb.output(b);first.cb.output(c);
assert.equal(raf.size,1);assert.equal(a.closed,1);assert.equal(b.closed,1);assert.equal(c.closed,0);
assert.deepEqual(drawn,['a'],'first output must draw immediately without waiting for repaint');
assert.equal(counted,0,'count once per repaint opportunity, not once per decoder output');
tick();assert.deepEqual(drawn,['a','c']);assert.equal(c.closed,1);assert.equal(counted,1);
assert.equal(canvas.width,720);assert.equal(canvas.height,1280);
const immediate=frame('immediate'),stale=frame('stale');first.cb.output(immediate);first.cb.output(stale);meta();
assert.equal(stale.closed,1);assert.equal(raf.size,0);assert.equal(first.state,'closed');
const late=frame('late');first.cb.output(late);first.cb.error(Error('old decoder'));
assert.equal(late.closed,1);assert.equal(failures.length,0);
const second=decoders[1], pending=frame('pending');second.cb.output(frame('first'));second.cb.output(pending);
// Backpressure must release unpainted work and wait for a new keyframe.
second.decodeQueueSize=5;const packet=new Uint8Array(10);
sink(3,packet);assert.equal(pending.closed,1);assert.equal(raf.size,0);assert.equal(keys,1);
assert.equal(second.chunk,undefined);packet[0]=1;sink(3,packet);assert.equal(second.chunk.type,'key');
const teardown=frame('teardown');second.cb.output(frame('first2'));second.cb.output(teardown);sink.dispose();sink.dispose();
assert.equal(teardown.closed,1);assert.equal(raf.size,0);assert.equal(second.state,'closed');
const after=frame('after');second.cb.output(after);second.cb.error(Error('disposed'));
assert.equal(after.closed,1);assert.equal(failures.length,0);tick();assert.equal(counted,1);
// A canvas failure still releases the VideoFrame and triggers normal fallback.
vm.runInContext('this.sink=wcSink()',context);const other=context.sink;
other(1,new TextEncoder().encode(JSON.stringify({codec:'avc1.42C029'})));
const bad=frame('bad');failDraw=true;decoders.at(-1).cb.output(bad);tick();
assert.equal(bad.closed,1);assert.equal(failures.length,1);assert.equal(counted,1);other.dispose();
console.log('PASS: latest frame, paint FPS, stale session, overload, disposal and draw failure');
