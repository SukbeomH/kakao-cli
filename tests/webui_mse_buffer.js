// Execute the real MSE sink against a fake SourceBuffer, without a browser or device.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const source = require('node:fs').readFileSync(0, 'utf8');
const listeners = {}, removed = [], appended = [], failures = [];
let ranges = [[0, 46]], open, updating = false;
const sb = {
  get updating(){ return updating; },
  appendBuffer(p){ appended.push(p); updating = true; },
  remove(a,b){ removed.push([a,b]); updating = true; },
  addEventListener(name, cb){ listeners[name] = cb; },
};
const video = {
  currentTime: 45.95, paused: false,
  get buffered(){ return {length:ranges.length, start:i=>ranges[i][0], end:i=>ranges[i][1]}; },
  addEventListener(name,cb){ listeners[name] = cb; },
  play(){ return Promise.resolve(); },
};
class MediaSource {
  addEventListener(name,cb){ if(name === 'sourceopen') open = cb; }
  addSourceBuffer(){ return sb; }
}
const context = vm.createContext({
  $:()=>video, MediaSource, TextDecoder, URL:{createObjectURL:()=> 'blob:test'},
  onMeta(){}, vframe(){}, fallback:(...args)=>failures.push(args),
});
vm.runInContext(source + '; this.sink = mseSink();', context);
const send = (t, data)=>context.sink(t, new TextEncoder().encode(JSON.stringify(data)));
send(1, {codec:'avc1.42C029'}); open();
send(4, {key_time:0});
// With only the initial keyframe, arbitrary trimming at 20s used to remove the
// current GOP through the next (not yet received) keyframe and freeze playback.
listeners.timeupdate();
assert.equal(removed.length, 0, 'must retain the current GOP until another keyframe is known');
// Legacy servers without key metadata also remain playable instead of guessing.
send(1, {codec:'avc1.42C029'}); open(); listeners.timeupdate();
assert.equal(removed.length, 0);
// When a safe older keyframe exists, prune before it, even with pending media.
send(4, {key_time:0}); send(4, {key_time:30}); send(4, {key_time:44});
context.sink(3, new Uint8Array([1]));
assert.equal(removed.length, 1);
assert.ok(removed[0][1] > 29.99 && removed[0][1] < 30, 'keep the keyframe before the live GOP');
assert.equal(appended.length, 0, 'cleanup must not wait for an empty append queue');
ranges = [[30,46]]; updating = false; listeners.updateend();
assert.equal(appended.length, 1);
updating = false; listeners.updateend();
assert.equal(removed.length, 1, 'do not repeatedly remove the retained boundary');
// New session: old keyframe timestamps must not be applied to the reset timeline.
ranges = [[0,46]]; send(1, {codec:'avc1.42C029'}); open(); listeners.timeupdate();
assert.equal(removed.length, 1);
assert.equal(failures.length, 0);
console.log('PASS: sparse keys, legacy server, queued appends and session reset');
