"""Developer A/B benchmark; emits timings/counts only, never message contents.

Save driver.py, kakaodb.py and tui.py before editing, then:
  PYTHONPATH=src python tests/benchmark_terminal.py --before /tmp/before --after src/kakao_cli
For read-only live DB measurements, run *inside* the ADB-capable container:
  ... --part db --serial redroid:5555
Driver uses a synthetic clock/device; TUI uses real curses in a local PTY with
synthetic data and a worker that cannot send. DB mode never drives the app UI.
"""
import argparse
import contextlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import queue
import select
import subprocess
import sys
import threading
import time
from types import SimpleNamespace


def load(directory, kind, label):
    name = 'kakao_cli._bench_' + label + '_' + kind
    spec = importlib.util.spec_from_file_location(name, Path(directory) / (kind + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def stats(values):
    a = sorted(values)
    return {'n':len(a), 'p50':round(a[math.ceil(len(a)*.5)-1],3),
            'p95':round(a[math.ceil(len(a)*.95)-1],3), 'max':round(a[-1],3)} if a else {'n':0}


def driver_bench(directory, label):
    from terminal_fakes import Clock, ModelDevice
    module = load(directory, 'driver', label)
    samples = {k:[] for k in ('chats_ms','self_chat_ms','send_model_ms','dumps')}
    for i in range(20):
        clock = Clock(); module.time = clock
        dev = ModelDevice(clock, delay=.08 + .01*(i%19))
        d = module.Driver(dev)
        for key, action in (('chats_ms',d.open_chats_tab), ('self_chat_ms',d.open_self_chat),
                            ('send_model_ms',lambda: d.send_in_room('synthetic'))):
            began = clock.now; action(); samples[key].append((clock.now-began)*1000)
        assert dev.typed == dev.submissions == 1 and dev.messages == ['synthetic']
        samples['dumps'].append(dev.dumps)
    return {'kind':'driver_model', 'variant':label, 'stats':{k:stats(v) for k,v in samples.items()},
            'samples':samples, 'dump_cost_ms':60, 'ui_ready_ms_range':[80,260]}


def pty_child(directory, fd, mode):
    import curses
    module = load(directory, 'tui', 'child')
    worker = SimpleNamespace(events=queue.Queue(), commands=[])
    arrival, delays, drawn = {}, [], set()
    def start():
        def produce():
            if mode == 'active':
                for i in range(7):
                    time.sleep(.31 if i == 0 else .281)
                    text = 'synthetic-tick-' + str(i)
                    arrival[text] = time.perf_counter()
                    worker.events.put(('status', text))
        threading.Thread(target=produce, daemon=True).start()
        os.write(fd, b'READY\n')
    worker.start = start
    worker.put = lambda *cmd: worker.commands.append(cmd)
    t = module.TUI(worker)
    t.rooms = [{'name':'synthetic room','unread':0}]
    t.messages = ['synthetic message ' + str(i) for i in range(100)]
    real_draw, draws = t._draw, []
    def draw(scr):
        draws.append(time.perf_counter())
        real_draw(scr)
        if t.status in arrival and t.status not in drawn:
            delays.append((time.perf_counter()-arrival[t.status])*1000); drawn.add(t.status)
    t._draw = draw
    cpu = time.process_time(); began = time.perf_counter()
    curses.wrapper(t.loop)
    result = {'draws':len(draws), 'cpu_ms':(time.process_time()-cpu)*1000,
              'seconds':time.perf_counter()-began, 'event_delays_ms':delays,
              'event_count':len(arrival), 'quit_received':worker.commands == [('quit',)]}
    os.write(fd, (json.dumps(result)+'\n').encode()); os.close(fd)


def tui_bench(directory, label, mode):
    import fcntl
    import pty
    import struct
    import termios
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH',30,100,0,0))
    read_fd, write_fd = os.pipe()
    env = dict(os.environ, TERM='xterm-256color')
    child = subprocess.Popen([sys.executable, __file__, '--pty-child',str(Path(directory).resolve()),
                              str(write_fd),mode],stdin=slave,stdout=slave,stderr=slave,
                              pass_fds=(write_fd,),env=env)
    os.close(slave); os.close(write_fd)
    raw, report, ready = bytearray(), bytearray(), None
    wrote = set(); deadline = time.monotonic()+8
    try:
        while time.monotonic()<deadline:
            now = time.monotonic()
            if ready is not None:
                elapsed = now-ready
                schedule = [(.15,b'\t'),(.22,b'abc'),(2.6,b'\tq')] if mode=='active' else [(2.1,b'q')]
                for i,(at,data) in enumerate(schedule):
                    if elapsed>=at and i not in wrote:
                        os.write(master,data); wrote.add(i)
            for descriptor in select.select([master,read_fd],[],[],.01)[0]:
                try: chunk = os.read(descriptor,65536)
                except OSError: chunk = b''
                if descriptor==master: raw.extend(chunk)
                else:
                    report.extend(chunk)
                    if ready is None and b'READY\n' in report: ready=time.monotonic()
            if child.poll() is not None: break
        child.wait(timeout=1)
        if child.returncode: raise RuntimeError('synthetic PTY failed')
        report.extend(os.read(read_fd,65536))
        data = json.loads(report.decode().splitlines()[-1])
        assert data['quit_received']
        if mode=='active': assert len(data['event_delays_ms'])==data['event_count']==7
        return {'kind':'tui_pty','variant':label,'mode':mode,'terminal_bytes':len(raw),**data}
    finally:
        if child.poll() is None: child.kill(); child.wait()
        os.close(master); os.close(read_fd)


def db_bench(before, after, serial, adb):
    from kakao_cli import cli, receiver
    from kakao_cli.adb import Device
    mods = {label:load(path,'kakaodb',label) for label,path in (('before',before),('after',after))}
    original, original_driver = receiver.Database, receiver.Driver
    def no_ui(*args): raise RuntimeError('DB benchmark refuses UI fallback')
    receiver.Driver = no_ui
    outputs, results = {}, []
    try:
        args = SimpleNamespace(serial=serial,addr=None,adb=adb,source='db',room='me',limit=20)
        # Full command handler: includes device discovery and Database/profile setup.
        for label in ('before','after'):
            receiver.Database = mods[label].Database
            with contextlib.redirect_stderr(io.StringIO()): cli.cmd_read(args)
        for label in ['before','after','after','before'] * 3:
            receiver.Database = mods[label].Database
            began = time.perf_counter()
            with contextlib.redirect_stderr(io.StringIO()): result=cli.cmd_read(args)
            elapsed=(time.perf_counter()-began)*1000
            if result['source']!='db': raise RuntimeError('DB fallback: discard benchmark')
            outputs.setdefault(label,result)
            if result != outputs[label]: raise RuntimeError('live result changed during cold samples')
            results.append({'kind':'cli_read_cold','variant':label,'ms':elapsed})
        # TUI's resident Receiver follows guard + read_detailed on each refresh.
        objects = {label:mod.Database(Device(serial,adb)) for label,mod in mods.items()}
        for label,obj in objects.items(): obj.guard()
        for i in range(12):
            for label in (('before','after') if i%2==0 else ('after','before')):
                obj=objects[label]; began=time.perf_counter()
                obj.guard(); result=obj.read_detailed('me',20)
                elapsed=(time.perf_counter()-began)*1000
                if result != outputs[label]['message_details']: raise RuntimeError('live result changed during warm samples')
                results.append({'kind':'resident_db_read','variant':label,'ms':elapsed})
        if outputs['before']!=outputs['after']: raise RuntimeError('before/after DB results differ')
        # Real new Python/CLI processes. Render JSON into RAM, never the terminal.
        for label in ['before','after','after','before'] * 2:
            began=time.perf_counter()
            run=subprocess.run([sys.executable,__file__,'--cli-child',
                                str(Path(before if label=='before' else after).resolve()),serial,adb],
                               capture_output=True,text=True,timeout=30)
            elapsed=(time.perf_counter()-began)*1000
            if run.returncode or json.loads(run.stdout) != {'db_read_ok':True}:
                raise RuntimeError('isolated CLI read failed; discard benchmark')
            results.append({'kind':'cli_process_read','variant':label,'ms':elapsed})
        return {'kind':'db_live','same_result':True,'samples':results}
    finally:
        receiver.Database=original
        receiver.Driver=original_driver


def driver_live(before, after, serial, adb):
    """Friends -> Chats/All only; requires mirror mode, never opens a room."""
    from kakao_cli.adb import Device
    from kakao_cli import consolelock
    class CountingDevice(Device):
        calls = 0
        dumps = 0
        def _run(self, *args, **kwargs):
            self.calls += 1
            return super()._run(*args, **kwargs)
        def dump(self):
            self.dumps += 1
            return super().dump()
    dev = CountingDevice(serial,adb)
    consolelock.check(dev)
    modules={label:load(path,'driver',label) for label,path in (('before',before),('after',after))}
    setup=modules['after'].Driver(dev)
    dev.shell('am start -n com.kakao.talk/.activity.main.MainActivity')
    results=[]
    for label in ('before','after','after','before'):
        consolelock.check(dev)
        friends=setup._find(lambda s: modules['after']._desc_prefix(s,'Friends tab'),timeout=10)
        if not friends: raise RuntimeError('Friends tab unavailable')
        setup.tap_node(friends)
        if not setup._screen_where(lambda s: modules['after']._selected(modules['after']._desc_prefix(s,'Friends tab'))
                                  and modules['after']._desc_contains(s,'Profile photo'),timeout=10):
            raise RuntimeError('Friends reset failed')
        calls,dumps=dev.calls,dev.dumps; began=time.perf_counter()
        modules[label].Driver(dev).open_chats_tab()
        results.append({'variant':label,'ms':(time.perf_counter()-began)*1000,
                        'adb_calls':dev.calls-calls,'dumps':dev.dumps-dumps})
        if not setup._screen_where(lambda s: modules['after']._selected(modules['after']._all_chip(s)),timeout=5):
            raise RuntimeError('All filter not selected')
    return {'kind':'driver_live_chats','samples':results}


def main():
    if len(sys.argv)>1 and sys.argv[1]=='--pty-child':
        pty_child(sys.argv[2],int(sys.argv[3]),sys.argv[4]); return
    if len(sys.argv)>1 and sys.argv[1]=='--cli-child':
        from kakao_cli import cli, receiver
        receiver.Database=load(sys.argv[2],'kakaodb','isolated').Database
        def no_ui(*args): raise RuntimeError('DB benchmark refuses UI fallback')
        receiver.Driver=no_ui
        out=io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code=cli.main(['read','me','--serial',sys.argv[3],'--adb',sys.argv[4],'--json'])
        response=json.loads(out.getvalue())
        if code or response.get('source')!='db': raise RuntimeError('isolated DB read failed')
        print(json.dumps({'db_read_ok':True})); return
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--before',required=True); ap.add_argument('--after',required=True)
    ap.add_argument('--part',choices=['local','driver','tui','db','driver-live'],default='local')
    ap.add_argument('--serial',default='redroid:5555'); ap.add_argument('--adb',default='adb')
    args=ap.parse_args(); results=[]
    if args.part in ('local','driver'):
        results += [driver_bench(path,label) for label,path in (('before',args.before),('after',args.after))]
    if args.part in ('local','tui'):
        for label in ('before','after','after','before'):
            for mode in ('idle','active'):
                results.append(tui_bench(args.before if label=='before' else args.after,label,mode))
    if args.part=='db': results.append(db_bench(args.before,args.after,args.serial,args.adb))
    if args.part=='driver-live': results.append(driver_live(args.before,args.after,args.serial,args.adb))
    print(json.dumps(results,ensure_ascii=False))


if __name__=='__main__': main()
