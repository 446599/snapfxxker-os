import ast, pathlib, unittest, logging, sys, types
import atexit, tempfile, tarfile
REPO=pathlib.Path(__file__).resolve().parents[2]
ARCHIVE=REPO/'overlays/233-dev.2-perms.1/root/usr/local/share/233-firmware/multiace-managed-1.27b-233.20261111-single.2.tar.gz'
TMP=tempfile.TemporaryDirectory(prefix='managed-rfid-tests-')
atexit.register(TMP.cleanup)
ROOT=pathlib.Path(TMP.name)
with tarfile.open(ARCHIVE,'r:gz') as archive:
    for name in ('ace.py','ace_rc522.py'):
        member=archive.getmember('multiace-1.27b-233.20261111-single.2/klipper/extras/'+name)
        (ROOT/name).write_bytes(archive.extractfile(member).read())
def load_method(path,name,ns):
    tree=ast.parse(path.read_text()); fn=next(x for node in tree.body if isinstance(node,ast.ClassDef) for x in node.body if isinstance(x,ast.FunctionDef) and x.name==name)
    code=compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),str(path),'exec');exec(code,ns);return ns[name]
class Clock:
    def __init__(self):self.t=0
    def monotonic(self):return self.t
    def pause(self,t):self.t=t
class Native:
    def __init__(self,seq):self.seq=list(seq);self.reactor=Clock();self._serials={0:object()};self.stops=0
    def _v2_slot_rfid(self,*a):return 0
    def _location_identity(self,*a):return ('port',(1,2,3))
    def _insert_fresh_lane(self,*a):return self.seq.pop(0) if self.seq else None
    def _insert_stop_bound(self,*a):self.stops+=1;return True
    def _stop_feeding(self,*a,**kw):self.stops+=1;return True
    _insert_verified_abort=load_method(ROOT/'ace.py','_insert_verified_abort',{'INSERT_GRAB_WAIT_S':8,'INSERT_GRAB_MM':20,'INSERT_PROC_WAIT_S':3,'INSERT_ABORT_VERIFY_S':4,'INSERT_DEPTH_MAX':2000,'V2_ACTIVE_MOTION_STATES':('feeding','rollback','rollback_assisting','preloading')})
    _insert_fresh_lane_actual=load_method(ROOT/'ace.py','_insert_fresh_lane',{})
    _insert_stop_unknown=load_method(ROOT/'ace.py','_insert_stop_unknown',{'INSERT_ABORT_VERIFY_S':8})
    _insert_native_once=load_method(ROOT/'ace.py','_insert_native_once',{'UID_SENTINEL_VERSIONS':(0x0201,0x0102)})
class Uncertain(Exception):pass
class Budget(Exception):pass
class Reader:
    def __init__(self,raw,direction=1,stop_ok=True):self.raw=raw;self._encoder_direction={(0,1):direction};self._encoder_ledger={};self.stop_ok=stop_ok
    def _req(self,*a):return {'code':0 if self.stop_ok else 1,'_v2_command':9,'_v2_frame_valid':True,'_v2_payload_valid':True}
    def _pause(self,*a):pass
    def _wait_motion_ready(self,*a):return True
    def _fresh_encoder(self,*a):
        self._encoder_diagnostic={'row':{'_decoder_present':self.raw not in (None,0),'steps':100,'length':25}}
        return self.raw
    def _insert_guard(self):pass
    _stop_and_measure=load_method(ROOT/'ace_rc522.py','_stop_and_measure',{'MotionUncertain':Uncertain,'SEARCH_MODE':0,'logging':logging})
    _commit_scan_motor=load_method(ROOT/'ace_rc522.py','_commit_scan_motor',{'MotionUncertain':Uncertain,'SEARCH_MODE':0,'logging':logging})
    _correct_to=load_method(ROOT/'ace_rc522.py','_correct_to',{'MotionUncertain':Uncertain,'SEARCH_MODE':0,'RESTORE_MODE':1,'PARK_SPEED':10})
    _rewind_scan_origin=load_method(ROOT/'ace_rc522.py','_rewind_scan_origin',{'MotionUncertain':Uncertain,'SEARCH_MODE':0,'RESTORE_MODE':1,'logging':logging})
    _natural_sweep_terminal=load_method(ROOT/'ace_rc522.py','_natural_sweep_terminal',{'MotionUncertain':Uncertain})
    _move_actual=load_method(ROOT/'ace_rc522.py','_move',{'MotionUncertain':Uncertain,'ScanBudgetExhausted':Budget,'PARK_SPEED':10,'SEARCH_MODE':0,'MOVE_MAX_MM':2000,'MOVE_RETRIES':1})
    _checked_actual=load_method(ROOT/'ace_rc522.py','read_slot_transport_checked',{'MotionUncertain':Uncertain,'ScanBudgetExhausted':Budget,'logging':logging})
    _single_actual=load_method(ROOT/'ace_rc522.py','read_slot_single_checked',{'MotionUncertain':Uncertain,'ScanBudgetExhausted':Budget,'SEARCH_MODE':0,'SELECT_OK':0,'PARK_SPEED':40,'logging':logging})
    _move_and_confirm=load_method(ROOT/'ace_rc522.py','_move_and_confirm',{'MotionUncertain':Uncertain,'PARK_SPEED':10,'logging':logging})
    _observed_expected_uid=load_method(ROOT/'ace_rc522.py','_observed_expected_uid',{})
class DirectionTests(unittest.TestCase):
    def test_fast_expected_uid_single_bcc_read_and_unknown(self):
        reader=Reader(0);calls=[]
        reader._rc_select=lambda *a:(calls.append('select') or True)
        reader._rc_read_uid=lambda *a:(calls.append('ntag') or '53A78935930001')
        reader._is_single_uid=lambda uid:len(uid)==8 and not uid.startswith('88')
        reader._current_single_uid=lambda *a:(calls.append('single') or 'AABBCCDD')
        self.assertEqual(reader._observed_expected_uid(0,1,'53A78935930001'),'53A78935930001')
        self.assertEqual(calls,['select','ntag']);calls.clear()
        self.assertEqual(reader._observed_expected_uid(0,1,'AABBCCDD'),'AABBCCDD')
        self.assertEqual(calls,['select','single']);calls.clear()
        reader._rc_select=lambda *a:False
        self.assertIsNone(reader._observed_expected_uid(0,1,'53A78935930001'))
        reader._rc_select=lambda *a:True;reader._rc_read_uid=lambda *a:''
        self.assertIsNone(reader._observed_expected_uid(0,1,'53A78935930001'))
    def test_seven_byte_uid_both_bcc_are_checked(self):
        fn=load_method(ROOT/'ace_rc522.py','uid_from_page0',{})
        uid=bytes.fromhex('53A78935930001')
        packet=bytes([*uid[:3],0x88^uid[0]^uid[1]^uid[2],*uid[3:],uid[3]^uid[4]^uid[5]^uid[6]])
        self.assertEqual(fn(packet),'53A78935930001')
        for index in (3,8):
            bad=bytearray(packet);bad[index]^=1
            self.assertEqual(fn(bad),'')
    def test_ambiguous_sweep_hit_rewinds_without_second_forward(self):
        env={'PARK_SPEED':40,'SEARCH_MODE':0,'START_PROBE_MM':40,'MOVE_MAX_MM':2000,
             'MotionUncertain':Uncertain}
        run=load_method(ROOT/'ace_rc522.py','read_slot_transport',env)
        reader=Reader(0,-1);clock=Clock();reader.ace=types.SimpleNamespace(reactor=clock,_location_identity=lambda idx:('port',(1,2,3)))
        reader._scan_measured=True;reader._encoder_ledger={};reader._encoder_direction={};reader._insert_checked_guard=lambda:True
        reader._known_neighbour_uid=lambda *a:'';reader._wait_neighbour_settled=lambda *a:None
        selections=iter([False,True]);reader._rc_select=lambda *a:next(selections)
        reader._fresh_encoder=lambda *a:0;reader._encoder_diagnostic={'row':{'steps':100,'length':10,'decoder':0}}
        reader._move=lambda *a,**kw:True;reader._pause=lambda t:clock.pause(clock.monotonic()+t)
        reader._stop_and_measure=lambda *a:289
        reader._rc_stable_uid=lambda *a:('53A78935930001',None)
        reader._is_single_uid=lambda *a:True
        reader._neighbour_occupied=lambda *a:True
        reader._known_neighbour_uid=lambda *a:''
        reader._whose_card=lambda *a:(None,False)
        reader._insert_result=lambda kind:{'kind':kind,'parked':reader._insert_checked_park_confirmed}
        calls=[];reader._rewind_scan_origin=lambda *a:calls.append('rewind')
        result=run(reader,0,1,lambda x:None,start_depth=390,net_target=390,sweep_mm=700)
        self.assertEqual(calls,['rewind']);self.assertEqual(result,{'kind':'unread','parked':False})
    def test_attribution_requires_two_independent_uid_observations(self):
        env={'MotionUncertain':Uncertain,'START_PROBE_MM':10,'SEARCH_MODE':0,
             'RESTORE_MODE':1,'MAX_ENCODER_RESIDUAL_MM':2,'logging':logging}
        run=load_method(ROOT/'ace_rc522.py','_rotation_test',env)
        reader=Reader(0,1);clock=Clock();reader.ace=types.SimpleNamespace(reactor=clock)
        reader._encoder_ledger={(0,1):0};reader._motion_uncertain=None
        reader._neighbour_signature=lambda *a:('ready',55,4,4,True)
        observed=[]
        def uid(*a):
            observed.append(clock.monotonic());clock.t+=1.4
            return '53A78935930001'
        reader._observed_expected_uid=uid;reader._observed_uid=lambda *a:''
        def move(idx,slot,length,mode,respond):
            reader._encoder_ledger[(idx,slot)]+=length if mode==0 else -length
            return True
        reader._move_and_confirm=move;reader._pause=lambda seconds:clock.pause(clock.monotonic()+seconds)
        self.assertEqual(run(reader,0,1,'53A78935930001',lambda x:None),'ours')
        self.assertEqual(len(observed),8)
        reader._observed_expected_uid=lambda *a:'53A78935930002'
        reader._move_and_confirm=lambda *a:(_ for _ in ()).throw(AssertionError('unexpected motion'))
        self.assertIsNone(run(reader,0,1,'53A78935930001',lambda x:None))
    def test_positive_negative_native(self):
        for sign in (1,-1):
            seq=[('feeding',0,False,1,0),('feeding',sign*21,True,200,20),('feeding',sign*25,True,250,25)]+[('ready',sign*25,True,250,25)]*3
            lane=Native(seq);self.assertEqual(lane._insert_verified_abort(0,1,lane._serials[0],lambda:True),25);self.assertEqual(lane.stops,1)
    def test_old_counter_is_not_fresh(self):
        lane=Native([('feeding',-800,True,10000,800)]*5+[('rollback',500,True,5000,400)]+[('ready',500,True,5000,400)]*3)
        self.assertIsNone(lane._insert_verified_abort(0,1,lane._serials[0],lambda:True));self.assertEqual(lane.stops,0)
    def test_no_observation_is_not_complete(self):
        lane=Native([('ready',0,False,0,0)]*3)
        with self.assertRaises(RuntimeError):lane._insert_verified_abort(0,1,lane._serials[0],lambda:True)
    def test_failed_stop_never_returns_a_depth(self):
        seq=[('feeding',0,False,1,0),('feeding',-21,True,200,20),('feeding',-25,True,250,25)]
        lane=Native(seq);lane._insert_stop_bound=lambda *a:False
        with self.assertRaisesRegex(RuntimeError,'stop unconfirmed'):
            lane._insert_verified_abort(0,1,lane._serials[0],lambda:True)
    def test_midprint_stop_rechecks_delayed_motion_and_unknown_baseline(self):
        seq=[('preloading',-10,True,120,10),('preloading',-14,True,170,14)]+[('preloading',-14,True,170,14)]*7
        lane=Native(seq)
        result=lane._insert_stop_unknown(0,1)
        self.assertEqual(result['kind'],'relative_unknown')
        self.assertEqual(result['baseline'],(-14,True,170,14))
        self.assertGreaterEqual(lane.stops,3)
    def test_midprint_zero_encoder_or_stop_failure_never_confirms(self):
        lane=Native([('preloading',0,False,0,0)]*20)
        with self.assertRaisesRegex(RuntimeError,'standstill'):lane._insert_stop_unknown(0,1)
        lane=Native([]);lane._insert_stop_bound=lambda *args:False
        with self.assertRaisesRegex(RuntimeError,'stop unconfirmed'):lane._insert_stop_unknown(0,1)
    def test_native_identity_once_rejects_uid_sentinel_and_stale_owner(self):
        lane=Native([]);transport=lane._serials[0];lane._insert_epochs={(0,1):4};stored=[]
        lane._insert_native_pending={(0,1):(transport,lane._location_identity(0),4)}
        lane._native_tag_format=lambda sku:''
        lane._v2_store_filament_read=lambda *a,**kw:stored.append((a,kw))
        version=[101];calls=[]
        def send(idx,req,cb):
            calls.append(req['method'])
            if req['method']=='get_status':
                result={'_status_wire_valid':True,'slots':[{'index':1,'_wire_valid':True,'slot_status':'ready','rfid':2}]};cmd=6
            else:
                result={'index':1,'code':0,'sku':'AHPLVO-106','type':'PLA','color':[255,127,50],'tag':{'field2':version[0]}};cmd=13
            cb(response={'code':0,'_v2_command':cmd,'_v2_frame_valid':True,'_v2_payload_valid':True,'result':result})
        lane.send_request_to=send
        self.assertTrue(lane._insert_native_once(0,1,transport,lambda:True))
        self.assertEqual(calls,['get_status','get_filament_info','get_status']);self.assertEqual(len(stored),1)
        version[0]=0x0102;calls.clear();stored.clear()
        self.assertFalse(lane._insert_native_once(0,1,transport,lambda:True));self.assertFalse(stored)
        lane._insert_native_pending={}
        self.assertFalse(lane._insert_native_once(0,1,transport,lambda:True));self.assertFalse(stored)
        lane._insert_native_pending={(0,1):(transport,lane._location_identity(0),4)}
        self.assertFalse(lane._insert_native_once(0,1,object(),lambda:False))
    def test_fresh_lane_checks_wire_presence_and_transport(self):
        lane=Native([]);transport=lane._serials[0]
        def send(idx,request,callback):
            method=request['method'];cmd=6 if method=='get_status' else 76
            result=({'_status_wire_valid':True,'slots':[{'index':1,'_wire_valid':True,'slot_status':'feeding'}]}
                    if cmd==6 else {'_feed_wire_valid':True,'feed_info':[{'index':1,'_wire_valid':True,'_decoder_present':True,'decoder':(1<<64)-25,'steps':300,'length':25}]})
            callback(response={'code':0,'_v2_command':cmd,'_v2_frame_valid':True,'_v2_payload_valid':True,'result':result})
        lane.send_request_to=send
        self.assertEqual(lane._insert_fresh_lane_actual(0,1,transport,lambda:True),('feeding',-25,True,300,25))
        with self.assertRaisesRegex(RuntimeError,'ownership'):
            lane._insert_fresh_lane_actual(0,1,object(),lambda:True)
        def bad(idx,request,callback):
            callback(response={'code':0,'_v2_command':6,'_v2_frame_valid':False,'_v2_payload_valid':True,'result':{}})
        lane.send_request_to=bad
        self.assertIsNone(lane._insert_fresh_lane_actual(0,1,transport,lambda:True))
        count=[0]
        def phase_change(idx,request,callback):
            method=request['method'];cmd=6 if method=='get_status' else 76
            if cmd==6:
                count[0]+=1
                result={'_status_wire_valid':True,'slots':[{'index':1,'_wire_valid':True,'slot_status':'feeding' if count[0]==1 else 'rollback'}]}
            else:
                result={'_feed_wire_valid':True,'feed_info':[{'index':1,'_wire_valid':True,'_decoder_present':True,'decoder':25,'steps':300,'length':25}]}
            callback(response={'code':0,'_v2_command':cmd,'_v2_frame_valid':True,'_v2_payload_valid':True,'result':result})
        lane.send_request_to=phase_change
        self.assertIsNone(lane._insert_fresh_lane_actual(0,1,transport,lambda:True))
    def test_explicit_rollback_then_ready_means_completed_not_a_depth(self):
        lane=Native([('rollback',40,True,100,9)]+[('ready',42,True,100,9)]*3)
        self.assertIsNone(lane._insert_verified_abort(0,1,lane._serials[0],lambda:True))
    def test_preloading_does_not_cause_fault(self):
        seq=[('preloading',0,False,1,0),('preloading',21,True,200,20),('preloading',25,True,250,25)]+[('ready',25,True,250,25)]*3
        lane=Native(seq);lane._insert_encoder_direction={(0,1):(lane._serials[0],lane._location_identity(0),1)}
        self.assertEqual(lane._insert_verified_abort(0,1,lane._serials[0],lambda:True),25)
    def test_unknown_preloading_waits_until_completed(self):
        seq=[('preloading',-25,True,250,25)]*42+[('ready',40,True,400,30)]*4
        lane=Native(seq)
        self.assertIsNone(lane._insert_verified_abort(0,1,lane._serials[0],lambda:True));self.assertEqual(lane.stops,0)
    def test_sweep_normalizes_only_learned_direction(self):
        for raw,direction in ((25,1),(-25,-1)):
            self.assertEqual(Reader(raw,direction)._stop_and_measure(0,1,40,lambda x:None),25)
    def test_sweep_wrong_zero_unknown_or_stop_failure(self):
        for raw,direction,ok in ((-25,1,True),(0,1,True),(-25,None,True),(25,1,False)):
            with self.subTest(raw=raw,direction=direction,ok=ok):
                with self.assertRaises(Uncertain):Reader(raw,direction,ok)._stop_and_measure(0,1,40,lambda x:None)
    def test_stop_retry_only_when_first_ack_missing(self):
        reader=Reader(-25,-1);answers=[None,{'code':0,'_v2_command':9,'_v2_frame_valid':True,'_v2_payload_valid':True}]
        reader._req=lambda *args:answers.pop(0)
        self.assertEqual(reader._stop_and_measure(0,1,40,lambda x:None),25)
        self.assertEqual(answers,[])
    def test_proven_subresolution_zero_never_claims_physical_zero(self):
        transport=object();reader=Reader(0,-1);reader._scan_measured=True
        reader._sweep_identity=('port',(1,2,3))
        reader.ace=types.SimpleNamespace(_serials={0:transport},_location_identity=lambda idx:reader._sweep_identity)
        reader._insert_checked_context=(0,1,transport);reader._scan_motor_pending=(0,287)
        reader._scan_motor_position=413;reader._scan_motor_total=413;reader._scan_zero_segments=0
        reader._sweep_reset_seen=True
        reader._sweep_baseline={'frame':True,'payload':True,'cmd':76,'code':0,'feed_wire':True,
                                'row':{'_wire_valid':True,'_decoder_present':True,'decoder':(1<<64)-289}}
        def zero(*args):
            reader._encoder_diagnostic={'row':{'_wire_valid':True,'_decoder_present':False,'decoder':0,'steps':7,'length':0}}
            return 0
        reader._fresh_encoder=zero
        self.assertEqual(reader._stop_and_measure(0,1,287,lambda x:None),0)
        self.assertEqual(reader._scan_motor_position,413);self.assertEqual(reader._scan_zero_segments,1)
    def test_zero_without_confirmed_baseline_is_not_motion_evidence(self):
        transport=object();reader=Reader(0,-1);reader._scan_measured=True
        reader._sweep_identity=('port',(1,2,3))
        reader.ace=types.SimpleNamespace(_serials={0:transport},_location_identity=lambda idx:reader._sweep_identity)
        reader._insert_checked_context=(0,1,transport);reader._scan_motor_pending=(0,287)
        reader._scan_motor_position=413;reader._scan_motor_total=413;reader._scan_zero_segments=0
        reader._sweep_reset_seen=True;reader._sweep_baseline={'row':{}}
        reader._fresh_encoder=lambda *args:0
        with self.assertRaises(Uncertain):reader._stop_and_measure(0,1,287,lambda x:None)
    def test_subresolution_zero_rejects_invalid_frame_reset_or_steps(self):
        for failure in ('bad_baseline_frame','no_reset','bad_row','too_many_steps'):
            with self.subTest(failure=failure):
                transport=object();reader=Reader(0,-1);reader._scan_measured=True
                reader._sweep_identity=('port',(1,2,3))
                reader.ace=types.SimpleNamespace(_serials={0:transport},_location_identity=lambda idx:reader._sweep_identity);reader._insert_checked_context=(0,1,transport)
                reader._scan_motor_pending=(0,287);reader._scan_motor_position=413
                reader._scan_motor_total=413;reader._scan_zero_segments=0
                reader._sweep_reset_seen=failure!='no_reset'
                reader._sweep_baseline={'frame':failure!='bad_baseline_frame','payload':True,'cmd':76,'code':0,'feed_wire':True,
                                        'row':{'_wire_valid':True,'_decoder_present':True,'decoder':(1<<64)-289}}
                def zero(*args):
                    reader._encoder_diagnostic={'row':{'_wire_valid':failure!='bad_row','_decoder_present':False,
                                                       'decoder':0,'steps':8 if failure=='too_many_steps' else 7,'length':0}}
                    return 0
                reader._fresh_encoder=zero
                with self.assertRaises(Uncertain):reader._stop_and_measure(0,1,287,lambda x:None)
    def test_natural_sweep_requires_stable_command_length(self):
        reader=Reader(-450,-1)
        def fresh(*args):
            reader._encoder_diagnostic={'row':{'_decoder_present':True,'steps':8600,'length':695}}
            return -450
        reader._fresh_encoder=fresh
        self.assertEqual(reader._natural_sweep_terminal(0,1,700),-450)
        def early(*args):
            reader._encoder_diagnostic={'row':{'_decoder_present':True,'steps':4960,'length':401}}
            return -450
        reader._fresh_encoder=early
        with self.assertRaises(Uncertain):reader._natural_sweep_terminal(0,1,700)
    def test_sweep_owner_loss_is_checked_after_stop(self):
        reader=Reader(-25,-1); calls=[]
        reader._req=lambda *args:(calls.append('STOP') or {'code':0,'_v2_command':9,'_v2_frame_valid':True,'_v2_payload_valid':True})
        reader._insert_guard=lambda:(_ for _ in ()).throw(Uncertain('owner changed'))
        with self.assertRaises(Uncertain):reader._stop_and_measure(0,1,40,lambda x:None)
        self.assertEqual(calls,['STOP'])
    def test_early_tag_uses_reset_and_stable_terminal_to_learn(self):
        reader=Reader(-25,None);reader._encoder_direction={};reader._encoder_ledger={};reader._relative_unknown=True
        reader._sweep_baseline={'row':{'steps':1000,'length':80}}
        reader._sweep_reset_seen=True;reader._sweep_values=[-4]
        saved=[];reader._remember_encoder_direction=lambda idx,slot,d:saved.append(d)
        self.assertEqual(reader._stop_and_measure(0,1,40,lambda x:None),25)
        self.assertEqual(saved,[-1]);self.assertEqual(reader._encoder_ledger[(0,1)],-25)
    def test_early_stop_reclaims_only_proven_unused_motor_command(self):
        reader=Reader(-25,-1);reader._scan_measured=True;reader._scan_motor_position=0
        transport=object();reader.ace=types.SimpleNamespace(_serials={0:transport})
        reader._insert_checked_context=(0,1,transport);reader._scan_motor_pending=(0,40)
        reader._scan_motor_total=0;reader._scan_zero_segments=0
        reader._sweep_reset_seen=True
        self.assertEqual(reader._stop_and_measure(0,1,40,lambda x:None),25)
        self.assertEqual(reader._scan_motor_position,25)
    def test_normalized_sweep_depth_selects_correction_mode(self):
        for raw,direction in ((25,1),(-25,-1)):
            reader=Reader(raw,direction);reader._insert_checked_guard=lambda:True;calls=[]
            reader._move_and_confirm=lambda idx,slot,length,mode,respond:(calls.append((length,mode)) or True)
            depth=100+reader._stop_and_measure(0,1,40,lambda x:None)
            reader._correct_to(0,1,90-depth,lambda x:None)
            reader._correct_to(0,1,200-depth,lambda x:None)
            self.assertEqual(calls,[(35,1),(75,0)])
    def test_unknown_relative_correction_uses_raw_measured_ledger(self):
        for polarity in (1,-1):
            reader=Reader(0,polarity);reader._relative_unknown=True
            reader._encoder_ledger={(0,1):polarity*35};calls=[]
            def move(idx,slot,length,mode,respond):
                calls.append((length,mode));reader._encoder_ledger[(idx,slot)]-=polarity*length
                return True
            reader._move_and_confirm=move
            reader._correct_to(0,1,999,lambda x:None)
            self.assertEqual(calls,[(35,1)]);self.assertEqual(reader._encoder_ledger[(0,1)],0)
    def test_second_pass_rewinds_actual_ledger_in_both_polarities(self):
        for polarity in (1,-1):
            reader=Reader(0,polarity);reader._encoder_ledger={(0,1):polarity*650};calls=[]
            def move(idx,slot,length,mode,respond):
                calls.append((length,mode));reader._encoder_ledger[(idx,slot)]-=polarity*length
                return True
            reader._move_and_confirm=move
            reader._rewind_scan_origin(0,1,lambda x:None)
            self.assertEqual(calls,[(650,1)]);self.assertEqual(reader._encoder_ledger[(0,1)],0)
    def test_second_pass_refuses_unconfirmed_direction(self):
        reader=Reader(0,None);reader._encoder_ledger={(0,1):650}
        with self.assertRaises(Uncertain):reader._rewind_scan_origin(0,1,lambda x:None)
    def test_nested_probe_cannot_cross_700_from_scan_origin(self):
        reader=Reader(0,-1);reader._scan_measured=True;reader._relative_measured=True
        reader._insert_checked_context=(0,1,object());reader._scan_forward_limit=700;reader._scan_motor_position=650
        reader._scan_motor_pending=None;reader._scan_motor_total=650;reader._scan_motor_total_limit=5600
        reader._scan_attempts=0;reader._scan_zero_segments=0
        reader._encoder_ledger={(0,1):-650}
        with self.assertRaisesRegex(Budget,'forward bound'):
            reader._move_actual(0,1,51,0,lambda x:None)
    def test_confirmed_motor_roundtrip_restores_forward_headroom(self):
        reader=Reader(0,1);reader._scan_measured=True;reader._relative_measured=True
        reader._insert_checked_context=(0,1,object());reader._scan_forward_limit=700
        reader._scan_motor_position=413;reader._scan_motor_pending=None;reader._scan_motor_total=413
        reader._scan_motor_total_limit=5600;reader._scan_attempts=0;reader._scan_zero_segments=0
        reader._motion_ledger={};reader._motion_uncertain=None
        for mode in (0,1,0):
            self.assertTrue(reader._move_actual(0,1,160,mode,lambda x:None))
            reader._commit_scan_motor(0,1,mode,160,160,True)
        self.assertEqual(reader._scan_motor_position,573)
    def test_unconfirmed_return_cannot_reclaim_forward_headroom(self):
        reader=Reader(0,1);reader._scan_measured=True;reader._relative_measured=True
        reader._insert_checked_context=(0,1,object());reader._scan_forward_limit=700
        reader._scan_motor_position=573;reader._scan_motor_pending=(1,160);reader._scan_motor_total=573
        reader._scan_motor_total_limit=5600;reader._scan_attempts=1;reader._scan_zero_segments=0
        with self.assertRaises(Uncertain):reader._commit_scan_motor(0,1,1,160,160,False)
        self.assertEqual(reader._scan_motor_position,573)
        with self.assertRaises(Uncertain):reader._move_actual(0,1,160,0,lambda x:None)
    def test_unsent_budget_refusal_rewinds_without_fault(self):
        reader=Reader(0,1);faults=[];rewinds=[]
        printer=types.SimpleNamespace(lookup_object=lambda *args:object())
        reader.ace=types.SimpleNamespace(_serials={0:object()},_insert_epochs={(0,1):1},printer=printer,
                                         _active_device_index=0,_latch_stop_fault=lambda *a:faults.append(a))
        reader.read_slot_transport=lambda *a,**kw:(_ for _ in ()).throw(Budget('bound'))
        reader._rewind_scan_origin=lambda *a:rewinds.append(a)
        result=reader._checked_actual(0,1,lambda x:None,lambda:True)
        self.assertEqual(result['kind'],'unread');self.assertFalse(result['parked'])
        self.assertEqual(len(rewinds),1);self.assertEqual(faults,[])
    def test_single_read_unknown_does_not_bind_or_retry(self):
        reader=Reader(0,1);transport=object();identity=('port',(1,2,3));calls=[]
        printer=types.SimpleNamespace(lookup_object=lambda *args:object())
        reader.ace=types.SimpleNamespace(_serials={0:transport},_insert_epochs={(0,1):4,(0,0):8},printer=printer,
                 _active_device_index=0,_location_identity=lambda idx:identity,_latch_stop_fault=lambda *a:calls.append('fault'))
        reader._target_motion_allowed=lambda *a:True;reader._neighbour_signature=lambda *a:('ready',1,2,3,True)
        reader._rc=lambda *a:(calls.append('select') or 0)
        reader._current_single_uid=lambda *a:(calls.append('uid') or 'AABBCCDD')
        reader._read_bambu_classic=lambda *a,**kw:(calls.append(('content',kw.get('single'))) or {'type':'PLA'})
        reader._ingest_bambu=lambda *a:calls.append('bind')
        reader._rewind_scan_origin=lambda *a:calls.append('rewind')
        result=reader._single_actual(0,1,lambda x:None,lambda:True,start_depth=None)
        self.assertEqual(result['kind'],'unread')
        self.assertEqual(result['candidate_uid'],'AABBCCDD')
        self.assertEqual(calls,['select','uid',('content',True),'rewind'])
    def test_single_seven_byte_uid_and_content_are_read_once(self):
        reader=Reader(0,1);transport=object();identity=('port',(1,2,3));calls=[]
        printer=types.SimpleNamespace(lookup_object=lambda *args:object())
        reader.ace=types.SimpleNamespace(_serials={0:transport},_insert_epochs={(0,1):4,(0,0):8},printer=printer,
                 _active_device_index=0,_location_identity=lambda idx:identity,_latch_stop_fault=lambda *a:calls.append('fault'))
        reader._target_motion_allowed=lambda *a:True;reader._neighbour_signature=lambda *a:None
        reader._rc=lambda *a:(calls.append('select') or 0)
        reader._current_single_uid=lambda *a:''
        reader._rc_read_uid=lambda *a:(calls.append('uid7') or '53A78935930001')
        reader._read_openspool=lambda *a:(calls.append('content') or {'material':'PLA'})
        reader._rewind_scan_origin=lambda *a:calls.append('rewind')
        result=reader._single_actual(0,1,lambda x:None,lambda:True,start_depth=None)
        self.assertEqual(result['kind'],'unread');self.assertEqual(calls,['select','uid7','content','rewind'])
    def test_single_read_binds_only_verified_distinct_neighbour(self):
        reader=Reader(0,1);transport=object();identity=('port',(1,2,3));calls=[]
        printer=types.SimpleNamespace(lookup_object=lambda *args:object())
        reader.ace=types.SimpleNamespace(_serials={0:transport},_insert_epochs={(0,1):4,(0,0):8},printer=printer,
                 _active_device_index=0,_location_identity=lambda idx:identity,_latch_stop_fault=lambda *a:calls.append('fault'),
                 _single_verified_uid={(0,0):(transport,identity,8,'11223344')})
        reader._target_motion_allowed=lambda *a:True;reader._neighbour_signature=lambda *a:('ready',1,2,3,True)
        reader._rc=lambda *a:0;reader._current_single_uid=lambda *a:'AABBCCDD'
        reader._read_bambu_classic=lambda *a,**kw:{'type':'PLA'}
        reader._ingest_bambu=lambda *a:calls.append('bind');reader._rewind_scan_origin=lambda *a:None
        result=reader._single_actual(0,1,lambda x:None,lambda:True,start_depth=None)
        self.assertEqual(result['kind'],'success');self.assertEqual(calls,['bind'])
    def test_single_initial_unknown_or_collision_never_moves(self):
        for status in (None,2):
            reader=Reader(0,1);transport=object();identity=('port',(1,2,3));calls=[]
            printer=types.SimpleNamespace(lookup_object=lambda *args:object())
            reader.ace=types.SimpleNamespace(_serials={0:transport},_insert_epochs={(0,1):4},printer=printer,
                     _active_device_index=0,_location_identity=lambda idx:identity,_latch_stop_fault=lambda *a:calls.append('fault'))
            reader._target_motion_allowed=lambda *a:True;reader._neighbour_signature=lambda *a:None
            reader._rc=lambda *a:status
            reader._move=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('unexpected motion'))
            result=reader._single_actual(0,1,lambda x:None,lambda:True,start_depth=None)
            self.assertEqual(result['kind'],'unread');self.assertEqual(calls,[])
    def test_single_mid_sweep_collision_stops_and_returns(self):
        reader=Reader(0,1);transport=object();identity=('port',(1,2,3));calls=[];clock=Clock()
        printer=types.SimpleNamespace(lookup_object=lambda *args:object())
        reader.ace=types.SimpleNamespace(_serials={0:transport},_insert_epochs={(0,1):4},printer=printer,reactor=clock,
                 _active_device_index=0,_location_identity=lambda idx:identity,_latch_stop_fault=lambda *a:calls.append('fault'))
        reader._target_motion_allowed=lambda *a:True;reader._neighbour_signature=lambda *a:None
        statuses=iter([1,2]);reader._rc=lambda *a:next(statuses)
        reader._fresh_encoder=lambda *a:0;reader._encoder_diagnostic={'row':{'steps':0,'length':0,'decoder':0}}
        reader._move=lambda *a,**kw:(calls.append('move') or True)
        reader._pause=lambda seconds:clock.pause(clock.monotonic()+seconds)
        reader._stop_and_measure=lambda *a:(calls.append('stop') or 0)
        reader._rewind_scan_origin=lambda *a:calls.append('rewind')
        result=reader._single_actual(0,1,lambda x:None,lambda:True,start_depth=None)
        self.assertEqual(result['kind'],'unread');self.assertEqual(calls,['move','stop','rewind'])
    def test_single_lost_ack_stops_original_lane_and_latches(self):
        reader=Reader(0,1);transport=object();identity=('port',(1,2,3));calls=[];clock=Clock()
        printer=types.SimpleNamespace(lookup_object=lambda *args:object())
        reader.ace=types.SimpleNamespace(_serials={0:transport},_insert_epochs={(0,1):4},printer=printer,reactor=clock,
                 _active_device_index=0,_location_identity=lambda idx:identity,_latch_stop_fault=lambda *a:calls.append('fault'))
        reader._target_motion_allowed=lambda *a:True;reader._neighbour_signature=lambda *a:None
        reader._rc=lambda *a:1;reader._fresh_encoder=lambda *a:0;reader._encoder_diagnostic={'row':{}}
        reader._move=lambda *a,**kw:(_ for _ in ()).throw(Uncertain('ACK lost'))
        reader._req=lambda idx,method,params:(calls.append((method,idx,params['index'])) or
                 {'code':0,'_v2_command':9,'_v2_frame_valid':True,'_v2_payload_valid':True})
        result=reader._single_actual(0,1,lambda x:None,lambda:True,start_depth=None)
        self.assertEqual(result['kind'],'stop_unconfirmed')
        self.assertEqual(calls,[('stop_feed_filament',0,1),'fault'])
    def test_single_rewind_ack_loss_also_stops_and_latches(self):
        reader=Reader(0,1);transport=object();identity=('port',(1,2,3));calls=[]
        printer=types.SimpleNamespace(lookup_object=lambda *args:object())
        reader.ace=types.SimpleNamespace(_serials={0:transport},_insert_epochs={(0,1):4},printer=printer,
                 _active_device_index=0,_location_identity=lambda idx:identity,_latch_stop_fault=lambda *a:calls.append('fault'))
        reader._target_motion_allowed=lambda *a:True;reader._neighbour_signature=lambda *a:None
        reader._rc=lambda *a:0;reader._current_single_uid=lambda *a:'';reader._rc_read_uid=lambda *a:''
        def rewind(*args):
            reader._single_pending_stop=(0,1)
            raise Uncertain('return ACK lost')
        reader._rewind_scan_origin=rewind
        reader._req=lambda idx,method,params:(calls.append((method,idx,params['index'])) or
                 {'code':0,'_v2_command':9,'_v2_frame_valid':True,'_v2_payload_valid':True})
        result=reader._single_actual(0,1,lambda x:None,lambda:True,start_depth=None)
        self.assertEqual(result['kind'],'stop_unconfirmed')
        self.assertEqual(calls,[('stop_feed_filament',0,1),'fault'])
    def test_park_converges_when_encoder_moves_seventy_percent(self):
        reader=Reader(0,-1);reader._scan_measured=True;reader._scan_origin_depth=0;reader._scan_target_depth=700
        reader._encoder_ledger={(0,1):0};reader._insert_checked_guard=lambda:True;calls=[]
        def move(idx,slot,length,mode,respond):
            calls.append(length);reader._encoder_ledger[(idx,slot)]-=max(1,round(length*.7))
            return True
        reader._move_and_confirm=move
        reader._correct_to(0,1,700,lambda x:None)
        self.assertLessEqual(abs(700+reader._encoder_ledger[(0,1)]),2)
        self.assertGreater(len(calls),4)
    def test_stock_correction_uses_measured_not_commanded_scan(self):
        reader=Reader(0,-1);reader._scan_measured=True;reader._scan_origin_depth=100;reader._scan_target_depth=390
        reader._encoder_ledger={(0,1):-150};reader._insert_checked_guard=lambda:True;calls=[]
        def move(idx,slot,length,mode,respond):
            calls.append((length,mode));reader._encoder_ledger[(idx,slot)]-=length
            return True
        reader._move_and_confirm=move
        reader._correct_to(0,1,-310,lambda x:None)
        self.assertEqual(calls,[(140,0)])
    def test_correction_preserves_raw_encoder_ledger_for_both_polarities(self):
        for direction in (1,-1):
            for mode in (0,1):
                reader=Reader(0,None);reader._encoder_direction={};reader._encoder_ledger={}
                reader._insert_checked_guard=lambda:True;reader._target_motion_allowed=lambda *a:True
                reader._move=lambda *a:True;remember=[]
                reader._remember_encoder_direction=lambda idx,slot,d:remember.append(d)
                seq=[(100,1000,80),(0,1,0),(direction*(-1 if mode else 1)*15,200,15)]
                def fresh(*args):
                    value,steps,length=seq.pop(0)
                    reader._encoder_diagnostic={'row':{'steps':steps,'length':length}}
                    return value
                reader._fresh_encoder=fresh
                self.assertTrue(reader._move_and_confirm(0,1,20,mode,lambda x:None))
                self.assertEqual(reader._encoder_ledger[(0,1)],direction*(-1 if mode else 1)*15)
                self.assertEqual(remember,[direction])

class InsertEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pkg=types.ModuleType('rfid_test_package');pkg.__path__=[]
        rc=types.ModuleType('rfid_test_package.ace_rc522')
        class StubReader:
            def __init__(self,ace):self._encoder_direction={};self._insert_checked_park_confirmed=True
            def read_slot_single_checked(self,*args,**kwargs):
                return {'kind':'success','parked':True,'uid':'a'}
        rc.AceTagReader=StubReader;sys.modules[pkg.__name__]=pkg;sys.modules[rc.__name__]=rc
        env={'__name__':'rfid_test_package.ace','__package__':'rfid_test_package',
             'logging':logging,'V2_ACTIVE_MOTION_STATES':('preloading','feeding','rollback'),
             'GATE_AVAILABLE':1,'INSERT_DEPTH_MAX':2000,'INSERT_PARK_NET':390,'INSERT_SWEEP_MM':700}
        cls.entry=staticmethod(load_method(ROOT/'ace.py','_insert_uid_read',env))
        cls.outer=staticmethod(load_method(ROOT/'ace.py','_insert_read_then_preload',env))
    def make_ace(self):
        class Printer:
            def lookup_object(self,*args):return types.SimpleNamespace(state='standby')
        class Ace:
            def __init__(self):
                self.printer=Printer();self._serials={0:object()};self._insert_epochs={(0,1):2}
                self._gate_status_per_ace={0:[1,1,1,1]};self._v2_filament_info_per_ace={0:{}}
                self._tag_read_busy=False;self._active_device_index=0;self.head_feed_length=[10]*4
                self._location_hold=types.SimpleNamespace(blocks_route=lambda **kw:False)
                self.preload_calls=0
            def _location_identity(self,idx):return ('port',(1,2,3))
            def _insert_fresh_lane(self,*a):return ('ready',-10,True,100,10)
            def _insert_native_once(self,*a):return False
            def _v2_slot_rfid(self,*a):return 0
            def _v2_get_slot_status(self,*a):return 'ready'
            def _v2_any_slot_active(self,*a):return False
            def _insert_drain_queue(self):pass
            def _disp(self,i):return i+1
            def log_always(self,*a):pass
            def _insert_guard(self,*a,**kw):return True
            def _insert_checked_preload(self,*a):self.preload_calls+=1;return {'kind':'preloaded'}
        return Ace()
    def test_normal_nonbusy_entry(self):
        ace=self.make_ace()
        result=self.entry(ace,0,1,20,guard=lambda:True)
        self.assertEqual(result['kind'],'success')
    def test_native_identity_fast_path_never_opens_reader_or_moves(self):
        ace=self.make_ace();ace._insert_native_once=lambda *a:True
        ace._insert_verified_abort=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('must not move'))
        result=self.entry(ace,0,1,'auto',guard=lambda:True)
        self.assertEqual(result,{'kind':'native','parked':True})
    def test_busy_relative_requeues_then_drains_without_stock_assumption(self):
        ace=self.make_ace();ace._tag_read_busy=True
        depth={'kind':'relative_unknown','transport':ace._serials[0],'identity':ace._location_identity(0),
               'epoch':1,'baseline':(-10,True,100,10)}
        result=self.entry(ace,0,1,depth,guard=lambda:True)
        self.assertEqual(result['kind'],'queued');queued=ace._insert_read_queue.pop()[2]
        self.assertEqual(queued['epoch'],2)
        ace._tag_read_busy=False;ace._insert_epochs[(0,1)]=3
        result=self.entry(ace,0,1,queued,guard=lambda:True)
        self.assertEqual(result['kind'],'success');self.assertTrue(result['relative_unknown'])
    def test_busy_new_insert_stops_immediately_and_never_starts_second_reader(self):
        ace=self.make_ace();ace._tag_read_busy=True;calls=[]
        def stop(idx,slot,guard=None):
            calls.append(('stop',idx,slot,guard is not None))
            return {'kind':'relative_unknown','transport':ace._serials[0],
                    'identity':ace._location_identity(0),'epoch':2,'baseline':(-10,True,100,10)}
        ace._insert_stop_unknown=stop
        ace._insert_verified_abort=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('natural wait forbidden'))
        result=self.entry(ace,0,1,'auto',guard=lambda:True)
        self.assertEqual(result['kind'],'queued')
        self.assertEqual(calls,[('stop',0,1,True)])
        self.assertTrue(ace._tag_read_busy)
        self.assertEqual(ace._insert_read_queue[0][2]['kind'],'relative_unknown')
    def test_relative_result_never_starts_stock_preload(self):
        ace=self.make_ace();ace._insert_uid_read=lambda *a,**kw:{'kind':'success','parked':True,'uid':'a','relative_unknown':True}
        result=self.outer(ace,0,1,{'kind':'relative_unknown'})
        self.assertTrue(result['relative_unknown']);self.assertEqual(ace.preload_calls,0)
    def test_midprint_duplicate_replaces_old_epoch_and_stop_failure_latches(self):
        method=load_method(ROOT/'ace.py','_insert_abort_and_queue',{'logging':logging})
        ace=self.make_ace();ace._is_v2_idx=lambda *a:True;ace._insert_bites_while_printing=lambda *a:True
        ace._ace_models={0:('','1.1.98O')};ace._insert_read_queue=[(0,1,{'epoch':0})]
        ace._insert_stop_unknown=lambda *a:{'kind':'relative_unknown','epoch':2}
        method(ace,0,1)
        self.assertEqual(len(ace._insert_read_queue),1);self.assertEqual(ace._insert_read_queue[0][2]['epoch'],2)
        faults=[];ace._latch_stop_fault=lambda *args:faults.append(args)
        ace._insert_stop_unknown=lambda *a:(_ for _ in ()).throw(RuntimeError('no stop'))
        method(ace,0,1)
        self.assertEqual(faults,[(0,1,'mid-print insert stop unconfirmed')])
    def test_reinserted_epoch_and_changed_transport_are_rejected(self):
        ace=self.make_ace()
        depth={'kind':'relative_unknown','transport':ace._serials[0],'identity':ace._location_identity(0),
               'epoch':0,'baseline':(-10,True,100,10)}
        with self.assertRaisesRegex(RuntimeError,'epoch'):
            self.entry(ace,0,1,depth,guard=lambda:True)
        depth['epoch']=1;depth['transport']=object()
        with self.assertRaisesRegex(RuntimeError,'identity'):
            self.entry(ace,0,1,depth,guard=lambda:True)
if __name__=='__main__':unittest.main()
