"""Flow15 scan envelope, attribution and measured-profile contracts."""
import copy
import importlib
from pathlib import Path
import tempfile
import types
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = '_flow15_rfid_tests'
pkg = types.ModuleType(PACKAGE)
pkg.__path__ = [str(ROOT / 'runtime/klipper/extras')]
sys.modules[PACKAGE] = pkg
rc = importlib.import_module(PACKAGE + '.ace_rc522')
ev = importlib.import_module(PACKAGE + '.ace_rfid_evidence')
remaining = importlib.import_module(PACKAGE + '.ace_manual_remaining')
UID = '04AABBCCDDEE11'


class Reactor:
    def __init__(self):
        self.now = 1.0

    def monotonic(self):
        return self.now

    def pause(self, at):
        self.now = max(self.now, at)


def scan_fixture(window=(400, 440), maximum=600):
    """Drive the real scan/rotation/budget code with ideal measured moves."""
    reader = rc.AceTagReader.__new__(rc.AceTagReader)
    reactor = Reactor()
    transport = object()
    ace = types.SimpleNamespace(reactor=reactor, _serials={0: transport},
        _ace_mode='multi', _insert_epochs={(0, 0): 1}, _tag_op_seq=9,
        _location_identity=lambda idx: ('/dev/ace', (1, 2, 3)),
        _rfid_route_heads=lambda idx, slot: [0], _slot_actions=types.SimpleNamespace(cancel=False),
        _single_verified_uid={}, _rc_last_uid={})
    reader.ace = ace
    reader._motion_uncertain = None
    reader._motion_ledger = {}
    reader._encoder_ledger = {}
    reader._encoder_direction = {}
    reader._insert_guard = lambda: None
    reader._manual_guard = lambda: None
    reader._target_motion_allowed = lambda *args: True
    reader._wait_motion_ready = lambda *args: True
    reader._neighbour_signature = lambda *args: ('ready', 0, 0, 0)
    reader._continuous_return_gate = lambda *args: None
    reader._manual_verify_stationary = lambda *args: None
    reader._pause = lambda seconds: reactor.pause(reactor.now + seconds)
    reader.commands = []
    reader.positions = [0]
    reader.physical_position = 0
    reader.live_raw = 0
    reader.commits = []

    def request(idx, method, params):
        if method == 'feed_or_rollback_raw':
            reader.commands.append(dict(params))
        return {'code': 0, 'msg': 'success', '_v2_payload_valid': True,
                '_v2_frame_valid': True, '_v2_command': 9 if method == 'stop_feed_filament' else 8}
    reader._req = request

    def observed(*args):
        return UID if window is not None and window[0] <= reader.physical_position <= window[1] else ''
    reader._continuous_uid = observed
    reader._observed_uid = observed
    reader._observed_expected_uid = observed

    def sample(*args):
        reader._encoder_diagnostic = {'row': {'decoder': reader.live_raw,
            '_decoder_present': True, 'length': abs(reader.live_raw), 'steps': abs(reader.live_raw) * 12}}
        return reader.live_raw
    reader._manual_sample = sample

    def move(idx, slot, length, mode, respond, *, observe=None):
        length = int(length)
        if not reader._move(idx, slot, length, mode, respond):
            return False
        origin = reader.physical_position
        sign = 1 if mode == rc.SEARCH_MODE else -1
        if observe is not None:
            for offset in range(20, length + 1, 20):
                reader.live_raw = sign * offset
                reader.physical_position = origin + sign * offset
                observe({})
        reader.live_raw = sign * length
        reader.physical_position = origin + reader.live_raw
        reader.positions.append(reader.physical_position)
        reader._encoder_direction[(idx, slot)] = 1
        reader._encoder_ledger[(idx, slot)] = reader.physical_position
        reader._continuous_budget['maximum_position_mm'] = max(
            reader._continuous_budget['maximum_position_mm'], reader.physical_position)
        return True
    reader._move_and_confirm = move
    reader._read_card = lambda *args, **kwargs: setattr(reader, '_pending_identity', {'uid': UID})

    def commit(idx, slot, uid, respond):
        reader.commits.append((uid, reader.physical_position, reader._manual_return_confirmed))
        return True
    reader._commit_continuous_identity = commit
    return reader


class ContinuousScanTests(unittest.TestCase):
    def test_first_scan_reaches_original_600_window_and_reads_late_tag(self):
        reader = scan_fixture((400, 440))
        reader.read_slot_continuous(0, 0, lambda msg: None)
        self.assertEqual(reader.commands[0]['length'], 600)
        self.assertEqual(reader.commits, [(UID, 0, True)])
        self.assertEqual(max(reader.positions), 600)
        self.assertGreater(reader._flow14_remaining['forward_commanded_mm'], 600)
        self.assertEqual(reader._flow14_remaining['maximum_position_mm'], 600)
        self.assertFalse(reader._flow14_remaining['complete_turn_confirmed'])

    def test_far_end_tag_attribution_reuses_window_in_reverse(self):
        reader = scan_fixture((500, 540))
        reader.read_slot_continuous(0, 0, lambda msg: None)
        probes = [x for x in reader.commands if x['length'] == 160]
        self.assertEqual([x['mode'] for x in probes], [1, 0, 1, 0])
        self.assertLessEqual(max(reader.positions), 600)
        self.assertGreaterEqual(min(reader.positions), 0)
        self.assertEqual(reader.commits, [(UID, 0, True)])

    def test_near_origin_tag_uses_forward_probe_inside_window(self):
        reader = scan_fixture((0, 40))
        reader.read_slot_continuous(0, 0, lambda msg: None)
        probes = [x for x in reader.commands if x['length'] == 160]
        self.assertEqual([x['mode'] for x in probes], [0, 1, 0, 1])
        self.assertEqual(reader.commits, [(UID, 0, True)])

    def test_no_tag_is_not_a_complete_turn_or_proof_of_absence(self):
        reader = scan_fixture(None)
        messages = []
        reader.read_slot_continuous(0, 0, messages.append)
        self.assertEqual(reader.physical_position, 0)
        self.assertEqual(reader.commits, [])
        self.assertIsNone(reader._flow14_remaining)
        self.assertTrue(any('不能据此认定无标签' in text for text in messages))

    def test_candidate_without_clean_departure_is_not_committed(self):
        reader = scan_fixture((400, 440))
        reader._observed_uid = lambda *args: UID
        reader.read_slot_continuous(0, 0, lambda msg: None)
        self.assertEqual(reader.physical_position, 0)
        self.assertEqual(reader.commits, [])

    def test_narrow_window_with_no_safe_probe_returns_without_commit(self):
        reader = scan_fixture((80, 120))
        reader.read_slot_continuous(0, 0, lambda msg: None, max_mm=180)
        self.assertEqual(reader.physical_position, 0)
        self.assertEqual(reader.commits, [])

    def test_failed_return_does_not_commit_identity(self):
        reader = scan_fixture((400, 440))
        def failed_return(respond):
            raise rc.MotionUncertain('return missing')
        reader._restore_recorded_motion = failed_return
        with self.assertRaises(rc.MotionUncertain):
            reader.read_slot_continuous(0, 0, lambda msg: None)
        self.assertEqual(reader.commits, [])

    def test_position_bound_is_separate_from_total_forward(self):
        reader = scan_fixture()
        reader._continuous_budget = {'key': (0, 0), 'limit': 600,
            'forward_commanded': 600, 'forward_limit': 940,
            'reverse_commanded': 200, 'reverse_limit': 1070,
            'maximum_position_mm': 600}
        reader._encoder_direction[(0, 0)] = 1
        reader._encoder_ledger[(0, 0)] = 420
        self.assertTrue(reader._move(0, 0, 160, rc.SEARCH_MODE, lambda msg: None))
        self.assertEqual(reader._continuous_budget['forward_commanded'], 760)
        reader._encoder_ledger[(0, 0)] = 500
        with self.assertRaises(rc.ScanBudgetExhausted):
            reader._move(0, 0, 160, rc.SEARCH_MODE, lambda msg: None)

    def test_cumulative_probe_budget_still_ends(self):
        reader = scan_fixture()
        reader._continuous_budget = {'key': (0, 0), 'limit': 600,
            'forward_commanded': 940, 'forward_limit': 940,
            'reverse_commanded': 0, 'reverse_limit': 1070,
            'maximum_position_mm': 0}
        with self.assertRaises(rc.ScanBudgetExhausted):
            reader._move(0, 0, 1, rc.SEARCH_MODE, lambda msg: None)
        self.assertEqual(reader.commands, [])

    def test_reverse_cannot_extend_beyond_origin(self):
        reader = scan_fixture()
        reader._continuous_budget = {'key': (0, 0), 'limit': 600,
            'forward_commanded': 0, 'forward_limit': 940,
            'reverse_commanded': 0, 'reverse_limit': 1070,
            'maximum_position_mm': 0}
        with self.assertRaises(rc.ScanBudgetExhausted):
            reader._move(0, 0, 160, rc.RESTORE_MODE, lambda msg: None)
        self.assertEqual(reader.commands, [])


def profile():
    return {'calibrated': True, 'calibration_id': 'measured-reader-1',
        'core_radius_mm': [25, 26], 'wound_width_mm': [60, 62],
        'packing_fraction': [.80, .85], 'density_g_cm3': [1.20, 1.25],
        'filament_diameter_mm': [1.73, 1.77], 'circumference_mm': [180, 600],
        'maximum_observation_spacing_mm': 10,
        'reader_device_uid': [1, 2, 3], 'reader_pair': 0}


class ProfileTests(unittest.TestCase):
    def test_missing_profile_never_estimates_default_mass(self):
        evidence = {'circumference_mm': {'min_mm': 200, 'max_mm': 210}}
        self.assertEqual(ev.estimate_interval(evidence, None)['reason'], 'spool_profile_missing')

    def test_geometry_and_reader_fields_are_all_explicit(self):
        for name in ('core_radius_mm', 'wound_width_mm', 'packing_fraction',
                     'density_g_cm3', 'filament_diameter_mm', 'reader_pair', 'reader_device_uid'):
            item = profile()
            del item[name]
            with self.assertRaises(ValueError, msg=name):
                ev.validate_profile(item)

    def test_nonfinite_and_reversed_ranges_are_rejected(self):
        for value in ([2, 1], [1, float('nan')], [1, float('inf')], [0, 2], [1, 1e200]):
            item = profile()
            item['core_radius_mm'] = value
            with self.assertRaises(ValueError):
                ev.validate_profile(item)

    def test_profile_roundtrip_preserves_other_spools_and_returns_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            ace = types.SimpleNamespace(paths={'config_dir': directory})
            item = profile()
            remaining.save_profile(ace, UID.lower(), item)
            other = dict(profile(), calibration_id='second')
            remaining.save_profile(ace, 'AABBCCDD', other)
            loaded = remaining.load_profile(ace, UID)
            self.assertEqual(loaded, item)
            loaded['core_radius_mm'][0] = 999
            self.assertEqual(remaining.load_profile(ace, UID), item)
            self.assertEqual(remaining.load_profile(ace, 'AABBCCDD'), other)

    def test_invalid_existing_profile_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            ace = types.SimpleNamespace(paths={'config_dir': directory})
            path = Path(remaining._profile_path(ace))
            path.parent.mkdir()
            path.write_text('{broken')
            with self.assertRaises(ValueError):
                remaining.save_profile(ace, UID, profile())
            self.assertEqual(path.read_text(), '{broken')

    def test_sampling_gap_keeps_remaining_unknown(self):
        evidence = {'circumference_mm': {'min_mm': 200, 'max_mm': 210},
                    'maximum_observation_spacing_mm': 11}
        self.assertEqual(ev.estimate_interval(evidence, profile())['reason'],
                         'rfid_sampling_coverage_unconfirmed')

    def test_valid_profile_produces_interval_not_scale_claim(self):
        evidence = {'circumference_mm': {'min_mm': 200, 'max_mm': 210},
                    'maximum_observation_spacing_mm': 8,
                    'measurement_forward_mm': {'min_mm': 30, 'max_mm': 250},
                    'return_residual_mm': 0}
        result = ev.estimate_interval(evidence, profile())
        self.assertEqual(result['state'], 'estimate')
        self.assertLess(result['remaining_g_interval']['min_g'], result['remaining_g_interval']['max_g'])
        self.assertIn('not a scale measurement', result['limitation'])


class WebRequest:
    def __init__(self, params):
        self.params = params
        self.result = None

    def get(self, key, default=None):
        return self.params.get(key, default)

    def get_int(self, key):
        value = self.params[key]
        if type(value) is not int:
            raise ValueError('expected integer')
        return value

    def get_str(self, key, default=None):
        value = self.params.get(key, default)
        if not isinstance(value, str):
            raise ValueError('expected string')
        return value

    def send(self, result):
        self.result = result


class ProfileEndpointTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.hooks = {}
        transport = object()
        identity = ('/dev/ace', (1, 2, 3))
        hooks = types.SimpleNamespace(register_endpoint=self.hooks.__setitem__)

        def no_motor(*args, **kwargs):
            raise AssertionError('profile operations must never send ACE commands')
        self.ace = types.SimpleNamespace(paths={'config_dir': self.directory.name},
            printer=types.SimpleNamespace(lookup_object=lambda name: hooks),
            _serials={0: transport}, _connected_per_ace={0: True},
            _serial_failed_per_ace={}, _reconnecting_per_ace={},
            _location_identity=lambda idx: identity, _insert_epochs={(0, 0): 2},
            _rc_last_uid={(0, 0): UID},
            _single_verified_uid={(0, 0): (transport, identity, 2, UID)},
            send_request_to=no_motor)
        remaining.register_endpoints(self.ace)

    def request(self, **params):
        values = dict(ace=0, slot=0, uid=UID, action='read')
        values.update(params)
        request = WebRequest(values)
        self.hooks['multiace/spool_profile'](request)
        return request.result

    def test_read_without_profile_is_unknown_and_creates_no_file(self):
        result = self.request()
        self.assertTrue(result['ok'])
        self.assertIsNone(result['profile'])
        self.assertFalse(result['calibrated_for_current_reader'])
        self.assertFalse(Path(remaining._profile_path(self.ace)).exists())

    def test_save_binds_real_reader_and_read_returns_same_profile(self):
        supplied = dict(profile(), reader_device_uid=[9, 8, 7], reader_pair=1)
        result = self.request(action='save', profile=supplied, uid=UID.lower())
        self.assertTrue(result['ok'])
        self.assertEqual(result['profile']['reader_device_uid'], [1, 2, 3])
        self.assertEqual(result['profile']['reader_pair'], 0)
        self.assertEqual(supplied['reader_device_uid'], [9, 8, 7])
        self.assertTrue(result['calibrated_for_current_reader'])
        self.assertEqual(self.request()['profile'], result['profile'])

    def test_empty_profile_has_no_invented_geometry(self):
        result = self.request(action='save', profile={})
        self.assertFalse(result['ok'])
        self.assertFalse(Path(remaining._profile_path(self.ace)).exists())

    def test_reconnect_invalidates_remembered_tag_for_read_and_save(self):
        self.ace._serials[0] = object()
        for action in ('read', 'save'):
            self.assertFalse(self.request(action=action, profile=profile())['ok'])
        self.assertFalse(Path(remaining._profile_path(self.ace)).exists())

    def test_replaced_spool_or_insertion_epoch_cannot_receive_profile(self):
        self.ace._rc_last_uid[(0, 0)] = 'AABBCCDD'
        self.assertFalse(self.request(action='save', profile=profile())['ok'])
        self.ace._rc_last_uid[(0, 0)] = UID
        self.ace._insert_epochs[(0, 0)] = 3
        self.assertFalse(self.request(action='save', profile=profile())['ok'])
        self.assertFalse(Path(remaining._profile_path(self.ace)).exists())

    def test_disconnected_or_reconnecting_reader_cannot_receive_profile(self):
        self.ace._connected_per_ace[0] = False
        self.assertFalse(self.request(action='save', profile=profile())['ok'])
        self.ace._connected_per_ace[0] = True
        self.ace._reconnecting_per_ace[0] = True
        self.assertFalse(self.request(action='save', profile=profile())['ok'])

    def test_saved_calibration_for_other_reader_is_not_silently_rebound(self):
        stored = dict(profile(), reader_device_uid=[9, 8, 7], reader_pair=1)
        remaining.save_profile(self.ace, UID, stored)
        result = self.request()
        self.assertTrue(result['ok'])
        self.assertFalse(result['calibrated_for_current_reader'])
        self.assertEqual(result['profile'], stored)

    def test_profile_file_error_is_visible_and_not_overwritten(self):
        path = Path(remaining._profile_path(self.ace))
        path.parent.mkdir()
        path.write_text('{broken')
        self.assertFalse(self.request()['ok'])
        self.assertFalse(self.request(action='save', profile=profile())['ok'])
        self.assertEqual(path.read_text(), '{broken')

    def test_uid_requires_hex_bytes_not_signed_or_padded_integer(self):
        for value in ('+1234567', '-1234567', ' 1234567', '00000000', 'FFFFFFFF'):
            self.assertFalse(ev._uid(value), value)
            self.assertFalse(self.request(uid=value)['ok'], value)


if __name__ == '__main__':
    unittest.main()
