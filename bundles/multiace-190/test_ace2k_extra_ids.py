"""Regression: a quad-serial adapter shared by V2 and ace2k units.

The adapter exposes one USB id (listed in [ace] v2_extra_usb_ids) for all
of its channels. Before the fix, a channel that was not named in
ace2k_ports was registered as V2 straight from the USB id, without any
protocol handshake, and then sat in COMMS_LOST forever.

Needs MULTIACE_STAGED pointing at a prepared 1.90.2 tree, because the
scenario drives the real _scan_ace_devices through tests/units/harness.
"""
import os
import sys
import unittest
from unittest.mock import patch

STAGED = os.environ.get('MULTIACE_STAGED')
PORT = '/dev/serial/by-path/platform-fed00000.usb-usb-0:1.%d:1.0'
ADAPTER_IDS = ('1a86', '55d5')


@unittest.skipUnless(STAGED and os.path.isdir(STAGED),
                     'MULTIACE_STAGED not set')
class ExtraUsbIdChannelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(STAGED, 'tests'))
        from units import harness, scenarios
        cls.H, cls.S = harness, scenarios

    def _scan(self, kinds):
        H, S = self.H, self.S
        clock = H.VirtualClock()
        units = [H.Unit('c%d' % i, kind, PORT % (i + 1),
                        (S.FakeAce2k if kind == 'ace2k' else S.FakeV2)(
                            clock, 'c%d' % i), ADAPTER_IDS)
                 for i, kind in enumerate(kinds)]
        cfg = {'ace_device_count': str(len(units)),
               'v2_extra_usb_ids': '1a86:55d5',
               'ace2k_ports': PORT % 1}
        h = H.Harness(units, config_overrides=cfg)
        h.build()          # build() installs the stock stubs; ours go after
        known = {}

        def discover_v2(cls):
            # The real _scan_v2_serial_paths selects by USB id, extra ids
            # included; the stock stub selects by unit kind instead.
            return [u.path for u in h.units
                    if u.plugged and tuple(u.usb_ids) == ADAPTER_IDS]

        def is_official(cls, path):
            # Real rule: 1a86:55d3 only. The stock stub accepts any v2 or
            # ace2k unit, which would hide the extra-id branch.
            u = h.by_path.get(path)
            return bool(u and tuple(u.usb_ids) == ('1a86', '55d3'))

        def probe_v2(cls, path, query_timeout=0.35):
            u = h.by_path.get(path)
            if u and u.kind == 'v2' and u.plugged:
                return {'ok': True, 'category': 'verified',
                        'uid': u.device.uid}
            return {'ok': False, 'category': 'no_response',
                    'error': 'silent'}

        def identify(self, path, reactor, budget, v2, ace2k):
            # Synchronous stand-in: the real worker thread cannot finish
            # inside the harness' virtual clock. A positive hit is served
            # from cache without spending budget, as the real one does.
            if path in known:
                return known[path]
            if budget[0] <= 0:
                return None
            budget[0] -= 1
            u = h.by_path.get(path)
            if u and u.kind == 'ace2k' and u.plugged:
                known[path] = (ace2k, {'ok': True, 'category': 'verified',
                                       'uid': u.device.uid})
                return known[path]
            return None

        with patch.object(h.P2.AceProtocolV2, 'discover',
                          classmethod(discover_v2)), \
                patch.object(h.P2.AceProtocolV2, 'is_official_usb_path',
                             classmethod(is_official)), \
                patch.object(h.P2.AceProtocolV2, 'serial_port_in_use',
                             staticmethod(lambda p: False)), \
                patch.object(h.P2.AceProtocolV2, 'probe_device',
                             classmethod(probe_v2)), \
                patch.object(h.A.SharedAce2Discovery, 'identify', identify):
            # Drive the scan directly: the reconnect ladder livelocks
            # under the virtual clock once a port stays silent.
            for _ in range(6):
                h.inst._scan_ace_devices('startup')
        return h

    def test_undeclared_channels_are_not_claimed_as_v2(self):
        h = self._scan(['ace2k'] * 4)
        chosen = {p.rsplit('-0:', 1)[-1]: getattr(c, 'NAME', c)
                  for p, c in h.inst._ace_path_protocol.items()}
        self.assertEqual(chosen, {'1.1:1.0': 'ace2k', '1.2:1.0': 'ace2k',
                                  '1.3:1.0': 'ace2k', '1.4:1.0': 'ace2k'})

    def test_each_channel_is_bound_to_a_verified_uid(self):
        # Claiming without a real identity is what made the bug silent.
        h = self._scan(['ace2k'] * 4)
        self.assertEqual(len(h.inst._unit_uid_by_path), 4)

    def test_stale_port_place_is_released_for_its_uid(self):
        # Migration from a hand-written ace2k_ports list: the table still
        # holds 'absent:port:X' places for sockets that now report a uid.
        # They must not keep the verified units out of a full table.
        h = self._scan(['ace2k'] * 4)
        inst = h.inst
        paths = [PORT % i for i in (1, 2, 3, 4)]
        socket = [os.path.basename(p) for p in paths]
        inst._unit_places = [
            {'key': 'port:' + socket[1], 'name': 'B', 'kind': 'ace2k'},
            {'key': 'port:' + socket[2], 'name': 'C', 'kind': 'ace2k'},
            {'key': 'port:' + socket[3], 'name': 'D', 'kind': 'ace2k'},
            {'key': 'uid:1', 'name': 'A', 'kind': 'ace2k'},
        ]
        inst._unit_excluded = set()
        inst._unit_uid_for_path = lambda p, probe=None: 'uid:%d' % (
            paths.index(p) + 1)
        ordered = inst._order_units(paths)
        self.assertEqual(len(ordered), 4)
        self.assertEqual([p for p in ordered if p.startswith('absent:')], [])
        self.assertTrue(all(e['key'].startswith('uid:')
                            for e in inst._unit_places))


if __name__ == '__main__':
    unittest.main()
