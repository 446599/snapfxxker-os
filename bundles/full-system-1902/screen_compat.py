"""Small 1.90.2 compatibility fixes for the released 233 screen."""
import re


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("screen compatibility anchor mismatch: " + old[:100])
    return text.replace(old, new, 1)


def patch_engine(text):
    # The explicit load chooses its ACE before T<n> A0. The activation
    # callback may still see an OLD head_source and change the active ACE
    # or queue feed-assist on that route. During an explicit load, let the
    # load's own feed path own both selection and assist.
    start = text.index('    def _on_extruder_change(self):')
    end = text.index('\n    def ', start + 1)
    block = text[start:end]
    block = replace_once(block,
        '''        self._apply_spool_pa(head_index, why='toolchange')

        source = self._head_source.get(head_index)
''',
        '''        self._apply_spool_pa(head_index, why='toolchange')

        if head_index in self._loading_heads:
            self._fa_trace('_on_extruder_change: explicit load owns head %d; skip old route' % head_index)
            return

        source = self._head_source.get(head_index)
''')
    block = replace_once(block,
        '''                try:
                    self._arm_fa_for(target_ace_local, target_slot_local)
''',
        '''                current = self._head_source.get(head_index_local)
                if (head_index_local in self._loading_heads
                        or not current or current.get('load_failed')
                        or not self.head_uses_ace(head_index_local)
                        or (current.get('ace_index'), current.get('slot')) !=
                           (target_ace_local, target_slot_local)
                        or not self._connected_per_ace.get(target_ace_local, False)):
                    self._fa_trace(
                        '_on_extruder_change deferred start SUPPRESSED (stale route): '
                        'head=%d idx=%d slot=%d'
                        % (head_index_local, target_ace_local, target_slot_local))
                    return self.reactor.NEVER
                try:
                    self._arm_fa_for(target_ace_local, target_slot_local)
''')
    text = text[:start] + block + text[end:]

    start = text.index('    def _cmd_ACE_LOAD_HEAD_body(self, gcmd):')
    end = text.index('\n    cmd_ACE_UNLOAD_HEAD_help', start)
    block = text[start:end]
    block = replace_once(block,
        '''        active_ext = self.toolhead.get_extruder().get_name()
''',
        '''        # Log the command's actual selection, not a display's one-based label.
        logging.info('[multiACE] LOAD_ROUTE head=%d ace=%d slot=%d requested_ace=%s requested_slot=%s',
                     head, ace_index, slot, gcmd.get('ACE', None), gcmd.get('SLOT', None))
        active_ext = self.toolhead.get_extruder().get_name()
''')
    block = replace_once(block,
        '''            self.gcode.run_script_from_command('T%d A0' % head)
            self.toolhead.wait_moves()

        module, channel = self.EXTRUDER_MAP[head]
''',
        '''            self.gcode.run_script_from_command('T%d A0' % head)
            self.toolhead.wait_moves()

        # Recheck after tool selection/reactor yields and before stamping
        # the source or issuing FEED_AUTO. Never feed the old active unit.
        if self.head_uses_ace(head) and self._active_device_index != ace_index:
            if not self._switch_ace_for_head_target(ace_index):
                self._last_load_ok = False
                raise self._ace_error(gcmd, 'Failed to connect to MMU %d' % self._disp(ace_index),
                                      code=208, head=head)

        module, channel = self.EXTRUDER_MAP[head]
''')
    text = text[:start] + block + text[end:]
    compile(text, 'ace.py', 'exec')
    return text


def patch_web(text):
    # The 233 screen posts /api/slot-override/sync and commits its local
    # lane edit only on HTTP 200. 1.90.2 ships only /api/slot-override.
    # Reuse the canonical override store, including its normal lifecycle;
    # do not send any motor command or write a second store.
    start = text.index('def _save_overrides_to_disk() -> None:')
    end = text.index('\ndef _drop_override_if_present(', start)
    block = text[start:end]
    block = replace_once(block, 'def _save_overrides_to_disk() -> None:',
                         'def _save_overrides_to_disk() -> bool:')
    block = replace_once(block,
        '''    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        tmp = p.with_suffix(p.suffix + ".tmp")
''',
        '''    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
''')
    block = replace_once(block,
        '''    except Exception:
        pass
''',
        '''        return True
    except Exception:
        _trace.exception("slot override save failed")
        return False
''')
    text = text[:start] + block + text[end:]

    start = text.index('@app.post("/api/slot-override")')
    end = text.index('@app.delete("/api/slot-override/{ace}/{slot}")', start)
    block = text[start:end]
    block = replace_once(block,
        '''async def set_slot_override(req: SlotOverride) -> dict:
    key = _override_key(req.ace, req.slot)
''',
        '''async def set_slot_override(req: SlotOverride) -> dict:
    _reload_overrides_if_changed()
    key = _override_key(req.ace, req.slot)
''')
    block = replace_once(block,
        '''    _kept_slot_emptied.discard((req.ace, req.slot))     # a fresh label
    _trace.info("override SET via picker POST MMU %d / slot %d: %s -> %s",
                req.ace, req.slot, old, new)
    _save_overrides_to_disk()
''',
        '''    if not _save_overrides_to_disk():
        if old is None:
            _slot_overrides.pop(key, None)
        else:
            _slot_overrides[key] = old
        raise HTTPException(500, "Unable to save slot override")
    _kept_slot_emptied.discard((req.ace, req.slot))     # a fresh label
    _trace.info("override SET via picker POST MMU %d / slot %d: %s -> %s",
                req.ace, req.slot, old, new)
''')
    block += '''@app.post("/api/slot-override/sync")
async def sync_slot_override(req: SlotOverride) -> dict:
    """Compatibility with the 233 native screen's bay editor."""
    if not (0 <= req.ace < 4 and 0 <= req.slot < 4):
        raise HTTPException(422, "ACE and slot must be zero-based 0..3")
    if req.color and not re.fullmatch(r"#[0-9A-Fa-f]{6}", req.color):
        raise HTTPException(422, "color must be #RRGGBB")
    _reload_overrides_if_changed()
    # The native editor sends material and color, not the other fields.
    # Keep previously declared brand/subtype instead of erasing them.
    old = _slot_overrides.get(_override_key(req.ace, req.slot), {})
    values = req.model_dump(exclude_unset=True)
    for field in ("material", "color", "brand", "subtype"):
        if field not in values:
            values[field] = old.get(field, "")
    return await set_slot_override(SlotOverride(**values))


'''
    text = text[:start] + block + text[end:]
    compile(text, 'web/backend/main.py', 'exec')
    return text
