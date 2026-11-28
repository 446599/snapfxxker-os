"""Regression cases from the 1902.14 real-device handoff, synthetic IDs only."""
import copy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from acceptance_1916 import patch_engine, patch_package_manager

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parents[1]/'overlays/233-native-ui/root/usr/local/lib/u1-device-service'))
from idle_checks import require_machine_idle


def snapshot():
    dryer={'status':'stop','rotisserie':False}
    device=dict(idx=0,connected=True,status='ready',fw_hold=False,
                auto_dry_running=False,dryer_status=dryer,feed_assist=-1,
                slots=[{'status':'ready'}]+[{'status':'empty'}]*3)
    offline=dict(idx=1,connected=False,status='unknown',fw_hold=False,
                 auto_dry_running=False,dryer_status={},slots=[])
    ace=dict(status='ready',device_count=2,aces=[device,offline],
             dryer_status=dryer,swap_in_progress=False,unload_all_active=False,
             tag_op={'busy':False},native_exit_tasks=[],material_batch_tasks=[],
             material_motion_idle=True,calibration_unload={'active':False},
             calibration={'state':'idle','tip_position_mm':None,'verify_position_mm':None},
             head_source={str(n):None for n in range(4)})
    return {'ace':ace,'webhooks':{'state':'ready'},'print_stats':{'state':'standby'},
            'idle_timeout':{'state':'Idle'},'ace_bg_swap':{'busy':[]},
            'heaters':{'available_heaters':['extruder','heater_bed']},
            'extruder':{'target':0.,'power':0.},'heater_bed':{'target':0.,'power':0.},
            'filament_feed left':{'e0':{'channel_state':'wait_insert','channel_action_state':'none'}}}


class OtaAcceptance(unittest.TestCase):
    def test_new_status_contract_and_unconnected_spare(self):
        require_machine_idle(snapshot())

    def test_non_idle_new_schema(self):
        cases=(
            ('native_exit_tasks',[{'state':'recovery'}]),
            ('native_exit_tasks',[{'state':'stopping'}]),
            ('material_batch_tasks',[{'state':'awaiting_parking'}]),
            ('material_batch_tasks',[{'state':'head_running'}]),
            ('material_batch_tasks',[{'state':'recovery'}]),
            ('material_motion_idle',False),
            ('calibration',{'state':'failed','tip_position_mm':500}),
            ('calibration',{'state':'verified','verify_position_mm':100}),
            ('calibration_unload',{'active':True}),
            ('tag_op',{'busy':True}),
        )
        for key,value in cases:
            with self.subTest(key=key,value=value):
                s=snapshot()
                s['ace'][key]=value
                with self.assertRaises(ValueError):
                    require_machine_idle(s)

    def test_missing_new_telemetry_is_not_idle(self):
        for key in ('native_exit_tasks','material_batch_tasks','material_motion_idle'):
            s=snapshot();del s['ace'][key]
            with self.subTest(key=key),self.assertRaises(ValueError):
                require_machine_idle(s)

    def test_offline_dependency_or_heating_still_blocks(self):
        s=snapshot();s['ace']['head_source']['0']={'ace_index':1,'slot':0}
        with self.assertRaises(ValueError):
            require_machine_idle(s)
        s=snapshot();s['extruder']['target']=200
        with self.assertRaises(ValueError):
            require_machine_idle(s)

    def test_completed_tasks_and_verified_park_allow_ota(self):
        s=snapshot()
        s['ace']['native_exit_tasks']=[{'state':'complete'},{'state':'cancelled'}]
        s['ace']['material_batch_tasks']=[{'state':'rejected'},{'state':'complete'}]
        s['ace']['calibration']={'state':'verified','tip_position_mm':0,'verify_position_mm':0}
        require_machine_idle(s)


class EngineAcceptance(unittest.TestCase):
    def test_patch_targets_shared_body_and_checks_before_clear(self):
        sample="""class Engine:
    def get_status(self):
        return {
            'material_batch_tasks': self._material_batch.status() if hasattr(self,'_material_batch') else [],
        }
    def _cmd_ACE_UNLOAD_HEAD_body(self, gcmd):
        head = 0
        requested_ace = requested_slot = None
        retract_override = 0
        self._wait_bg_op(head, gcmd)
        unload_verified = True
        if unload_verified:
            self._head_source[head] = None

    cmd_ACE_TEST_help = ''
"""
        patched=patch_engine(sample)
        compile(patched,'ace.py','exec')
        self.assertLess(patched.index('head_park_plan('),patched.index('unload_verified ='))
        self.assertLess(patched.index('_fresh_park_sample('),patched.index('self._head_source[head] = None'))
        self.assertIn('material_motion_idle',patched)
