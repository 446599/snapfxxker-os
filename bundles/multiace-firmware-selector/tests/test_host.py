"""Offline regression tests for the packaged Klipper ACE firmware handoff."""
import ast
import logging
import os
from pathlib import Path
import time
import types
import unittest
from unittest.mock import Mock, patch

from packaged_runtime import runtime_root

SOURCE = runtime_root() / 'klipper/extras/ace.py'
KEY = 'ace__fw_port_protocols'
A = '/dev/serial/by-path/A'
B = '/dev/serial/by-path/B'
C = '/dev/serial/by-path/C'
ID_A = '/dev/serial/by-id/ACE-A'
REAL = {A: '/dev/ttyUSB0', ID_A: '/dev/ttyUSB0', B: '/dev/ttyUSB1',
        C: '/dev/ttyUSB2'}

class V1:
    NAME = 'v1'
    discover = staticmethod(lambda: [C])

class V2:
    NAME = 'v2'
    discover = staticmethod(lambda: [A, B])

class Ace2k:
    NAME = 'ace2k'
    discover = staticmethod(lambda: [ID_A])

namespace = dict(os=os, time=time, logging=logging, AceProtocolV1=V1,
                 AceProtocolV2=V2, AceProtocolAce2k=Ace2k, KNOWN_PROTOCOLS=(V1,Ace2k,V2))
tree = ast.parse(SOURCE.read_text())
cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'MultiAce')
names = {'_scan_ace_devices','_ace_path_sort_key','_fw_port_key','_fw_set_port_protocol',
         'cmd_ACE_FW_RESUME','cmd_ACE_FW_RELEASE','_is_v2','_is_v1','_is_ace2k'}
cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
exec(compile(ast.Module(body=[cls], type_ignores=[]), str(SOURCE), 'exec'), namespace)
Host = namespace['MultiAce']

class Command:
    def __init__(self, target=None, switch=0):
        self.target = target
        self.switch = switch
    def get_int(self, name, default=None, **options):
        return self.switch if name == "SWITCH" else 0
    def get(self, name, default=None):
        return self.target if name == 'PROTOCOL' else default

class Tests(unittest.TestCase):
    def setUp(self):
        for name, func in [('realpath', lambda p: REAL.get(p,p)),
                           ('exists', lambda p: p in set(REAL) | set(REAL.values()))]:
            p = patch.object(os.path, name, side_effect=func)
            p.start()
            self.addCleanup(p.stop)
        self.h=self.host()
        self.h._ace_canonical=[A,B,C]

    def host(self, saved=None):
        h = Host()
        h.save_variables=types.SimpleNamespace(allVariables=dict(saved or {}))
        h._usb_stats={'scans':0}
        h._usb_log=Mock()
        h._v2_order='usb'
        h._enable_ace_v2=h._enable_ace2k=True
        h._ace2k_ports_cfg=[ID_A]
        h._ace_canonical=None
        h._ace_devices=[A,B,C]
        h._ace_path_protocol={A:V2, B:V2, C:V1}
        h._protocols={0:V2()}
        h._fw_update_hold={0}
        h._v2_probe_after={0:20}
        h._connected_per_ace={}
        h._ace_error=lambda gcmd,msg,code: ValueError(msg)
        h.save_variable=lambda key,v,write: h.save_variables.allVariables.update({key:v})
        h._open_ace=Mock(return_value=True)
        h.log_always=Mock()
        h._disp=lambda i:i+1
        return h

    def test_switch_new_install_and_restart_with_static_peers(self):
        h=self.h
        h._ace2k_ports_cfg=[]
        h._enable_ace2k=False
        h.cmd_ACE_FW_RESUME(Command('ace2k'))
        self.assertIs(h._ace_path_protocol[A],Ace2k)
        self.assertIs(h._ace_path_protocol[B],V2)
        self.assertEqual(h.save_variables.allVariables[KEY],{A:'ace2k'})
        rest=self.host(h.save_variables.allVariables)
        rest._ace2k_ports_cfg=[]
        rest._enable_ace2k=False
        rest._scan_ace_devices()
        self.assertEqual(rest._ace_path_protocol,{A:Ace2k,B:V2,C:V1})

    def test_switch_to_stock_beats_static_ace2k_and_aliases(self):
        h=self.h
        h._protocols[0]=Ace2k()
        h._ace_path_protocol[A]=Ace2k
        h.save_variables.allVariables[KEY]={ID_A:'ace2k', B:'ace2k'}
        h._enable_ace_v2=False
        h.cmd_ACE_FW_RESUME(Command('v2'))
        self.assertEqual(h.save_variables.allVariables[KEY],{A:'v2',B:'ace2k'})
        h._ace_path_protocol.clear()
        self.assertCountEqual(h._scan_ace_devices(),[A,B,C])
        self.assertEqual(h._ace_path_protocol,{A:V2,B:Ace2k,C:V1})
        rest=self.host(h.save_variables.allVariables)
        rest._enable_ace_v2=False
        rest._ace_path_protocol.clear()
        self.assertCountEqual(rest._scan_ace_devices(),[A,B,C])
        self.assertEqual(rest._ace_path_protocol,{A:V2,B:Ace2k,C:V1})

    def test_failed_open_is_retryable(self):
        h=self.h
        h._open_ace.side_effect=[False,True]
        with self.assertRaisesRegex(ValueError, 'did not come back'):
            h.cmd_ACE_FW_RESUME(Command('ace2k'))
        self.assertEqual(h.save_variables.allVariables[KEY],{A:'ace2k'})
        h.cmd_ACE_FW_RESUME(Command('ace2k'))
        h._connected_per_ace[0]=True
        h._protocols[0]=Ace2k()
        h.cmd_ACE_FW_RESUME(Command('ace2k'))
        self.assertEqual(h._open_ace.call_count,2)

    def test_failed_persistence_preserves_hold_and_prior_map(self):
        h=self.h
        h.save_variables.allVariables[KEY]={A:'v2',B:'ace2k'}
        def fail(key,v,write):
            h.save_variables.allVariables[key] = v
            raise OSError('storage')
        h.save_variable=fail
        with self.assertRaises(OSError): h.cmd_ACE_FW_RESUME(Command('ace2k'))
        self.assertEqual(h.save_variables.allVariables[KEY],{A:'v2',B:'ace2k'})
        self.assertEqual(h._fw_update_hold,{0})
        self.assertIs(h._ace_path_protocol[A],V2)
        h._open_ace.assert_not_called()

    def test_legacy_resume_and_invalid_driver(self):
        h=self.h
        with self.assertRaises(ValueError): h.cmd_ACE_FW_RESUME(Command('v1'))
        self.assertEqual(h._fw_update_hold,{0})
        h.cmd_ACE_FW_RESUME(Command())
        self.assertNotIn(KEY,h.save_variables.allVariables)
        h._open_ace.assert_called_once_with(0)

    def test_unreleased_port_cannot_switch(self):
        h=self.h
        h._fw_update_hold.clear()
        with self.assertRaisesRegex(ValueError,'Release'): h.cmd_ACE_FW_RESUME(Command('ace2k'))
        h._open_ace.assert_not_called()

    def test_v1_cannot_switch_to_ace2k(self):
        h=self.h
        h._ace_path_protocol[A]=V1
        with self.assertRaisesRegex(ValueError,'ACE 2 Pro'): h.cmd_ACE_FW_RESUME(Command('ace2k'))
        self.assertEqual(h._fw_update_hold,{0})

    def test_canonical_path_is_retained_on_alias_scan(self):
        h=self.h
        h.save_variables.allVariables[KEY]={ID_A:'v2'}
        self.assertCountEqual(h._scan_ace_devices(),[A,B,C])
        self.assertIs(h._ace_path_protocol[A],V2)

    def test_readback_uses_only_live_matching_serial(self):
        source = ast.parse(SOURCE.read_text())
        open_ace = next(n for n in ast.walk(source) if isinstance(n, ast.FunctionDef)
                        and n.name == '_open_ace')
        callbacks = [n for n in open_ace.body if isinstance(n, ast.FunctionDef)
                     and n.name in {'info_callback', '_info_known'}]
        h=self.h
        h._fw_live_versions={}
        h._serials={0:object()}
        h._ace_models={0:('previous', 'old')}
        h._t=lambda key, **kwargs:key
        h.log_error=Mock()
        local=dict(namespace,idx=0,ser=h._serials[0],serial_path=A,self=h)
        exec(compile(ast.Module(body=callbacks,type_ignores=[]),str(SOURCE),'exec'),local)
        self.assertFalse(local['_info_known']())
        response={'msg':'success','result':{'model':'ACE2','firmware':'ace2k 0.16.0-233b'}}
        local['info_callback'](h,response)
        self.assertTrue(local['_info_known']())
        self.assertEqual(h._fw_live_versions,{0:'ace2k 0.16.0-233b'})
        h._serials[0]=object()
        h._fw_live_versions.clear()
        self.assertTrue(local['_info_known']()) # retired callback must exit
        local['info_callback'](h,response)
        self.assertFalse(h._fw_live_versions)
        local['ser']=h._serials[0]
        local['info_callback'](h,{'msg':'error','result':{'firmware':'stale'}})
        self.assertFalse(h._fw_live_versions)
        local['info_callback'](h,{'msg':'success','result':{'model':'ACE2','firmware':'V1.2.04O'}})
        self.assertEqual(h._fw_live_versions,{0:'V1.2.04O'})

    def test_cross_protocol_release_requires_stable_port_before_disconnect(self):
        h=self.h
        h._fw_update_hold.clear()
        h._ace_devices[0]='/dev/ttyUSB7'
        h._disconnect_from=Mock()
        with patch.object(os.path, 'isdir', return_value=False):
            with self.assertRaisesRegex(ValueError, 'stable USB port'):
                h.cmd_ACE_FW_RELEASE(Command(switch=1))
        h._disconnect_from.assert_not_called()
        self.assertFalse(h._fw_update_hold)

    def test_cross_protocol_release_refuses_gen1(self):
        h=self.h
        h._fw_update_hold.clear()
        h._protocols[0]=V1()
        h._disconnect_from=Mock()
        with self.assertRaisesRegex(ValueError, 'ACE 2 Pro'):
            h.cmd_ACE_FW_RELEASE(Command(switch=1))
        h._disconnect_from.assert_not_called()
        self.assertFalse(h._fw_update_hold)

    def test_ace2k_release(self):
        h=self.h
        h._protocols[0]=Ace2k()
        h._fw_update_hold=set()
        h._swap_in_progress=False
        h._transport_abandon={}
        h._path_operations=None
        h._path_calibration=None
        h._is_actively_printing=Mock(return_value=False)
        h._dry_cycles=Mock()
        h._dry_cycles.safe_for_maintenance.return_value=True
        h._disconnect_from=Mock()
        h._transport_retiring={}
        h._transport_opening={}
        h.reactor=Mock()
        h.reactor.monotonic.return_value=0
        namespace['ACE_OPEN_TIMEOUT']=5
        h.cmd_ACE_FW_RELEASE(Command())
        self.assertEqual(h._fw_update_hold,{0})
        h._disconnect_from.assert_called_once_with(0)

if __name__=='__main__':
    unittest.main()
