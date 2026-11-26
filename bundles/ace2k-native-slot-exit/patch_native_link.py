"""Apply the native guard to the exact 1.90.2 production driver for offline tests.

Does not alter compose.py, expose a G-code, or produce a firmware image.
"""
from pathlib import Path

HERE = Path(__file__).resolve().parent


def replace(text, old, new):
    if text.count(old) != 1:
        raise ValueError('native driver anchor mismatch: ' + old[:90])
    return text.replace(old, new, 1)


def patch_driver(text):
    text = replace(text, 'from .ace_protocol import AceProtocol, native_sample\n',
                   'from .ace_protocol import AceProtocol, native_sample\nfrom .native_link_guard import NativeExitLinkGuard\n')
    text = replace(text, '        self._io = threading.RLock()\n',
                   '        self._io = threading.RLock()\n        self._native_exit = NativeExitLinkGuard(self)\n')
    text = replace(text, '        elif name == \'ace2k_feed_event\':\n',
                   '        elif name == \'ace2k_feed_event\':\n            if self._native_exit.observe(vals, now):\n                return\n')
    text = replace(text, '    def _call(self, name, response, **values):\n',
                   '''    def _call(self, name, response, **values):
        if (name in ('ace2k_feed_start', 'ace2k_feed_stop_when',
                     'ace2k_feed_speed_set', 'ace2k_rfid_read')
                and self._native_exit.blocked(values.get('lane'))):
            return {'lane': values['lane'], 'accepted': 0, 'reason': 1}
''')
    text = replace(text, '    def _send(self, name, **values):\n',
                   '''    def _send(self, name, **values):
        if name == 'ace2k_feed_clear' and self._native_exit.blocked(values.get('lane')):
            return None
''')
    text = replace(text, '        mask = self._in_use\n', '        mask = self._in_use | self._native_exit.mask()\n')
    text = replace(text, '''            self._send('ace2k_rfid_in_use', mask=mask)
            self._in_use_sent = (now, mask)
''', '''            if self._send('ace2k_rfid_in_use', mask=mask) is True:
                self._in_use_sent = (self._now(), mask)
            else:
                self._in_use_sent = (0.0, None)
''')
    text = replace(text, '        if not self.dictionary.has(\'ace2k_feed_start\'):\n',
                   '        if self._native_exit.blocked(lane):\n            return None, \'busy\'\n        if not self.dictionary.has(\'ace2k_feed_start\'):\n')
    text = replace(text, "        piece = min(job['target'] - job['done'], self._move_max_mm())\n",
                   "        if self._native_exit.blocked(job['slot']):\n            return 'busy'\n        piece = min(job['target'] - job['done'], self._move_max_mm())\n")
    # No normal motion can queue a follow or RFID start behind the new hold.
    text = replace(text, "        self._yield_auto(lane)\n        if method == 'start_feed_assist':\n",
                   "        if self._native_exit.blocked(lane):\n            return reply(1, 'FORBIDDEN')\n        self._yield_auto(lane)\n        if method == 'start_feed_assist':\n")
    old = "        if lane in self._tag_reads:\n            return reply(0)\n        self._yield_auto(lane)\n"
    if text.count(old) != 2:
        raise ValueError('native tag guard anchors changed')
    text = text.replace(old, "        if self._native_exit.blocked(lane):\n            return reply(1, 'FORBIDDEN')\n" + old)
    # close/reset invalidate the connection epoch; new transport creates a new guard.
    text = replace(text, '    def close(self):\n', '    def close(self):\n        self._native_exit.invalidate()\n')
    text = replace(text, "        logging.info(self._lp + '%s - sending reset' % why)\n",
                   "        self._native_exit.invalidate()\n        logging.info(self._lp + '%s - sending reset' % why)\n")
    compile(text, 'ace_protocol_ace2k.py', 'exec')
    return text


def stage(extras):
    extras = Path(extras)
    driver = extras / 'ace_protocol_ace2k.py'
    result = patch_driver(driver.read_text())
    (extras / 'native_link_guard.py').write_bytes((HERE / 'native_link_guard.py').read_bytes())
    driver.write_text(result)
