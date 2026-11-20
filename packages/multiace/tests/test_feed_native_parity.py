"""Exercise the real feed entry points with printer peripherals replaced.

Stock reference: Snapmaker/u1-klipper@7ac453b50fe6779b2ec0b759038a17da5e6d671d
klippy/extras/filament_feed.py. No motors or heaters are accessed by these tests.
"""
import importlib
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
EXTRAS = Path(os.environ.get('U1_ACE_RUNTIME', ROOT / 'runtime/klipper/extras'))
PACKAGE = '_u1_native_feed_parity'
package = types.ModuleType(PACKAGE)
package.__path__ = [str(EXTRAS)]
for extra in os.environ.get('U1_ACE_TEST_HELPERS', '').split(os.pathsep):
    if extra:
        package.__path__.append(extra)
sys.modules[PACKAGE] = package
sys.modules[PACKAGE + '.pulse_counter'] = types.ModuleType(PACKAGE + '.pulse_counter')
with patch('os.path.isdir', return_value=True), patch('os.path.isfile', return_value=True), patch('os.path.lexists', return_value=True):
    feed_module = importlib.import_module(PACKAGE + '.filament_feed_ace')
guard_module = importlib.import_module(PACKAGE + '.ace_stop_guard')


class Reactor:
    NEVER = 1e30

    def __init__(self):
        self.now = 0.

    def monotonic(self):
        return self.now

    def pause(self, until):
        self.now = max(self.now, until)


class Machine:
    def __init__(self, main='IDLE', action='IDLE'):
        self.main, self.action = main, action

    def get_status(self):
        return {'main_state': self.main, 'action_code': self.action}


class GCode:
    def __init__(self, machine):
        self.machine = machine
        self.commands = []

    def run_script_from_command(self, command):
        self.commands.append(command.strip())
        for line in command.splitlines():
            fields = line.split()
            if fields and fields[0] in ('SET_MAIN_STATE', 'SET_ACTION_CODE'):
                values = dict(f.split('=', 1) for f in fields[1:])
                self.machine.main = values.get('MAIN_STATE', self.machine.main)
                self.machine.action = values.get('ACTION', self.machine.action)

    def respond_raw(self, message):
        pass


class CommandError(RuntimeError):
    def __init__(self, message, details):
        super().__init__(message)
        self.details = details


class Command:
    def __init__(self, **values):
        self.values = values

    def get_int(self, name, default=None, **kwargs):
        return self.values.get(name, default)

    def get(self, name, default=None):
        return self.values.get(name, default)

    def get_raw_command_parameters(self):
        return str(self.values)

    def error(self, *args, **kwargs):
        return CommandError(kwargs.get('message', args[0] if args else 'command failed'), kwargs)


def make_feed(main='IDLE', action='IDLE'):
    machine = Machine(main, action)
    feed = feed_module.FilamentFeed.__new__(feed_module.FilamentFeed)
    feed.ace = None
    feed.reactor = Reactor()
    feed.module_name = 'left'
    feed.filament_ch = [0, 1]
    feed.channel_state = [feed_module.FEED_STA_PRELOAD_FINISH] * 2
    feed.channel_error = [feed_module.FEED_OK] * 2
    feed.channel_error_state = [feed_module.FEED_STA_NONE] * 2
    feed.exception_code = [0, 0]
    feed.channel_active = None
    feed.config = {'auto_mode': [True, True]}
    feed.module_exist = [True, True]
    feed.manual_feeding = [False, False]
    feed.saved_states = []
    feed._port = [types.SimpleNamespace(get_filament_detected=lambda: True)] * 2
    feed.runout_sensor = [types.SimpleNamespace(runout_buttun_state=True,
        get_status=lambda eventtime: {'filament_detected': True, 'enabled': True})] * 2
    feed._get_filament_temp = lambda channel: 220
    feed._get_filament_unload_temp = lambda channel: 220
    feed._get_filament_temp_db = lambda channel: 220
    feed._get_filament_soft = lambda channel: False
    feed._check_homing_xy = lambda: True
    feed._hang_neutral = lambda channel: None
    feed._put_into_drive = lambda channel: None
    feed.toolhead = types.SimpleNamespace(wait_moves=lambda: None,
        get_extruder=lambda: types.SimpleNamespace(nozzle_diameter=.4))
    feed.gcode = GCode(machine)
    feed.printer = types.SimpleNamespace(
        lookup_object=lambda name, default=None: (
            feed.ace if name == 'ace' else machine if name == 'machine_state_manager' else default),
        extract_coded_message_field=lambda message: message)

    def set_state(channel, state, save=False):
        feed.channel_state[channel] = state
        feed.saved_states.append((channel, state, save))
    feed._set_channel_state = set_state
    return feed, machine


class NativeFeedParityTests(unittest.TestCase):
    def _ace_for_command(self, feed):
        ace = types.SimpleNamespace(
            _stop_guard=guard_module.StopFaultLatch(), _active_device_index=0,
            _head_source={0: {'ace_index': 0, 'slot': 0}}, _ace_mode='multi',
            head_uses_ace=lambda head: True, _ace_slot_for_head=lambda head: 0,
            _require_location_clear=lambda *args, **kwargs: None,
            _ensure_active_ace_for_head=lambda head: None,
            _is_v2_idx=lambda idx: True)
        feed.ace = ace
        return ace

    def test_source_preflight_refusal_is_normal_command_error_without_shutdown(self):
        for main, expected_action in (('IDLE', 'none'), ('PRINTING', 'pause')):
            with self.subTest(main=main):
                feed, machine = make_feed(main)
                ace = self._ace_for_command(feed)
                with self.assertRaises(CommandError) as caught:
                    feed.cmd_FEED_AUTO(Command(CHANNEL=0, LOAD=1))
                self.assertIn('确认原来源', str(caught.exception))
                self.assertEqual(caught.exception.details['action'], expected_action)
                self.assertEqual(machine.main, main)
                self.assertIsNone(feed.channel_active)
                self.assertEqual(feed.gcode.commands, [])
                self.assertIsNone(ace._stop_guard.snapshot())

    def test_repeated_load_of_physically_loaded_head_is_noop_without_reconfirming_source(self):
        feed, machine = make_feed()
        self._ace_for_command(feed)  # A pre-Flow.15 source has no insertion metadata.
        feed.channel_state[0] = feed_module.FEED_STA_LOAD_FINISH
        feed.cmd_FEED_AUTO(Command(CHANNEL=0, LOAD=1))
        self.assertEqual(feed.channel_state[0], feed_module.FEED_STA_LOAD_FINISH)
        self.assertEqual((machine.main, machine.action), ('IDLE', 'IDLE'))
        self.assertEqual(feed.gcode.commands, [])
        self.assertIsNone(feed.channel_active)

    def test_auto_mode_toggle_preserves_loaded_head(self):
        for enabled in (False, True):
            for exists in (False, True):
                with self.subTest(enabled=enabled, exists=exists):
                    feed, _ = make_feed()
                    feed.channel_state[0] = feed_module.FEED_STA_LOAD_FINISH
                    feed.module_exist[0] = exists
                    feed._do_feed(0, feed_module.FEED_ACT_UPDATE_AUTO_MODE, auto_mode=enabled)
                    self.assertEqual(feed.channel_state[0], feed_module.FEED_STA_LOAD_FINISH)
                    self.assertEqual(feed.config['auto_mode'][0], enabled)
                    self.assertIn((0, feed_module.FEED_STA_LOAD_FINISH, True), feed.saved_states)
                    self.assertIsNone(feed.channel_active)
                    self.assertFalse(feed.gcode.commands)

    def test_auto_mode_enabling_absent_feeder_clears_stale_unloaded_state(self):
        feed, _ = make_feed()
        feed.module_exist[0] = False
        feed.channel_state[0] = feed_module.FEED_STA_PRELOAD_FAIL
        feed._do_feed(0, feed_module.FEED_ACT_UPDATE_AUTO_MODE, auto_mode=True)
        self.assertEqual(feed.channel_state[0], feed_module.FEED_STA_NONE)
        self.assertIsNone(feed.channel_active)

    def test_unloaded_auto_mode_follows_real_port(self):
        for present in (False, True):
            with self.subTest(present=present):
                feed, _ = make_feed()
                feed._port[0] = types.SimpleNamespace(get_filament_detected=lambda: present)
                feed._do_feed(0, feed_module.FEED_ACT_UPDATE_AUTO_MODE, auto_mode=True)
                expected = feed_module.FEED_STA_PRELOAD_FINISH if present else feed_module.FEED_STA_WAIT_INSERT
                self.assertEqual(feed.channel_state[0], expected)

    def test_combined_unload_returns_idle_and_next_command_can_run(self):
        feed, machine = make_feed()
        calls = []
        def unload(channel, action, stage=None):
            calls.append((channel, action, stage))
            feed.channel_state[channel] = feed_module.FEED_STA_UNLOAD_FINISH
        feed._do_feed = unload  # Motion is the peripheral boundary for this command test.
        feed.cmd_FEED_AUTO(Command(CHANNEL=0, UNLOAD=1))
        self.assertEqual((machine.main, machine.action), ('IDLE', 'IDLE'))
        self.assertEqual(calls, [(0, feed_module.FEED_ACT_UNLOAD, None)])
        feed.cmd_FEED_AUTO(Command(CHANNEL=0, UNLOAD=1))
        self.assertEqual(len(calls), 2)

    def test_combined_unload_during_paused_print_keeps_print_resumable(self):
        feed, machine = make_feed('PRINTING', 'PAUSED')
        feed._do_feed = lambda *args, **kwargs: None
        feed.cmd_FEED_AUTO(Command(CHANNEL=0, UNLOAD=1))
        self.assertEqual((machine.main, machine.action), ('PRINTING', 'IDLE'))
        self.assertFalse(any('MAIN_STATE=IDLE' in line for line in feed.gcode.commands))

    def test_prepare_unload_stays_in_preparation_until_done_or_cancel(self):
        feed, machine = make_feed()
        feed._do_feed = lambda *args, **kwargs: None
        feed.cmd_FEED_AUTO(Command(CHANNEL=0, UNLOAD=1, STAGE='prepare'))
        self.assertEqual(machine.main, 'AUTO_UNLOAD')
        feed.cmd_FEED_AUTO(Command(CHANNEL=0, UNLOAD=1, STAGE='cancel'))
        self.assertEqual((machine.main, machine.action), ('IDLE', 'IDLE'))

    def test_failed_unload_returns_idle_and_preserves_recoverable_error(self):
        feed, machine = make_feed()
        def fail(channel, action, stage=None):
            feed.channel_error[channel] = feed_module.FEED_ERR_TIMEOUT
            raise RuntimeError('filament did not arrive')
        feed._do_feed = fail
        with self.assertRaisesRegex(RuntimeError, 'timeout'):
            feed.cmd_FEED_AUTO(Command(CHANNEL=0, UNLOAD=1))
        self.assertEqual((machine.main, machine.action), ('IDLE', 'IDLE'))
        self.assertEqual(feed.channel_error[0], feed_module.FEED_ERR_TIMEOUT)

    def test_hot_seat_stop_failure_does_not_enter_extrusion_or_rearm_assist(self):
        feed, _ = make_feed()
        events = []
        stop_error = feed_module.StopUnconfirmedError('seat STOP not confirmed')
        ace = types.SimpleNamespace(
            _stop_guard=guard_module.StopFaultLatch(),
            _active_device_index=0, _ace_mode='multi',
            _head_source={0: {'ace_index': 0, 'slot': 0}},
            _auto_feed_enabled=False, _fa_context='idle', _fa_load_disable=set(),
            _feed_assist_per_ace={}, _path_operations=None, seat_overshoot_length=20,
            head_uses_ace=lambda head: True, _is_v2_idx=lambda idx: False,
            _ace_slot_for_head=lambda head: 0, _ensure_active_ace_for_head=lambda head: None,
            _check_calibration_unload_cancel=lambda: None, _fa_trace=lambda text: None,
            wait_ace_ready=lambda: None,
            _arm_fa_for=lambda idx, slot: events.append('arm'),
            _disable_feed_assist_all=lambda: events.append('disarm'),
            _dwell_fan=lambda enabled: events.append(('dwell', enabled)),
            _tipform_send=lambda *args, **kwargs: {'code': 0, 'msg': 'success'},
            _tipform_rejected=lambda response: False)
        def stop(idx, request):
            if request['method'] == 'stop_feed_filament':
                events.append('stop_failed')
                raise stop_error
        ace._checked_stop_request = stop
        ace._retract_with_decoder_span = lambda idx, slot, move: move()
        feed.ace = ace
        save_state = feed._set_channel_state
        def state_before_extrusion(channel, state, save=False):
            save_state(channel, state, save)
            self.assertNotEqual(state, feed_module.FEED_STA_LOAD_EXTRUDING,
                                'the failed STOP was swallowed and extrusion was entered')
        feed._set_channel_state = state_before_extrusion
        with self.assertRaises(feed_module.StopUnconfirmedError) as caught:
            feed._do_feed(0, feed_module.FEED_ACT_LOAD)
        self.assertIs(caught.exception, stop_error)
        self.assertEqual(events.count('arm'), 1)
        self.assertNotIn(feed_module.FEED_STA_LOAD_EXTRUDING,
                         [state for channel, state, save in feed.saved_states])
        self.assertEqual(feed.channel_state[0], feed_module.FEED_STA_LOAD_FAIL)
        self.assertIsNone(feed.channel_active)
        self.assertIn('M104 S0', feed.gcode.commands)
        self.assertEqual((ace._auto_feed_enabled, ace._fa_context), (False, 'idle'))


if __name__ == '__main__':
    unittest.main()
