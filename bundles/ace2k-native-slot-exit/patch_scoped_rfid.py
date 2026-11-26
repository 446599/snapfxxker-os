"""Scope native host RFID tasks by actual reader, preserve legacy arbitration."""
from patch_scoped_runtime import method, replace

HELPERS = '''    def _native_tag_jobs(self):
        jobs = getattr(self, '_native_tag_tasks', None)
        if jobs is None:
            jobs = self._native_tag_tasks = {}
        return jobs

    def _insert_tag_busy(self, idx, slot):
        if self._cap(idx, 'host_tag_session'):
            return (idx, slot // 2) in self._native_tag_jobs()
        return bool(getattr(self, '_tag_read_busy', False))

    def _claim_native_tag(self, idx, slot, kind):
        key = (idx, slot // 2)
        jobs = self._native_tag_jobs()
        if key in jobs:
            return None
        seq = int(getattr(self, '_tag_op_seq', 0)) + 1
        self._tag_op_seq = seq
        self._tag_op_kind = kind
        self._tag_op_result = None
        jobs[key] = {'ace': idx, 'slot': slot, 'seq': seq, 'kind': kind}
        return seq

    def _finish_native_tag(self, idx, slot, seq, result=None):
        jobs = self._native_tag_jobs()
        key = (idx, slot // 2)
        if (jobs.get(key) or {}).get('seq') != seq:
            return
        del jobs[key]
        if result is not None and self._tag_op_seq == seq:
            self._tag_op_result = result
        self._insert_drain_queue()

    def _native_tag_status(self):
        return [dict(v) for _, v in sorted(self._native_tag_jobs().items())]

'''

def patch_engine(text):
    text = replace(text, "    def _insert_uid_read(self, idx, slot, depth='auto'):\n", HELPERS + "    def _insert_uid_read(self, idx, slot, depth='auto'):\n")
    def insert(s):
        s=replace(s,"        if getattr(self, '_tag_read_busy', False):\n", "        if self._insert_tag_busy(idx, slot):\n")
        s=replace(s,"            setattr(self, '_tag_read_busy', True)\n            self._tag_op_kind = 'insert'\n", "            seq = self._claim_native_tag(idx, slot, 'insert')\n            if seq is None:\n                return\n")
        return replace(s,"                setattr(self, '_tag_read_busy', False)\n                self._insert_drain_queue()\n", "                self._finish_native_tag(idx, slot, seq)\n")
    text=method(text,'_insert_uid_read',insert)
    def drain(s):
        start=s.index('        while q:\n')
        end=s.index("        logging.info('[multiACE] [insert-read] draining queue:",start)
        s=s[:start]+'''        selected = None
        for entry in list(q):
            idx, slot, depth = entry
            why = self._insert_queue_stale_reason(idx, slot)
            if why is not None:
                q.remove(entry)
                logging.info('[multiACE] dropping queued RFID read MMU %d slot %d: %s' % (idx, slot, why))
            elif not self._insert_tag_busy(idx, slot):
                q.remove(entry)
                selected = entry
                break
        if selected is None:
            return
        idx, slot, depth = selected
'''+s[end:]
        return s
    text=method(text,'_insert_drain_queue',drain)
    def manual(s):
        start=s.index("        if getattr(self, '_tag_read_busy', False):\n")
        end=s.index('        def _run(eventtime):',start)
        s=s[:start]+'''        _seq = self._claim_native_tag(ace_idx, slot, 'read')
        if _seq is None:
            self.log_warn(self._t('msg.tag_read_busy'))
            return

'''+s[end:]
        return replace(s,"                setattr(self, '_tag_read_busy', False)\n                self._tag_op_result = {\n                    'ok': bool(ok), 'kind': 'read', 'seq': _seq,\n                    'msg': (msg or 'no tag read')[:200]}\n", "                self._finish_native_tag(ace_idx, slot, _seq, {\n                    'ok': bool(ok), 'kind': 'read', 'seq': _seq,\n                    'msg': (msg or 'no tag read')[:200]})\n")
    text=method(text,'_cmd_ace_tag_read_host',manual)
    text=replace(text,"'tag_op': {'busy': bool(getattr(self, '_tag_read_busy', False)\n                               or getattr(self, '_gen1_manual_busy', False)),", "'tag_op': {'busy': bool(getattr(self, '_tag_read_busy', False)\n                               or getattr(self, '_gen1_manual_busy', False)\n                               or self._native_tag_jobs()),\n                       'tasks': self._native_tag_status(),")
    compile(text,'ace.py','exec')
    return text
