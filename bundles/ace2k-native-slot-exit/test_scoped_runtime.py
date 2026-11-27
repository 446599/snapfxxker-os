import ast
import logging
from pathlib import Path
import tarfile
import tempfile
import types
import unittest
from unittest.mock import Mock
import os
from patch_scoped_runtime import patch_engine, patch_driver

HERE = Path(__file__).resolve().parent


def source(name):
    # CI uses the real, pinned field archive; local offline run may supply
    # exported exact production sources without copying field logs/configs.
    root = os.environ.get('U1_FIELD_EXPORT')
    if root:
        if name == 'ace.py':
            return (Path(root)/'provider-1902.7-route-files/klipper/extras'/name).read_text()
        return (Path(root)/'provider-field-unchanged/klipper/extras'/name).read_text()
    with tarfile.open(HERE.parent/'multiace-190/artifacts/multiace-1.90.2-233.20261126.tar.gz') as t:
        return t.extractfile('multiace-1.90.2-233.20261126/klipper/extras/'+name).read().decode()


class Reactor:
    def __init__(self): self.t=0.
    def monotonic(self): return self.t
    def pause(self,when): self.t=when


def err(*args,**kw): return RuntimeError(str(args))


def fail(*args): raise RuntimeError(args[-1])


def reply(slot, state='ready', ready=True):
    return {'code':0,'result':{'index':slot,'state':state,'move_admissible':ready}}


class ScopedRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s=patch_engine(source('ace.py'))
        d=patch_driver(source('ace_protocol_ace2k.py'))
        compile(d,'driver','exec')
        names={'_is_native_ace2k','wait_ace_motion_ready_on','cmd_ACE_START_DRYING','cmd_ACE_STOP_DRYING','cmd_ACE_DRY'}
        methods=[n for n in ast.walk(ast.parse(s)) if isinstance(n,ast.FunctionDef) and n.name in names]
        assert {n.name for n in methods}==names
        node=ast.ClassDef(name='Subject',bases=[],keywords=[],body=methods,decorator_list=[])
        ns={'logging':logging}; exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),'subject','exec'),ns)
        cls.subject=ns['Subject']; cls.text=s
    def setUp(self):
        a=self.a=self.subject(); a.reactor=Reactor()
        a._protocols={0:types.SimpleNamespace(NAME='ace2k'),1:types.SimpleNamespace(NAME='v2'),2:types.SimpleNamespace(NAME='ace2k')}
        a._serials={0:object(),1:object(),2:object()}; a._connected_per_ace={0:True,1:True,2:True}
        a.printer=types.SimpleNamespace(command_error=err); a._raise_ace_gone=Mock(side_effect=fail)
        a._disp=lambda n:n+1; a._t=lambda s,**k:s; a._ace_error=err
        a.wait_ace_ready_on=Mock(); a._wait_homing_clear=Mock(); a._tipform_send=Mock()
        a.send_request_to=Mock(); a._active_device_index=0; a._ace_devices=[0,1,2]
        a.max_dryer_temperature=65; a.dry_auto_roll=False; a.ace_dryer_temp={}; a.ace_dryer_duration={}
        a.dryer_temp=45; a.dryer_duration=120; a.gcode=Mock(); a._auto_dry_release=Mock(); a._close_dry_exhaust=Mock()
    def gcmd(self,**params):
        return types.SimpleNamespace(get_int=lambda k,default=None:params.get(k,default))
    def test_ready_lane_does_not_wait_for_other_lanes(self):
        self.a._tipform_send.return_value=reply(2)
        self.a.wait_ace_motion_ready_on(0,2)
        self.a.wait_ace_ready_on.assert_not_called()
        self.a._tipform_send.assert_called_once_with(0,{'method':'get_lane_state','params':{'index':2}},2.)
    def test_busy_target_waits_then_ready(self):
        self.a._tipform_send.side_effect=[reply(1,'feeding',False),reply(1)]
        self.a.wait_ace_motion_ready_on(0,1)
        self.assertGreater(self.a.reactor.t,0)
        self.a.wait_ace_ready_on.assert_not_called()
    def test_wrong_slot_timeout_no_unit_reconnect(self):
        self.a._tipform_send.return_value=reply(1)
        with self.assertRaisesRegex(RuntimeError,'motion not ready'):
            self.a.wait_ace_motion_ready_on(0,2,timeout=.5)
        self.a.wait_ace_ready_on.assert_not_called()
    def test_existing_fault_reaches_original_driver_recovery_only_if_lane_free(self):
        self.a._tipform_send.return_value=reply(2,'feed_error',True)
        self.a.wait_ace_motion_ready_on(0,2)
        self.a.wait_ace_ready_on.assert_not_called()
    def test_fault_on_owned_lane_cannot_bypass_motion_guard(self):
        self.a._tipform_send.return_value=reply(2,'feed_error',False)
        with self.assertRaisesRegex(RuntimeError,'motion not ready'):
            self.a.wait_ace_motion_ready_on(0,2,timeout=.5)
    def test_connection_replacement_during_wait_is_not_ready(self):
        def answer(*args):
            self.a._serials[0]=object()
            return reply(2)
        self.a._tipform_send.side_effect=answer
        with self.assertRaisesRegex(RuntimeError,'connection changed'):
            self.a.wait_ace_motion_ready_on(0,2)
    def test_legacy_wait_preserved(self):
        self.a.wait_ace_motion_ready_on(1,0)
        self.a.wait_ace_ready_on.assert_called_once_with(1,timeout=30.)
        self.a._tipform_send.assert_not_called()
    def test_native_dryer_targets_explicit_unit_without_motion_wait(self):
        a=self.a; a.cmd_ACE_START_DRYING(self.gcmd(ACE=2,TEMP=45,DURATION=90))
        args,kw=a.send_request_to.call_args
        self.assertEqual(args,(2,)); self.assertEqual(kw['request']['method'],'drying')
        self.assertEqual(a._active_device_index,0)
        a.wait_ace_ready_on.assert_not_called(); a._wait_homing_clear.assert_not_called()
        a.cmd_ACE_STOP_DRYING(self.gcmd(ACE=2))
        self.assertEqual(a.send_request_to.call_args.args[:2],(2,{'method':'drying_stop'}))
        a.wait_ace_ready_on.assert_not_called(); a._auto_dry_release.assert_called_once_with(2,'ACE_STOP_DRYING')
    def test_native_dry_gcode_never_switches_active_unit(self):
        self.a.cmd_ACE_DRY(self.gcmd(ACE=2,TEMP=45,DURATION=90))
        self.a.gcode.run_script_from_command.assert_called_once_with('ACE_START_DRYING ACE=2 TEMP=45 DURATION=90')
    def test_legacy_dryer_retains_unit_wait_and_switch(self):
        a=self.a; a.cmd_ACE_DRY(self.gcmd(ACE=1,TEMP=45,DURATION=90))
        self.assertEqual(a.gcode.run_script_from_command.call_args_list[0].args,('ACE_SWITCH TARGET=1',))
        a.cmd_ACE_START_DRYING(self.gcmd(ACE=1,TEMP=45,DURATION=90))
        a.wait_ace_ready_on.assert_called_once_with(1)
    def test_temperature_limits_retained(self):
        with self.assertRaises(RuntimeError): self.a.cmd_ACE_START_DRYING(self.gcmd(ACE=2,TEMP=99,DURATION=90))
        self.a.send_request_to.assert_not_called()
    def test_actual_feed_retract_call_sites_use_lane_wait(self):
        for name in ('_feed','_retract'):
            methods=[n for n in ast.walk(ast.parse(self.text)) if isinstance(n,ast.FunctionDef) and n.name==name]
            self.assertEqual(len(methods),1)
            self.assertIn('self.wait_ace_motion_ready_on(idx, index)',ast.unparse(methods[0]))

if __name__=='__main__': unittest.main()
