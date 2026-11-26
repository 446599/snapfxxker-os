"""Patch the 1902.8 provider to assign explicit USB sockets to ACE places."""


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('ambiguous or missing provider anchor: ' + old[:100])
    return text.replace(old, new, 1)


METHOD = '''    def _order_hub_ports(self, scan):
        # Each configured socket owns its index even when other sockets are
        # missing. UID continues to identify the device and its saved state.
        paths = self._hub_port_order
        scanned = set(scan)
        previous = list(self._unit_places or [None] * MAX_ACE_UNITS)
        names = {e.get('key'): e.get('name', '') for e in previous if e and e.get('key')}
        assigned = {}
        keys = {}
        unassigned = {}
        canonical = self._ace_canonical or ()
        for i, path in enumerate(paths):
            if path not in scanned:
                continue
            key = self._unit_uid_for_path(path, self._ace_canonical is None or path not in canonical)
            key = key or self._unit_key_for_path(path)
            if key in self._unit_excluded or key in keys:
                unassigned[key] = {'path': path, 'reason': 'removed' if key in self._unit_excluded else 'duplicate',
                                   'kind': self._unit_kind_of_path(path)}
                continue
            assigned[i] = path
            keys[key] = i
        for path in scan:
            if path not in paths:
                key = self._unit_key_for_path(path)
                unassigned[key] = {'path': path, 'reason': 'unconfigured port',
                                   'kind': self._unit_kind_of_path(path)}
        places = [None] * MAX_ACE_UNITS
        ordered = []
        for i in range(MAX_ACE_UNITS):
            if i in assigned:
                path = assigned[i]
                key = next(k for k, place in keys.items() if place == i)
                places[i] = {'key': key, 'name': names.get(key, ''),
                             'kind': self._unit_kind_of_path(path)}
                ordered.append(path)
            else:
                old = previous[i] if i < len(previous) else None
                key = (old or {}).get('key') or ''
                if key and key not in keys and key not in self._unit_excluded:
                    places[i] = old
                    ordered.append(ABSENT_UNIT_PREFIX + key)
                else:
                    ordered.append(FREE_UNIT_PATH)
        if places != self._unit_places:
            self._unit_places = places
            self._save_unit_places('assigned by USB socket')
        self._unit_unassigned = unassigned
        self._last_scan_place_of_path = {path: i for i, path in assigned.items()}
        if self._ace_canonical is None:
            self._unit_place_of_idx = {i: i for i in range(MAX_ACE_UNITS)}
        return ordered

'''


def patch_engine(text):
    text = replace_once(text, "        self._ace_canonical = None\n",
        """        self._ace_canonical = None
        raw_hub_ports = config.get('hub_port_order', '')
        self._hub_port_order = tuple(p.strip() for p in raw_hub_ports.split(',') if p.strip())
        if self._hub_port_order and (len(self._hub_port_order) != MAX_ACE_UNITS
                or len(set(self._hub_port_order)) != MAX_ACE_UNITS
                or any(not p.startswith('/dev/serial/by-path/') for p in self._hub_port_order)):
            raise config.error('[multiACE] hub_port_order needs four distinct /dev/serial/by-path/ ports')
        self._hub_identity_pending = set()
        self._hub_identity_changed = set()
""")
    text = replace_once(text, '    def _order_units(self, scan):\n', METHOD + '    def _order_units(self, scan):\n')
    text = replace_once(text, '        scan = list(scan)\n        # The id probe opens the port:',
                        '        scan = list(scan)\n        if self._hub_port_order:\n            return self._order_hub_ports(scan)\n        # The id probe opens the port:')
    text = replace_once(text, "    def cmd_ACE_UNIT_ASSIGN(self, gcmd):\n",
        """    def cmd_ACE_UNIT_ASSIGN(self, gcmd):
        if self._hub_port_order:
            raise self._ace_error(gcmd, '[multiACE] ACE numbering is fixed by hub_port_order', 200)
""")
    text = replace_once(text, "            ('overrides', getattr(self, '_slot_overrides', {}),\n             getattr(self, '_save_slot_overrides', None)),\n",
        """            ('overrides', getattr(self, '_slot_overrides', {}),
             getattr(self, '_save_slot_overrides', None)),
            ('retract_pending', getattr(self, '_retract_pending', {}),
             getattr(self, '_save_retract_pending', None)),
""")
    text = replace_once(text, "            if name in ('bindings', 'overrides'):\n                a, sl = str(key).split('_', 1)\n",
        "            if name in ('bindings', 'overrides', 'retract_pending'):\n                a, sl = str(key).split('_', 1)\n")
    text = replace_once(text, "        if name in ('bindings', 'overrides'):\n            return '%d_%d' % (idx, sub), payload\n",
        """        if name in ('bindings', 'overrides', 'retract_pending'):
            if name in ('overrides', 'retract_pending') and isinstance(payload, dict):
                payload = dict(payload, ace=idx, slot=sub)
            return '%d_%d' % (idx, sub), payload
""")
    text = replace_once(text, "        serial_path = self._ace_devices[idx]\n        if self._is_unit_sentinel(serial_path):\n",
        """        serial_path = self._ace_devices[idx]
        if self._hub_port_order and idx in self._hub_identity_changed:
            self._connect_error_per_ace[idx] = 'ACE identity changed on hub port; restart Klipper when idle'
            return False
        if self._hub_port_order and getattr(self._ace_path_protocol.get(serial_path), 'NAME', '') == 'ace2k':
            self._hub_identity_pending.add(idx)
        if self._is_unit_sentinel(serial_path):
""")
    text = replace_once(text, "            self._unit_uid_by_path[serial_path] = 'uid:' + uid\n",
        """            new_key = 'uid:' + uid
            old_key = self._unit_uid_by_path.get(serial_path, '')
            if self._hub_port_order and old_key.startswith('uid:') and old_key != new_key:
                self._hub_identity_changed.add(idx)
                self._hub_identity_pending.discard(idx)
                self._disconnect_from(idx)
                self._serial_failed_per_ace[idx] = True
                self._connect_error_per_ace[idx] = 'ACE identity changed on hub port; restart Klipper when idle'
                self.log_error('[multiACE] ACE identity changed on hub port %d; restart Klipper when idle' % (idx + 1))
                return
            self._unit_uid_by_path[serial_path] = new_key
            if self._hub_port_order and not old_key.startswith('uid:'):
                self._order_hub_ports([p for p in self._ace_devices if not self._is_unit_sentinel(p)])
                self._reconcile_unit_state('hub identity established')
            self._hub_identity_pending.discard(idx)
            if self._hub_port_order:
                # A lane-mask report held during the handshake must be sent
                # again on the next status update, not treated as delivered.
                getattr(self, '_lane_report_sent', {}).pop(idx, None)
""")
    text = replace_once(text, "                if ready_state['fired'] or on_ready is None:\n",
        "                if (ready_state['fired'] or on_ready is None\n                        or idx in self._hub_identity_pending or idx in self._hub_identity_changed):\n")
    text = replace_once(text, "            handshake_requests = protocol.initial_handshake_requests() or []\n",
        """            handshake_requests = list(protocol.initial_handshake_requests() or [])
            if self._hub_port_order and self._is_native_ace2k(idx):
                handshake_requests.append({'method': 'discover_device'})
""")
    text = replace_once(text, "            return mark is not None and last is not None and last > mark\n",
        "            return (idx not in self._hub_identity_pending and idx not in self._hub_identity_changed\n                    and mark is not None and last is not None and last > mark)\n")
    text = replace_once(text, "    def send_request_to(self, idx, request, callback):\n",
        """    def send_request_to(self, idx, request, callback):
        if (self._hub_port_order and (idx in self._hub_identity_pending or idx in self._hub_identity_changed)
                and request.get('method') not in ('get_info', 'discover_device', 'get_status',
                                                  'get_feed_info', 'get_filament_info',
                                                  'get_lane_state', 'get_key_state')):
            if callback is not None:
                callback(self=self, response={'code': 1, 'msg': 'ACE identity not confirmed', 'result': {}})
            return
""")
    compile(text, 'ace.py', 'exec')
    return text
