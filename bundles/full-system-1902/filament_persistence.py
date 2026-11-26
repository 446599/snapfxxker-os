"""Manual filament persistence/readback only; no ACE2K or RFID motion changes."""
from screen_compat import replace_once


def patch_persistence_engine(text):
    # Changing a colour in an existing key must publish just like adding a key.
    start = text.index('    def _refresh_slot_overrides_if_changed(self):')
    end = text.index('\n    def ', start + 1)
    block = text[start:end]
    block = replace_once(block,
        '            old_keys = set(self._slot_overrides.keys())\n', '')
    block = replace_once(block,
        '            new_keys = set(self._slot_overrides.keys())\n            if old_keys != new_keys:',
        '            if _prev != self._slot_overrides:')
    text = text[:start] + block + text[end:]

    # The released 233 screen subscribes to ace.aces[].slots, not /api/state.
    # Apply the saved identity only to the serialized status, never to the
    # protocol cache, gate, UID, tag state, routing or motion.
    start = text.index('    def get_status(self, eventtime=None):')
    end = text.index('            protocol = self._protocols.get(i)', start)
    block = text[start:end]
    block = replace_once(block,
        '                })\n',
        '                })\n                self._publish_manual_slot_identity(i, slots_out[-1])\n')
    helper = '''    def _publish_manual_slot_identity(self, idx, slot):
        override = self._override_for(idx, slot['index'])
        if override is None:
            return
        # Preserve the unmodified device identity for the web parser and
        # other readers. Never claim a tag was read because a user edited it.
        slot['raw_tag_identity'] = {k: slot[k] for k in
                                    ('material', 'brand', 'subtype', 'color')}
        if override.get('material'):
            slot['material'] = override['material']
        for field in ('brand', 'subtype'):
            if field in override:
                slot[field] = override[field] or ''
        color = (override.get('color') or '').strip().lstrip('#')
        if len(color) == 6 and all(c in '0123456789abcdefABCDEF' for c in color):
            slot['color'] = [int(color[n:n+2], 16) for n in (0, 2, 4)]

'''
    text = text[:start] + helper + block + text[end:]
    compile(text, 'ace.py', 'exec')
    return text


def patch_persistence_web(text):
    # The screen needs resolved labels in ace.aces[].slots; the web already
    # resolves its own labels, so continue using the original device data.
    text = replace_once(text,
        '            sd = slots_by_idx.get(s, {}) or {}\n',
        '''            sd = slots_by_idx.get(s, {}) or {}
            raw_identity = sd.get("raw_tag_identity")
            if isinstance(raw_identity, dict):
                sd = dict(sd, **raw_identity)
''')
    # A zero-valued default/stale gate after boot, reconnect, or when a bay
    # was already empty is not evidence that a user just removed a spool.
    start = text.index('            if gate == 0:\n                _now = time.time()')
    end = text.index('            rfid_status = sd.get("rfid", 0)', start)
    text = text[:start] + '''            if _confirmed_slot_eject(i, s, gate, bool(a.get("connected")),
                                     str(a.get("unit_key") or "")):
                if not remember_filament and _drop_override_if_present(i, s):
                    overrides_dirty = True
                if remember_filament:
                    _kept_slot_emptied.add((i, s))
''' + text[end:]
    anchor = 'EJECT_DEBOUNCE_S = 0.5\n_eject_pending_since: dict[tuple[int, int], float] = {}\n'
    text = replace_once(text, anchor, anchor + '''

# Only a connected unit's observed present->empty edge can start an eject.
# Keep the state in the web process; boot/reconnect is not an eject.
_last_eject_gate: dict[tuple[int, int], tuple[str, int]] = {}


def _confirmed_slot_eject(ace, slot, gate, connected, unit_key):
    key = (ace, slot)
    if not connected or gate not in (0, 1):
        _last_eject_gate.pop(key, None)
        _eject_pending_since.pop(key, None)
        return False
    previous = _last_eject_gate.get(key)
    _last_eject_gate[key] = (unit_key, gate)
    if previous is None or previous[0] != unit_key:
        _eject_pending_since.pop(key, None)
        return False
    if gate == 1:
        _eject_pending_since.pop(key, None)
        return False
    now = time.monotonic()
    if previous[1] == 1:
        _eject_pending_since[key] = now
        return False
    since = _eject_pending_since.get(key)
    if since is not None and now - since >= EJECT_DEBOUNCE_S:
        _eject_pending_since.pop(key, None)
        return True
    return False
''')
    # A fresh manual edit supersedes a previously queued eject.
    text = replace_once(text,
        '    _kept_slot_emptied.discard((req.ace, req.slot))     # a fresh label\n',
        '''    _eject_pending_since.pop((req.ace, req.slot), None)
    _kept_slot_emptied.discard((req.ace, req.slot))     # a fresh label
''')
    text = replace_once(text,
        'async def list_slot_overrides() -> dict:\n    return {"overrides": _slot_overrides}',
        'async def list_slot_overrides() -> dict:\n    _reload_overrides_if_changed()\n    return {"overrides": _slot_overrides}')
    # Roll back an automatic removal in RAM if the disk refused the write.
    text = replace_once(text, '    overrides_dirty = False\n',
        '    overrides_dirty = False\n    overrides_before = dict(_slot_overrides)\n')
    text = replace_once(text,
        '    if overrides_dirty:\n        _save_overrides_to_disk()\n',
        '''    if overrides_dirty and not _save_overrides_to_disk():
        _slot_overrides.clear()
        _slot_overrides.update(overrides_before)
''')
    compile(text, 'web/backend/main.py', 'exec')
    return text
