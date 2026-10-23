"""Synthetic navigation/input model. No ADB, account data or network access."""
from html import escape

P = 'com.kakao.talk:id/'


class Clock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def monotonic(self): return self.now
    def time(self): return self.now
    def sleep(self, seconds):
        assert seconds >= 0
        self.sleeps.append(seconds)
        self.now += seconds


def node(rid, x, text='', desc='', **attrs):
    fields = {'resource-id': P + rid, 'bounds': f'[{x-1},0][{x+1},2]',
              'text': text, 'content-desc': desc, **attrs}
    return '<node ' + ' '.join(f'{k}="{escape(str(v), quote=True)}"' for k, v in fields.items()) + '/>'


class ModelDevice:
    def __init__(self, clock, page='friends', delay=.18, acknowledge=True):
        self.clock, self.page, self.delay = clock, page, delay
        self.pending = []
        self.focused, self.all_selected = False, False
        self.draft, self.messages, self.actions = '', [], []
        self.dumps = self.typed = self.submissions = 0
        self.acknowledge = acknowledge

    def later(self, fn): self.pending.append((self.clock.now + self.delay, fn))
    def update(self):
        for at, fn in list(self.pending):
            if at <= self.clock.now:
                self.pending.remove((at, fn)); fn()

    def current_activity(self):
        self.clock.sleep(.02)
        return 'com.kakao.talk/.activity.main.MainActivity'

    def tap(self, x, y):
        self.clock.sleep(.03); self.actions.append(x); self.update()
        if x in (11, 21, 31, 41):
            dest = {11:'chats', 21:'friends', 31:'profile', 41:'chat'}[x]
            self.later(lambda: setattr(self, 'page', dest))
        elif x == 71: self.later(lambda: setattr(self, 'all_selected', True))
        elif x == 51: self.later(lambda: setattr(self, 'focused', True))
        elif x == 61:
            assert self.focused and self.draft, 'must wait for focus and committed text'
            self.submissions += 1
            if self.acknowledge:
                text = self.draft
                def ack(): self.messages.append(text); self.draft = ''
                self.later(ack)
        else: raise AssertionError('unexpected synthetic tap')

    def type_text(self, text):
        self.clock.sleep(.06); self.update()
        assert self.focused, 'typing before focus is ready'
        self.typed += 1
        self.later(lambda: setattr(self, 'draft', self.draft + text))

    def dump(self):
        self.clock.sleep(.06); self.dumps += 1; self.update()
        parts = [node('tab', 11, desc='Chats tab', selected=str(self.page == 'chats').lower()),
                 node('tab', 21, desc='Friends tab', selected=str(self.page == 'friends').lower())]
        if self.page == 'friends': parts += [node('avatar',31,desc='Profile photo')]
        if self.page == 'profile': parts += [node('memo',41,text='My Chatroom',desc='My Chatroom')]
        if self.page == 'chats':
            parts += [node('tab_chip',71,text='All',selected=str(self.all_selected).lower()),
                      node('recycler_view',81)]
        if self.page == 'chat':
            parts += [node('message_edit_text',51,text=self.draft,focused=str(self.focused).lower()),
                      node('send_layout',61,enabled=str(bool(self.draft)).lower())]
            parts += ['<node resource-id="'+P+'chat_log_recycler_list">']
            parts += ['<node>'+node('message',91,text=m)+'</node>' for m in self.messages]
            parts += ['</node>']
        return '<?xml version="1.0"?><hierarchy>' + ''.join(parts) + '</hierarchy>'

    def shell(self, command): raise AssertionError('model must never run a device command')
    def back(self): raise AssertionError('unexpected navigation recovery')
