"""Bounded native-driver regression against extracted production methods.

The fixture is generated from the pinned upstream source plus source-patches.
When a staged checkout is available, verify its method ASTs are identical.
"""
import ast
import logging
import math
import os
from pathlib import Path
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from lane_contract import native_sample, stock_sample
from test_lane_contract import stock
from test_lane_io import c

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / 'fixtures/native_motion.py'


class LinkDead(IOError):
    pass


def chip_words(raw):
    if len(raw) != 12:
        return ()
    result = struct.unpack('<3I', raw)
    return result if any(result) else ()


namespace = dict(logging=logging, math=math, LinkDead=LinkDead,
                 LOST_EVENT_S=.5, LOST_ENCODER_WAIT_S=1.5,
                 FOLLOW_MODES=(5, 6, 8), STOP_EVENT_WAIT_S=.2,
                 FAILED=-1, UNSUPPORTED=-2, SPEED_MIN_MM_S=9.,
                 SPEED_MAX_MM_S=70., DEADLINE_EXTRA_S=10.,
                 FREE_TIP_MIN_MM=100., START_BUSY_RETRY_S=30.,
                 DRYER_METHODS=(), MOTION_METHODS=(
                     'feed_filament', 'unwind_filament', 'stop_feed_filament',
                     'stop_unwind_filament', 'stop_feed_assist',
                     'start_feed_assist', 'update_feeding_speed',
                     'update_unwinding_speed'), native_sample=native_sample,
                 chip_words=chip_words, KIND_NAMES={0: 'done', 1: 'stopped'},
                 NOTICE_KINDS=(), ERROR_KINDS=(), RELAX_END_KINDS=(),
                 REMAIN_MIN_MM=1., SLIP_MOVED_MM=5.)
exec(compile(FIXTURE.read_text(), str(FIXTURE), 'exec'), namespace)
NativeMotion = namespace['NativeMotion']


class NativeStallTests(unittest.TestCase):
    def setUp(self):
        self.h = h = NativeMotion()
        self.now = 100.
        self.events = []
        h._now = lambda: self.now
        h._lp = ''
        h._feed = None
        h._jobs = {}
        h._tag_reads = {}
        h._follow = {3: 99}
        h._follow_t0 = {3: 90.}
        h._follow_want = {3}
        h._follow_lost = {}
        h._fresh = lambda v: v is not None and 0 <= self.now-v[0] <= 3.5
        h._end_job = Mock(side_effect=lambda lane, *a, **kw: h._jobs.pop(lane, None))
        h._end_tag_read = Mock(side_effect=lambda lane, *a: h._tag_reads.pop(lane, None))
        h._start_piece = Mock(return_value='ok')
        h._send = Mock(return_value=True)
        h._work_events = Mock()
        h._flog_advance = Mock()
        h._relax_watch = Mock()
        h._follow_full_watch = Mock()
        h._lanes_tick = Mock()
        h._start_follow = Mock(return_value=(1, 'ok'))
        h._note_follow_lost = Mock()
        h._lost_piece_mm = Mock(return_value=5.)
        h._pump = Mock()
        h._tag_tick_original = h._tag_tick
        h._dryer_tick = Mock()
        h._ready = True
        h._shutdown = None
        h._call = Mock(side_effect=lambda *a, **kw: {'accepted': 1, 'lane': kw.get('lane')})
        h._speed_um = lambda speed: max(9000,min(70000,int(float(speed)*1000)))
        h.dictionary = SimpleNamespace(constant=lambda key, default: default)
        h._motion_seq = 0
        h._lane_motion = [None]*4
        h._lane_report = lambda: True
        h._evidence_sensors = (100., 2)
        h._evidence_enc = (100., [0, 1200, 0, 1])
        h._fg = (100., [0, 20, 0, 10])

    def job(self, **kw):
        job = dict(kind='retract', slot=1, done=0., piece=100., target=100., speed=50., t0=80., deadline=100.)
        job.update(kw)
        self.h._jobs[1] = job
        return job

    def modes(self, lane=0, stamp=None):
        self.h._feed = (self.now if stamp is None else stamp, {'mode': bytes((0,lane,0,8))})

    def assert_peer_untouched(self):
        self.assertEqual(self.h._follow.get(3),99)
        self.assertIn(3,self.h._follow_want)
        self.assertFalse(any(call.kwargs.get('lane') == 3 for call in self.h._send.call_args_list))

    def test_expired_waiting_never_starts(self):
        self.job(waiting=True); self.h._follow_want.add(1)
        self.h._tick()
        self.h._start_piece.assert_not_called()
        self.assertNotIn(1,self.h._jobs)
        self.assertNotIn(1,self.h._follow_want)
        self.assert_peer_untouched()

    def test_busy_retry_before_deadline_retains_original_limit(self):
        j=self.job(waiting=True,deadline=105.)
        self.h._start_piece.return_value='busy'
        for t in range(100,105):
            self.now=float(t); self.h._tick(); self.assertEqual(j['deadline'],105.)
        self.now=105.; self.h._tick()
        self.assertEqual(self.h._start_piece.call_count,5)
        self.assertNotIn(1,self.h._jobs)

    def test_stop_request_before_tick_prevents_pending_start(self):
        self.job(waiting=True,deadline=105.)
        self.h._execute({'id':5,'method':'stop_feed_filament','params':{'index':1}})
        self.h._start_piece.assert_not_called()
        self.assertNotIn(1,self.h._jobs)
        self.h._send.assert_called_once_with('ace2k_feed_stop',lane=1)
        self.assert_peer_untouched()

    def test_cancel_pending_and_later_tick_does_not_restart(self):
        self.job(waiting=True,deadline=120.)
        reply=self.h._motion('stop_feed_filament',{'index':1},1)
        self.assertEqual(reply['code'],0)
        self.h._tick(); self.h._start_piece.assert_not_called()
        self.assert_peer_untouched()

    def test_running_timeout_keeps_job_until_fresh_idle(self):
        j=self.job(); self.modes(2); self.h._follow_want.add(1)
        self.h._tick()
        self.assertIs(self.h._jobs[1],j); self.assertTrue(j['stopping'])
        self.assertNotIn(1,self.h._follow_want)
        self.now=101.; self.modes(0); self.h._tick()
        self.assertNotIn(1,self.h._jobs); self.assert_peer_untouched()

    def test_timeout_no_ack_uses_existing_link_recovery(self):
        self.job(); self.modes(2); self.h._send.return_value=None
        self.h._tick(); self.assertIn(1,self.h._jobs)
        self.now=103.; self.modes(2)
        with self.assertRaises(LinkDead): self.h._tick()
        self.assertIn(1,self.h._jobs); self.h._end_job.assert_not_called()
        self.assert_peer_untouched()

    def test_timeout_ack_but_motor_never_stops_is_bounded(self):
        self.job(); self.modes(2)
        for t in (100.,101.,102.):
            self.now=t; self.modes(2); self.h._tick(); self.assertIn(1,self.h._jobs)
        self.now=103.; self.modes(2)
        with self.assertRaises(LinkDead): self.h._tick()
        self.assertEqual(self.h._send.call_count,4)
        self.assert_peer_untouched()

    def test_follow_or_unknown_mode_not_idle(self):
        for mode in (5,6,8,255):
            self.job(deadline=105.); self.modes(mode)
            self.h._tick(); self.assertIn(1,self.h._jobs)

    def test_rfid_timeout_waits_for_stop_and_preserves_other_slot(self):
        self.h._tag_reads={1:{'t0':90.,'deadline':100.},2:{'t0':90.,'deadline':120.}}
        self.h._feed=(100.,{'mode':bytes((0,4,4,8))})
        self.h._tag_tick(); self.assertIn(1,self.h._tag_reads)
        self.now=101.; self.h._feed=(101.,{'mode':bytes((0,0,4,8))})
        self.h._tag_tick(); self.assertNotIn(1,self.h._tag_reads)
        self.assertIn(2,self.h._tag_reads); self.assert_peer_untouched()

    def test_rfid_stop_unanswered_does_not_release(self):
        self.h._tag_reads={1:{'t0':90.,'deadline':100.}}
        self.modes(4); self.h._send.return_value=False
        self.h._tag_tick(); self.assertIn(1,self.h._tag_reads)
        self.now=103.; self.modes(4)
        with self.assertRaises(LinkDead): self.h._tag_tick()
        self.assertIn(1,self.h._tag_reads)

    def test_auto_buffer_stop_ack_is_not_end_event(self):
        j=self.job(auto=True,deadline=120.)
        self.h._yield_auto(1)
        self.assertIs(self.h._jobs[1],j); self.assertTrue(j['stopping'])
        self.h._end_job.assert_not_called(); self.assert_peer_untouched()

    def test_auto_buffer_stop_failure_retains_owner(self):
        j=self.job(auto=True,deadline=120.); self.h._send.return_value=None
        self.h._yield_auto(1)
        self.assertIs(self.h._jobs[1],j); self.h._end_job.assert_not_called()

    def test_unanswered_follow_stop_blocks_competing_retract(self):
        self.h._follow[1]=44; self.h._follow_want.add(1)
        self.h._send.return_value=None
        reply=self.h._motion('unwind_filament',{'index':1,'length':20,'speed':50},1)
        self.assertNotEqual(reply['code'],0)
        self.assertIn(1,self.h._follow); self.assertNotIn(1,self.h._follow_want)
        self.h._start_piece.assert_not_called(); self.assert_peer_untouched()

    def test_explicit_stop_assist_cancels_queued_follow_only_for_target(self):
        self.h._follow_want.add(1)
        self.assertEqual(self.h._motion('stop_feed_assist',{'index':1},1)['code'],0)
        self.assertNotIn(1,self.h._follow_want); self.assert_peer_untouched()

    def test_invalid_index_never_turns_into_all_lanes_stop(self):
        self.job(deadline=120.)
        for method in ('stop_feed_filament','stop_feed_assist'):
            for bad in (4, True, 1.9, float('inf'), '1'):
                self.assertNotEqual(self.h._motion(method,{'index':bad},1)['code'],0)
        self.h._send.assert_not_called(); self.assertIn(1,self.h._jobs)

    def test_same_speed_updates_do_not_move_deadline(self):
        j=self.job(deadline=120.)
        for t in (100.,101.,102.):
            self.now=t; self.h._motion('update_feeding_speed',{'index':1,'speed':50},1)
            self.assertEqual(j['deadline'],120.)

    def test_alternating_speeds_cannot_extend_past_absolute_cap(self):
        j=self.job(deadline=120.)
        cap=j['t0']+1.5*j['piece']/9.+10.
        for t in (100.,101.,102.,103.,104.,105.):
            self.now=t; self.h._motion('update_feeding_speed',{'index':1,'speed':9 if int(t)%2 else 70},1)
            self.assertLessEqual(j['deadline'],cap)

    def test_nonfinite_motion_never_starts(self):
        for v in (float('nan'),float('inf'),-float('inf')):
            self.assertNotEqual(self.h._motion('unwind_filament',{'index':1,'length':v,'speed':20},1)['code'],0)
        self.h._start_piece.assert_not_called()

    def test_real_clamped_speed_is_used_for_move_deadline(self):
        self.h._motion('unwind_filament',{'index':1,'length':20,'speed':999},1)
        self.assertEqual(self.h._jobs[1]['speed'],70.)

    def test_late_done_event_cannot_start_next_piece(self):
        j=self.job(seq=10,target=200.)
        self.h._follow_want.add(1)
        self.h._on_event(dict(lane=1,kind=0,seq=10,filament_um=100000))
        self.h._start_piece.assert_not_called()
        self.assertNotIn(1,self.h._jobs); self.assertNotIn(1,self.h._follow_want)
        self.assert_peer_untouched()

    def test_on_time_done_event_can_start_next_piece(self):
        j=self.job(seq=10,target=200.,deadline=110.)
        self.h._on_event(dict(lane=1,kind=0,seq=10,filament_um=100000))
        self.h._start_piece.assert_called_once_with(j)

    def test_native_snapshot_retains_pending_job_state(self):
        self.job(waiting=True,deadline=120.); self.modes(0)
        self.h._start_piece.return_value='busy'
        reply=self.h._execute({'id':5,'method':'get_lane_state','params':{'index':1}})
        self.assertEqual(reply['result']['state'],'rollback')


class TelemetryStallTests(unittest.TestCase):
    def test_malformed_native_samples_are_missing_not_exceptions(self):
        valid=[(10,2),(10,{'mode':[0]*4}),(10,[0]*4),(10,[0]*4)]
        for index,bad in ((0,(None,2)),(0,(10,None)),(1,(10,None)),(2,(10,None)),(3,(10,[0, -1,0,0])),(2,(10,[0,2**31,0,0])),(1,(10,{'mode':[0,{},0,0]}))):
            data=valid.copy(); data[index]=bad
            with self.subTest(index=index,bad=bad): self.assertIsNone(native_sample(*data,1,10))

    def test_malformed_stock_frames_are_missing_not_exceptions(self):
        for key,value in (('slots',None),('slots',[None]),('slots',[dict(index=True)])):
            s,g,f=stock(); s['result'][key]=value
            self.assertIsNone(stock_sample(s,g,f,1))
        s,g,f=stock(); g['result']['fields']=None
        self.assertIsNone(stock_sample(s,g,f,1))

    def test_v2_no_response_does_not_run_two_more_timeout_queries(self):
        h=SimpleNamespace(_serials={3:object()},_protocols={3:SimpleNamespace(NAME='v2')},_connected_per_ace={3:True},_tipform_send=Mock(return_value=None))
        self.assertIsNone(c.read_lane_sample(h,3,1)); self.assertEqual(h._tipform_send.call_count,1)

    def test_v2_second_query_failure_stops_before_third(self):
        h=SimpleNamespace(_serials={3:object()},_protocols={3:SimpleNamespace(NAME='v2')},_connected_per_ace={3:True},_tipform_send=Mock(side_effect=[stock()[0],None]))
        self.assertIsNone(c.read_lane_sample(h,3,1)); self.assertEqual(h._tipform_send.call_count,2)

    def test_invalid_slot_does_not_send(self):
        h=SimpleNamespace(_serials={3:object()},_protocols={3:SimpleNamespace(NAME='ace2k')},_connected_per_ace={3:True},_tipform_send=Mock())
        for slot in (-1,4,True,None): self.assertIsNone(c.read_lane_sample(h,3,slot))
        h._tipform_send.assert_not_called()

    def test_driver_change_on_same_port_discards_response(self):
        h=SimpleNamespace(_serials={3:object()},_protocols={3:SimpleNamespace(NAME='ace2k')},_connected_per_ace={3:True})
        def ask(*a,**kw):
            h._protocols[3]=SimpleNamespace(NAME='v2')
            return dict(code=0,msg='success',result=dict(index=1,present=True,state='ready',encoder=1,motor=2,stamp=[1,1,1,1]))
        h._tipform_send=Mock(side_effect=ask)
        self.assertIsNone(c.read_lane_sample(h,3,1))

    def test_native_invalid_state_counter_or_stamp_rejected(self):
        good=dict(index=1,present=True,state='ready',encoder=1,motor=2,stamp=[1,1,1,1])
        h=SimpleNamespace(_serials={3:object()},_protocols={3:SimpleNamespace(NAME='ace2k')},_connected_per_ace={3:True},_tipform_send=Mock())
        for changes in (dict(state=None),dict(state='unknown'),dict(motor=-1),dict(index=True),dict(stamp=[1,1,float('nan'),1])):
            h._tipform_send.return_value=dict(code=0,msg='success',result=dict(good,**changes))
            self.assertIsNone(c.read_lane_sample(h,3,1))


class StagedSourceTests(unittest.TestCase):
    def test_fixture_matches_staged_driver(self):
        staged=os.environ.get('MULTIACE_STAGED')
        if not staged:
            self.skipTest('Full staged checkout unavailable locally')
        src=Path(staged)/'multiace/klipper/extras/ace_protocol_ace2k.py'
        def methods(path):
            tree=ast.parse(path.read_text())
            return {n.name:ast.dump(n,include_attributes=False) for n in ast.walk(tree) if isinstance(n,ast.FunctionDef)}
        actual=methods(src)
        tree=ast.parse(FIXTURE.read_text())
        for fn in tree.body[0].body:
            if isinstance(fn,ast.FunctionDef): self.assertEqual(ast.dump(fn,include_attributes=False),actual[fn.name],fn.name)


if __name__=='__main__': unittest.main()
