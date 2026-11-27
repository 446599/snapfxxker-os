"""Use the existing USB probes to rediscover previously connected ACEs."""
from hub_port_order import replace_once as replace


def patch_engine(engine):
    engine = replace(
        engine,
        '    def _probe_claim(self, protocol_cls, path, probe_budget):',
        '    def _probe_claim(self, protocol_cls, path, probe_budget, fresh=False):',
    )
    engine = replace(
        engine,
        "        if (getattr(protocol_cls, 'PROBE_ONCE_PER_PATH', False)\n",
        "        if (not fresh and getattr(protocol_cls, 'PROBE_ONCE_PER_PATH', False)\n",
    )
    engine = replace(
        engine,
        '        cached = self._cfs_probe_cache.get(real_path)\n',
        '        cached = None if fresh else self._cfs_probe_cache.get(real_path)\n',
    )
    engine = replace(
        engine,
        """        category = result.get('category', '?')
        prev = self._cfs_probe_cache.get(real_path)
""",
        """        if fresh:
            # A recovery scan needs a reply today, but only the normal
            # connection handshake can commit an identity for this socket.
            return verified
        category = result.get('category', '?')
        prev = self._cfs_probe_cache.get(real_path)
""",
    )
    engine = replace(
        engine,
        """        if self._reconnecting_per_ace.get(idx, False):
            return
        self._reconnecting_per_ace[idx] = True
        try:
            logging.info('[multiACE] MMU %d comms lost""",
        """        if self._reconnecting_per_ace.get(idx, False):
            return
        if not self._is_actively_printing() or not self._comms_loss_feeds(idx):
            # A spare or idle unit can wait for discovery. Close its old
            # transport so the scan can acquire the port exclusively.
            if self._serial_failed_per_ace.get(idx) and idx in self._serials:
                self._disconnect_from(idx)
            self._handle_per_ace_failure(idx, err)
            return
        self._reconnecting_per_ace[idx] = True
        try:
            logging.info('[multiACE] MMU %d comms lost""",
    )
    engine = replace(
        engine,
        '''    IDLE_RECONNECT_INTERVAL = 5.0

    def _schedule_idle_reconnect(self, idx):
        """Keep looking for a unit the reconnect ladder gave up on. The
        ladder spans ~3 s, which covers a USB blip but not a power cycle
        (a unit takes longer to enumerate again). Every
        IDLE_RECONNECT_INTERVAL the device path is checked; once it exists
        the unit is reopened through the same path as the ladder. Stops on
        success, when something else reconnected the unit, or when it is
        held for a firmware update."""
''',
        '''    IDLE_RECONNECT_INTERVAL = 30.0

    def _can_scan_offline_aces(self, eventtime):
        if (self.printer.get_state_message()[1] != 'ready'
                or self._homing_active
                or not self._is_printer_idle_for_generic_probe()):
            return False
        print_time, estimated_time, queue_empty = self.toolhead.check_busy(eventtime)
        return queue_empty and print_time <= estimated_time

    def _schedule_idle_reconnect(self, idx):
        """Scan a known offline socket while idle, opening only a responder."""
''',
    )
    engine = replace(
        engine,
        """            if (not self._serial_failed_per_ace.get(idx, False)
                    or idx in getattr(self, '_fw_update_hold', ())):
""",
        """            if (not self._serial_failed_per_ace.get(idx, False)
                    or idx in self._hub_identity_changed):
""",
    )
    engine = replace(
        engine,
        """            if not os.path.exists(path):
                return nxt
            self._reconnecting_per_ace[idx] = True
            try:
                ok = self._open_ace_verified(
""",
        """            if (idx in self._fw_update_hold or not os.path.exists(path)
                    or not self._can_scan_offline_aces(eventtime)):
                return nxt
            self._reconnecting_per_ace[idx] = True
            try:
                protocol = self._ace_path_protocol.get(path, type(self._protocols.get(idx)))
                if callable(getattr(protocol, 'probe_device', None)):
                    if not self._probe_claim(protocol, path, [1], fresh=True):
                        return self.reactor.monotonic() + self.IDLE_RECONNECT_INTERVAL
                    # The worker yields the reactor while it reads. A print,
                    # homing or firmware update can have started meanwhile.
                    if (idx >= len(self._ace_devices) or self._ace_devices[idx] != path
                            or idx in self._fw_update_hold
                            or not self._serial_failed_per_ace.get(idx, False)
                            or not self._can_scan_offline_aces(self.reactor.monotonic())):
                        return nxt
                # V1 enumerates as a device, and has no read-only probe.
                ok = self._open_ace_verified(
""",
    )
    engine = replace(
        engine,
        """            logging.info('[multiACE] MMU %d unreachable - retrying every '
                         '%.0f s in the background'
""",
        """            logging.info('[multiACE] MMU %d offline - scan every '
                         '%.0f s while idle'
""",
    )
    engine = replace(
        engine,
        """        for idx in list(self._serial_failed_per_ace.keys()):
            if not self._serial_failed_per_ace.get(idx, False):
                continue
            try:
                # FA re-arm is hooked to the post-handshake on_ready (the
                # immediate _arm_fa_for later in _on_print_start races the
                # reopen handshake and the device drops it).
                ok = self._open_ace(
                    idx, on_ready=lambda i=idx: self._rearm_fa_after_reconnect(i))
            except Exception as e:
                ok = False
                logging.info('[multiACE] resume reopen MMU %d raised: %s'
                             % (idx, str(e)))
""",
        """        for idx in list(self._serial_failed_per_ace.keys()):
            feeds = self._comms_loss_feeds(idx)
            if not self._serial_failed_per_ace.get(idx, False) or not feeds:
                continue
            ok = False
            if (not self._reconnecting_per_ace.get(idx, False)
                    and idx < len(self._ace_devices)
                    and os.path.exists(self._ace_devices[idx])):
                self._reconnecting_per_ace[idx] = True
                try:
                    ok = self._open_ace(
                        idx, on_ready=lambda i=idx: self._rearm_fa_after_reconnect(i))
                except Exception as e:
                    logging.info('[multiACE] resume reopen MMU %d raised: %s', idx, e)
                finally:
                    self._reconnecting_per_ace[idx] = False
""",
    )
    engine = replace(
        engine,
        """            if not ok:
                self.log_error(self._t('msg.open_ace_failed_attempts',""",
        """            if not ok:
                self._serial_failed_per_ace[idx] = True
                self._schedule_idle_reconnect(idx)
                self.log_error(self._t('msg.open_ace_failed_attempts',""",
    )
    engine = replace(
        engine,
        """        try:
            ps = self.printer.lookup_object('print_stats', None)
            st = (ps.get_status(eventtime).get('state', '')
                  if ps is not None else '')
            if st in ('printing', 'paused'):
                return eventtime + IDLE_RESCAN_S
        except Exception:
            pass
""",
        """        if not self._can_scan_offline_aces(eventtime):
            return eventtime + IDLE_RESCAN_S
""",
    )
    compile(engine, 'ace.py', 'exec')
    return engine
