"""Attach automatic ACE parking at the actual 1902.16 motion boundaries."""


def replace_checked(source, old, new, count=1):
    if source.count(old) != count:
        raise ValueError('ACE thermal parking patch anchor mismatch: ' + old[:100])
    return source.replace(old, new)


def patch_engine(source):
    start = source.index('    def cmd_ACE_LOAD_HEAD(self, gcmd):')
    end = source.index('\n    cmd_ACE_TEST_help', start)
    body = source[start:end]
    anchor = '        module, channel = self.EXTRUDER_MAP[head]\n'
    load, unload = body.split('    def cmd_ACE_UNLOAD_HEAD(self, gcmd):', 1)
    load = replace_checked(load, anchor, anchor + '''        park_reference = self._material_batch.capture_load_park(
            head, ace_index, slot, _staged)
''')
    anchor = '        self._in_internal_load_head = True\n'
    load = replace_checked(load, anchor, anchor + '''        self._material_batch.pending_load_park = park_reference
''')
    anchor = '            self._in_internal_load_head = False\n'
    load = replace_checked(load, anchor, anchor + '''            park_reference = self._material_batch.pending_load_park
            self._material_batch.pending_load_park = None
''')
    anchor = "        self._audit_state('LOAD_HEAD', {'head': head, 'ace': ace_index, 'slot': slot})\n"
    load = replace_checked(load, anchor, anchor + '''        self._material_batch.record_load_park(head, park_reference)
''')
    anchor = "                getattr(self, '_unload_slot_hint', {}).pop(head, None)\n"
    unload = replace_checked(unload, anchor, anchor + '''                self._material_batch.thermal_return = None
''')
    anchor = '        self.wait_ace_ready()\n\n        module, channel = self.EXTRUDER_MAP[head]\n'
    unload = replace_checked(unload, anchor, '''        self.wait_ace_ready()

        if park_selection is not None and park_selection.route == 'encoder_unload':
            try:
                self._material_batch.start_thermal_return(park_selection)
            except (ValueError, RuntimeError, OSError) as exc:
                raise gcmd.error(str(exc)) from exc

        module, channel = self.EXTRUDER_MAP[head]
''')
    unload = replace_checked(unload, '''                self._material_batch._fresh_park_sample(
                    park_selection, self.reactor.monotonic())
''', '''                if park_selection.route == 'encoder_unload':
                    self._material_batch.verify_thermal_return()
                else:
                    self._material_batch._fresh_park_sample(
                        park_selection, self.reactor.monotonic())
''')
    source = source[:start] + load + '    def cmd_ACE_UNLOAD_HEAD(self, gcmd):' + unload + source[end:]
    compile(source, 'ace.py', 'exec')
    return source


def patch_feed(source):
    anchor = '                        self.gcode.run_script_from_command(_flush_cmd + "\\r\\n")\n'
    source = replace_checked(source, anchor, '''                        if self.ace is not None:
                            self.ace._material_batch.mark_load_seated(self.filament_ch[ch])
''' + anchor)
    anchor = '                                _rest = _full_retract - _short_retract\n'
    source = replace_checked(source, anchor, anchor + '''                                if self.ace._material_batch.thermal_return is not None:
                                    try:
                                        self.ace._material_batch.thermal_retract(
                                            head_idx, retract_ace, _ace_slot, _retract_speed)
                                    except (ValueError, RuntimeError, OSError) as exc:
                                        raise self.printer.command_error(str(exc)) from exc
                                    _rest = 0
''', count=2)
    source = replace_checked(source,
        '                            elif self.ace._head_source.get(head_idx) is not None:\n',
        '''                            elif (self.ace._material_batch.thermal_return is None
                                  and self.ace._head_source.get(head_idx) is not None):
''', count=2)
    anchor = '                logging.error("[feed][unload] channel[%d]: auto unload error: %s", channel, raw_msg)\n'
    source = replace_checked(source, anchor, anchor + '''                if (self.ace is not None and self.ace._material_batch.thermal_exit_confirmed(
                        self.filament_ch[channel])):
                    self.channel_error[channel] = FEED_OK
                    self.channel_error_state[channel] = FEED_STA_NONE
                    self.channel_action_state[channel] = FEED_STA_NONE
                    self._set_channel_state(channel, FEED_STA_WAIT_INSERT, True)
                    raise gcmd.error(message=raw_msg, action='none', oneshot=1, level=1) from e
''')
    compile(source, 'filament_feed_ace.py', 'exec')
    return source
