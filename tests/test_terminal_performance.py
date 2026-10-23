import queue
from types import SimpleNamespace

import pytest

from kakao_cli import driver, kakaodb, tui
from kakao_cli.errors import KakaoError, SendUncertain
from terminal_fakes import Clock, ModelDevice, node


def model(monkeypatch, **kwargs):
    clock = Clock()
    monkeypatch.setattr(driver, 'time', clock)
    dev = ModelDevice(clock, **kwargs)
    return clock, dev, driver.Driver(dev)


@pytest.mark.parametrize('delay', [.05, .18, .8, 1.8])
def test_navigation_waits_for_selected_all_and_self_chat(monkeypatch, delay):
    clock, dev, d = model(monkeypatch, delay=delay)
    d.open_chats_tab()
    assert dev.page == 'chats' and dev.all_selected
    assert dev.actions.count(11) == 1 and dev.actions.count(71) == 1
    d.open_chats_tab()
    assert dev.actions.count(11) == 1 and dev.actions.count(71) == 1, 'selected controls need no retap'
    d.open_self_chat()
    assert dev.page == 'chat'


@pytest.mark.parametrize('delay', [.05, .18, .8])
def test_send_waits_for_focus_commit_and_new_log_without_retry(monkeypatch, delay):
    clock, dev, d = model(monkeypatch, page='chat', delay=delay)
    dev.messages = ['synthetic']  # an old identical row cannot prove a new send
    assert d.send_in_room('synthetic') == 'verified'
    assert dev.messages == ['synthetic', 'synthetic']
    assert dev.typed == dev.submissions == 1


def test_unconfirmed_send_is_uncertain_even_with_identical_history_or_draft(monkeypatch):
    clock, dev, d = model(monkeypatch, page='chat', acknowledge=False)
    dev.messages = ['synthetic']
    with pytest.raises(SendUncertain): d.send_in_room('synthetic')
    assert dev.typed == dev.submissions == 1


def test_no_focus_or_commit_never_taps_send(monkeypatch):
    clock, dev, d = model(monkeypatch, page='chat')
    dev.tap = lambda *a: None
    with pytest.raises(KakaoError, match='focus'): d.send_in_room('synthetic')
    assert dev.typed == dev.submissions == 0
    dev.focused = True
    dev.type_text = lambda text: None
    with pytest.raises(KakaoError, match='not ready'): d.send_in_room('synthetic')
    assert dev.submissions == 0


def test_failed_readback_after_submit_is_uncertain_and_never_retries(monkeypatch):
    clock,dev,d=model(monkeypatch,page='chat')
    original=dev.dump
    def dump():
        if dev.submissions: raise OSError('synthetic disconnect')
        return original()
    dev.dump=dump
    with pytest.raises(SendUncertain): d.send_in_room('synthetic')
    assert dev.typed == dev.submissions == 1


@pytest.mark.parametrize('delay',[.18,.8])
def test_document_picker_waits_for_search_focus_and_selects_result_not_search_box(monkeypatch,delay):
    clock=Clock(); monkeypatch.setattr(driver,'time',clock)
    class Picker(ModelDevice):
        search_focus=False
        query=''
        selected=0
        def push(self,local,remote): pass  # synthetic public file; no filesystem or ADB
        def shell(self,command): assert command.startswith('am broadcast ')
        def current_activity(self):
            self.update()
            return 'com.kakao.talk/.activity.main.MainActivity' if self.page=='chat' else None
        def dump(self):
            xml=super().dump()
            extra=[]
            if self.page=='chat': extra=[node('media_send_layout',111)]
            elif self.page=='sheet': extra=[node('file',121,text='File')]
            elif self.page=='select': extra=[node('select',131,text='Select from File')]
            elif self.page=='picker':
                extra=[node('option_menu_search',141),node('search_src_text',151,text=self.query,focused=str(self.search_focus).lower())]
                if self.query: extra += [node('title',161,text=self.query)]
            return xml.replace('</hierarchy>',''.join(extra)+'</hierarchy>')
        def tap(self,x,y):
            self.clock.sleep(.03); self.update()
            if x in (111,121,131,161):
                dest={111:'sheet',121:'select',131:'picker',161:'chat'}[x]
                self.later(lambda:setattr(self,'page',dest))
                if x==161: self.selected+=1
            elif x==141: self.later(lambda:setattr(self,'search_focus',True))
            else: raise AssertionError('selected the search box instead of the document')
        def type_text(self,text):
            self.update(); assert self.search_focus
            self.typed+=1; self.later(lambda:setattr(self,'query',text))
    dev=Picker(clock,page='chat',delay=delay); d=driver.Driver(dev)
    d._open_target=lambda target:None
    assert d.send_doc('me','synthetic.txt')=='sent'
    assert dev.typed==dev.selected==1 and dev.page=='chat'


def test_waits_back_off_and_do_not_sleep_past_deadline(monkeypatch):
    clock, dev, d = model(monkeypatch)
    assert driver._poll(lambda: False, timeout=.25) is False
    assert clock.now == pytest.approx(100.25)
    assert clock.sleeps == pytest.approx([.1, .15])
    with pytest.raises(ValueError): driver._poll(lambda: False, interval=0)


def guarded(monkeypatch):
    obj = object.__new__(kakaodb.Database)
    state = {'version':kakaodb.VERSION_CODE, 'schema':1, 'inode':'1:2',
             'boot':'11111111-1111-1111-1111-111111111111', 'probes':0, 'columns':0, 'bad':False}
    def shell(command):
        state['probes'] += 1
        assert 'sqlite3 -readonly -json' in command and 'PRAGMA schema_version' in command
        assert not any(x in command for x in ('VACUUM', 'checkpoint', 'UPDATE ', 'DELETE '))
        if state['bad']: raise OSError('PRIVATE TRANSPORT ERROR')
        return f"{state['boot']}\n{state['inode']}\nversionCode={state['version']}\n" + '[{"schema_version":'+str(state['schema'])+'}]'
    obj.dev = SimpleNamespace(adb='fake', serial='fake', shell=shell)
    def check(): state['columns'] += 1
    obj._check_columns = check
    clock = Clock(); monkeypatch.setattr(kakaodb, 'time', clock)
    return obj, state, clock


def test_guard_probes_every_time_but_reuses_columns_with_bounded_ttl(monkeypatch):
    obj, s, clock = guarded(monkeypatch)
    obj.guard(); obj.guard()
    assert (s['probes'], s['columns']) == (2, 1)
    clock.sleep(obj.GUARD_TTL); obj.guard()
    assert s['columns'] == 2
    obj.guard(force=True); assert s['columns'] == 3


@pytest.mark.parametrize('field,value', [('schema',2), ('inode','1:3'), ('boot','22222222-2222-2222-2222-222222222222')])
def test_guard_invalidates_immediately_on_identity_change(monkeypatch, field, value):
    obj, s, clock = guarded(monkeypatch)
    obj.guard(); s[field] = value; obj.guard()
    assert s['columns'] == 2
    obj.dev.serial = 'other'; obj.guard(); assert s['columns'] == 3
    obj.dev = SimpleNamespace(**vars(obj.dev)); obj.guard(); assert s['columns'] == 4


def test_guard_update_failure_and_query_error_cannot_reuse_old_validation(monkeypatch):
    obj, s, clock = guarded(monkeypatch); obj.guard()
    s['version'] = 1
    with pytest.raises(kakaodb.DBUnavailable, match='versionCode'): obj.guard()
    assert obj._guard_key is None
    s['version'] = kakaodb.VERSION_CODE; obj.guard(); assert s['columns'] == 2
    s['bad'] = True
    with pytest.raises(kakaodb.DBUnavailable) as err: obj.guard()
    assert 'PRIVATE' not in str(err.value) and obj._guard_key is None
    s['bad'] = False; obj.guard()
    monkeypatch.setattr(kakaodb.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1, stdout='PRIVATE'))
    with pytest.raises(kakaodb.DBUnavailable): obj._query('KakaoTalk.db', 'SELECT 1')
    assert obj._guard_key is None
    obj.guard(); assert s['columns'] == 4


def test_schema_failure_is_not_cached(monkeypatch):
    obj, s, clock = guarded(monkeypatch)
    def broken(): raise kakaodb.DBUnavailable('missing column')
    obj._check_columns = broken
    for _ in range(2):
        with pytest.raises(kakaodb.DBUnavailable): obj.guard()
        assert obj._guard_key is None


def test_columns_use_one_readonly_query_and_reject_missing_columns():
    import sqlite3
    db = sqlite3.connect(':memory:')
    columns = {
        'chat_logs':'_id id type chat_id user_id message attachment created_at v',
        'chat_rooms':'id type members active_member_ids watermarks last_read_log_id meta private_meta link_id unread_count mute_until_at last_updated_at',
    }
    for table, names in columns.items():
        db.execute('CREATE TABLE '+table+' ('+','.join(name+' TEXT' for name in names.split())+')')
    obj=object.__new__(kakaodb.Database); calls=[]
    def query(database, sql):
        assert database == 'KakaoTalk.db' and sql.startswith('SELECT ')
        calls.append(sql)
        cursor=db.execute(sql)
        return [dict(zip([c[0] for c in cursor.description], row)) for row in cursor.fetchall()]
    obj._query=query
    obj._check_columns(); assert len(calls)==1
    db.execute('ALTER TABLE chat_logs DROP COLUMN message')
    with pytest.raises(kakaodb.DBUnavailable, match='필수 열'): obj._check_columns()
    db.close()


@pytest.mark.parametrize('fingerprint', ['PRIVATE',
    '11111111-1111-1111-1111-111111111111\n1:2\nversionCode=29260830\n[{"schema_version":true}]',
    '11111111-1111-1111-1111-111111111111\n1:2\nversionCode=29260830\nPRIVATE'])
def test_bad_fingerprint_never_reuses_or_exposes_raw_output(monkeypatch, fingerprint):
    obj,s,clock=guarded(monkeypatch); obj.guard()
    obj.dev.shell=lambda command: fingerprint
    with pytest.raises(kakaodb.DBUnavailable) as err: obj.guard()
    assert obj._guard_key is None and 'PRIVATE' not in str(err.value)


def test_tui_drain_only_reports_real_display_changes_and_preserves_duplicate_messages():
    worker = SimpleNamespace(events=queue.Queue())
    t = tui.TUI(worker)
    worker.events.put(('status', t.status)); worker.events.put(('messages', ('other', ['ignore'])))
    assert not t._drain()
    worker.events.put(('opened', 'room')); worker.events.put(('messages', ('room', ['same', 'same'])))
    assert t._drain() and t.messages == ['same', 'same']
    worker.events.put(('messages', ('room', [{'text':'same'}, {'text':'same'}])))
    assert not t._drain()
    worker.events.put(('messages', ('room', ['same', 'same', 'last'])))
    assert t._drain() and t.messages[-1] == 'last'


def test_tui_idle_resize_keyboard_and_every_send_are_preserved(monkeypatch):
    from collections import deque
    monkeypatch.setattr(tui.curses, 'curs_set', lambda *a: None)
    commands, draws = [], []
    worker = SimpleNamespace(events=queue.Queue(), start=lambda: None, put=lambda *x: commands.append(x))
    t = tui.TUI(worker)
    keys = deque([None, None, tui.curses.KEY_RESIZE, '\t', 'a', '\n', 'b', '\n', '\t', 'q'])
    class Screen:
        def keypad(self, flag): pass
        def timeout(self, ms): assert ms == 50
        def get_wch(self):
            key = keys.popleft()
            if key is None: raise tui.curses.error()
            return key
    t._draw = lambda scr: draws.append((t.focus, t.input))
    t.loop(Screen())
    assert len(draws) == 8  # initial, resize, Tab, a, cleared, b, cleared, Tab
    assert commands == [('send','a'), ('send','b'), ('quit',)]
    assert t.sel == 0
