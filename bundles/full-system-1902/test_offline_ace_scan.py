import ast
import logging
import os
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from hub_port_order import patch_engine as patch_hub
from offline_ace_scan import patch_engine
from test_hub_port_order import PORTS, provider_text


def make_engine():
    tree = ast.parse(patch_engine(patch_hub(provider_text())))
    names = {'_can_scan_offline_aces', '_schedule_idle_reconnect',
             '_reopen_failed_aces_on_resume', '_reconnect_or_pause', '_probe_claim'}
    engine = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                  and any(getattr(member, 'name', None) in names for member in node.body))
    engine.bases = []
    engine.keywords = []
    engine.decorator_list = []
    engine.body = [member for member in engine.body if getattr(member, 'name', None) in names
                   or (isinstance(member, ast.Assign)
                       and getattr(member.targets[0], 'id', None) == 'IDLE_RECONNECT_INTERVAL')]
    module = ast.fix_missing_locations(ast.Module(body=[engine], type_ignores=[]))
    env = {'logging': logging, 'os': os, 'threading': threading, 'time': time,
           'AceProtocolV2': SimpleNamespace(serial_port_in_use=lambda path: False)}
    exec(compile(module, '<packaged offline scan>', 'exec'), env)
    return env[engine.name]


class OfflineScanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.Engine = make_engine()

    def setUp(self):
        self.ace = ace = self.Engine()
        self.idx = 3
        ace._ace_devices = PORTS
        ace._ace_path_protocol = {PORTS[3]: SimpleNamespace(probe_device=Mock())}
        ace._protocols = {}
        ace._serials = {}
        ace._serial_failed_per_ace = {3: True}
        ace._reconnecting_per_ace = {}
        ace._idle_reconnect_timers = {}
        ace._hub_identity_changed = set()
        ace._fw_update_hold = set()
        ace._homing_active = False
        ace._is_printer_idle_for_generic_probe = Mock(return_value=True)
        ace._is_actively_printing = Mock(return_value=False)
        ace.printer = SimpleNamespace(get_state_message=Mock(return_value=('', 'ready')))
        ace.toolhead = SimpleNamespace(check_busy=Mock(return_value=(50.0, 50.0, True)))
        ace.reactor = SimpleNamespace(NEVER=float('inf'), monotonic=Mock(return_value=40.0),
                                      register_timer=Mock(return_value='scan timer'),
                                      pause=Mock())
        ace._usb_stats = {'errno5_recovered': 0, 'errno5_unrecovered': 0}
        ace._open_ace_verified = Mock(return_value=True)
        ace._open_ace = Mock(return_value=True)
        ace._probe_claim = Mock(return_value=False)
        ace._handle_per_ace_failure = Mock()
        ace._disconnect_from = Mock()
        ace._comms_loss_feeds = Mock(return_value=[])
        ace._schedule_comms_pause = Mock()
        ace._rearm_fa_after_reconnect = Mock()
        ace._state_log = Mock()
        ace._audit_state = Mock()
        ace._comms_loss_warned = set()
        ace.log_always = Mock()
        ace.log_warn = Mock()
        ace.log_error = Mock()
        ace._raw = ace._disp = lambda idx: idx + 1
        ace._t = Mock(return_value='ACE link status')
        present = patch('os.path.exists', return_value=True)
        self.present = present.start()
        self.addCleanup(present.stop)

    def start_scan(self):
        self.ace._schedule_idle_reconnect(self.idx)
        tick, due = self.ace.reactor.register_timer.call_args.args
        self.assertEqual(due, 70.0)
        self.ace.reactor.monotonic.return_value = due
        return tick, due

    def test_missing_path_is_checked_again_without_open(self):
        tick, due = self.start_scan()
        self.present.return_value = False
        self.assertEqual(tick(due), due + 30.0)
        self.ace._probe_claim.assert_not_called()
        self.ace._open_ace_verified.assert_not_called()

    def test_silent_adapter_only_probes_then_rejoins_on_reply(self):
        tick, due = self.start_scan()
        self.ace._schedule_idle_reconnect(self.idx)
        self.ace.reactor.register_timer.assert_called_once()
        self.assertEqual(tick(due), due + 30.0)
        self.ace._open_ace_verified.assert_not_called()
        protocol = self.ace._ace_path_protocol[PORTS[3]]
        self.ace._probe_claim.assert_called_once_with(protocol, PORTS[3], [1], fresh=True)
        self.assertFalse(self.ace._reconnecting_per_ace[self.idx])
        self.ace._probe_claim.return_value = True
        self.assertEqual(tick(due + 30.0), self.ace.reactor.NEVER)
        self.ace._open_ace_verified.assert_called_once()
        self.assertFalse(self.ace._serial_failed_per_ace[self.idx])
        self.assertNotIn(self.idx, self.ace._idle_reconnect_timers)

    def test_recovery_probe_ignores_cached_uid_and_does_not_overwrite_it(self):
        ace = self.ace
        path = PORTS[self.idx]
        ace._configured_serial_path_set = lambda: set()
        ace._unit_uid_by_path = {path: 'uid:previous'}
        ace._cfs_probe_cache = {os.path.realpath(path): (40.0, True, 'ok', 0)}
        probe = Mock(return_value={'ok': False})
        protocol = SimpleNamespace(NAME='ace2k', PROBE_ONCE_PER_PATH=True,
                                   probe_device=probe, uid_key=lambda uid: 'uid:other')

        with patch.object(threading, 'Thread', side_effect=lambda target, **kw:
                          SimpleNamespace(start=target)):
            self.assertFalse(self.Engine._probe_claim(ace, protocol, path, [1], fresh=True))
            probe.return_value = {'ok': True, 'uid': ('other',)}
            self.assertTrue(self.Engine._probe_claim(ace, protocol, path, [1], fresh=True))
        self.assertEqual(probe.call_count, 2)
        self.assertEqual(ace._unit_uid_by_path[path], 'uid:previous')

    def test_print_homing_and_pending_moves_defer_the_probe(self):
        tick, due = self.start_scan()
        self.ace._is_printer_idle_for_generic_probe.return_value = False
        self.assertEqual(tick(due), due + 30.0)
        self.ace._is_printer_idle_for_generic_probe.return_value = True
        self.ace._homing_active = True
        self.assertEqual(tick(due), due + 30.0)
        self.ace._homing_active = False
        self.ace.toolhead.check_busy.return_value = (51.0, 50.0, True)
        self.assertEqual(tick(due), due + 30.0)
        self.ace.toolhead.check_busy.return_value = (50.0, 50.0, False)
        self.assertEqual(tick(due), due + 30.0)
        self.ace._probe_claim.assert_not_called()

    def test_print_start_during_probe_defers_the_open(self):
        tick, due = self.start_scan()

        def reply_after_print_start(*args, **kwargs):
            self.ace._is_printer_idle_for_generic_probe.return_value = False
            return True

        self.ace._probe_claim.side_effect = reply_after_print_start
        self.assertEqual(tick(due), due + 30.0)
        self.ace._open_ace_verified.assert_not_called()

    def test_resume_opens_only_assigned_devices_and_never_twice(self):
        self.ace._serial_failed_per_ace = {1: True, 2: True, 3: True}
        self.ace._comms_loss_feeds.side_effect = {1: [0], 2: [1], 3: []}.get
        self.ace._reconnecting_per_ace = {2: True}
        self.ace._reopen_failed_aces_on_resume()
        self.ace._open_ace.assert_called_once()
        self.assertEqual(self.ace._open_ace.call_args.args, (1,))
        self.ace._schedule_comms_pause.assert_called_once_with(2, [1])

    def test_spare_link_failure_closes_transport_without_fast_retries(self):
        self.ace._is_actively_printing.return_value = True
        self.ace._serials[self.idx] = object()
        self.ace._reconnect_or_pause(self.idx, 'no reply')
        self.ace._disconnect_from.assert_called_once_with(self.idx)
        self.ace._handle_per_ace_failure.assert_called_once_with(self.idx, 'no reply')
        self.ace._open_ace_verified.assert_not_called()

    def test_active_feeding_loss_keeps_quick_verified_recovery(self):
        self.ace._is_actively_printing.return_value = True
        self.ace._comms_loss_feeds.return_value = [0]
        self.ace._reconnect_or_pause(self.idx, 'link lost')
        self.ace._open_ace_verified.assert_called_once()
        self.ace._handle_per_ace_failure.assert_not_called()
