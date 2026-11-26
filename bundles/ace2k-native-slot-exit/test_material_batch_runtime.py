"""Test the real host batch route/ticket/callback path and full source assembly."""
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import types
import unittest
from unittest.mock import Mock
import subprocess
from contextlib import nullcontext

from material_batch_runtime import MaterialBatchRuntime
from patch_material_runtime import stage,patch_mount_map,patch_device_service,MODULES

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
FULL=HERE.parent/'full-system-1902'


class Reactor:
    def __init__(self):self.t=10.;self.callbacks=[]
    def monotonic(self):return self.t
    def pause(self,t):self.t=t
    def register_callback(self,f):self.callbacks.append(f)


class MaterialRuntimeTests(unittest.TestCase):
    def setUp(self):
        a=self.a=types.SimpleNamespace()
        self.r=a.reactor=Reactor()
        a._ace_devices=['usb'+str(n) for n in range(4)]
        a._serials={i:object() for i in range(4)}
        a._protocols={i:types.SimpleNamespace(NAME='ace2k') for i in range(4)}
        a._connected_per_ace={i:True for i in range(4)}
        a._unit_key=lambda i:'uid:unit'+str(i)
        a._ace_mode='multi';a.head_uses_ace=lambda h:True
        a._loading_heads=set();a._bg_staged={};a._bg_lanes_on=lambda i:set()
        a._head_source={i:None for i in range(4)}
        self.sensors={i:False for i in range(4)}
        a._head_sensor_present=lambda h:self.sensors[h]
        a._get_heads_for_ace_slot=lambda i,s:[h for h,src in a._head_source.items() if src and (src['ace_index'],src['slot'])==(i,s)]
        a._calibration_route_occupied=lambda:None
        self.print_state='standby'
        a.printer=types.SimpleNamespace(lookup_object=lambda *args:types.SimpleNamespace(get_status=lambda t:{'state':self.print_state}))
        self.sent=[];self.counters={(i,s):(10+i*4+s,30+s) for i in range(4) for s in range(4)}
        def send(idx,req,cb):
            self.sent.append((idx,req))
            slot=req['params']['index'];enc,fg=self.counters[idx,slot]
            cb(self=a,response={'code':0,'result':dict(index=slot,state='ready',present=True,encoder=enc,motor=fg,stamp=[self.r.t]*4)})
        a.send_request_to=send
        a.get_retract_speed=lambda i:60
        a.gcode=Mock();a.gcode.get_mutex.side_effect=lambda:nullcontext()
        n=self.n=types.SimpleNamespace(jobs={},mask=lambda i:0,cancel=Mock(return_value=True))
        self.started=[]
        def begin(p,speed):
            ident='native-'+str(len(self.started));self.started.append(p)
            n.jobs[ident]=types.SimpleNamespace(state='running',reason='')
            return ident
        n.begin_parked=begin
        self.runtime=MaterialBatchRuntime(a,n)
    def row(self,i,s):return dict(ace=i,slot=s,serial='uid:unit'+str(i))
    def preview(self,action,rows):return self.runtime.preview(dict(action=action,slots=rows))
    def start(self,preview,confirm=True):return self.runtime.start(dict(ticket=preview['ticket'],confirm_parked=confirm))
    def test_sixteen_actual_dispatch_requests_in_one_tick(self):
        p=self.preview('eject',[self.row(i,s) for i in range(4) for s in range(4)])
        self.assertTrue(p['needs_parking_confirmation']);self.assertEqual(len(self.sent),16)
        b=self.start(p);self.runtime.tick(10)
        self.assertEqual(len(self.started),16)
        self.assertEqual(len(self.runtime.status(b['batch'])),16)
        self.assertTrue(all(t['state']=='native' for t in self.runtime.status(b['batch'])))
        self.a.gcode.run_script_from_command.assert_not_called()
        self.n.jobs['native-3'].state='complete';self.runtime.tick(11)
        self.assertEqual(sum(t['state']=='complete' for t in self.runtime.status(b['batch'])),1)
    def test_no_confirmation_or_replayed_ticket_never_moves(self):
        p=self.preview('eject',[self.row(0,1)])
        with self.assertRaises(ValueError):self.start(p,False)
        with self.assertRaises(ValueError):self.start(p)
        self.runtime.tick(10);self.assertEqual(self.started,[])
    def test_changed_counter_or_connection_rejects_only_affected_bay(self):
        p=self.preview('eject',[self.row(0,0),self.row(0,1),self.row(1,0)])
        self.counters[0,0]=(999,999);self.a._serials[1]=object()
        b=self.start(p);self.runtime.tick(10)
        self.assertEqual([(x.ace,x.slot) for x in self.started],[(0,1)])
        self.assertEqual(sum(t['state']=='rejected' for t in self.runtime.status(b['batch'])),2)
    def test_head_eject_requires_real_source_then_second_physical_confirmation(self):
        self.sensors[0]=True;self.a._head_source[0]={'ace_index':2,'slot':0}
        p=self.preview('eject',[self.row(2,0),self.row(3,1)])
        b=self.start(p);self.runtime.tick(10)
        self.assertEqual([(x.ace,x.slot) for x in self.started],[(3,1)])
        def unload(cmd):
            self.assertEqual(cmd,'ACE_UNLOAD_HEAD HEAD=0 ACE=2 SLOT=0')
            self.sensors[0]=False;self.a._head_source[0]=None;self.a._last_unload_ok=True
        self.a.gcode.run_script_from_command.side_effect=unload
        self.r.callbacks.pop(0)(10)
        self.assertEqual(len(self.started),1)
        follow=self.runtime.parking_preview(b['batch'])
        self.assertTrue(follow['needs_parking_confirmation'])
        self.start(follow);self.runtime.tick(10)
        self.assertEqual([(x.ace,x.slot) for x in self.started],[(3,1),(2,0)])
    def test_loaded_head_source_does_not_follow_wrong_selected_ace(self):
        self.sensors[0]=True;self.a._head_source[0]={'ace_index':2,'slot':0}
        p=self.preview('unload',[self.row(1,0)])
        self.assertEqual(p['ticket'],'');self.assertEqual(p['slots'][0]['state'],'rejected')
        self.a.gcode.run_script_from_command.assert_not_called()
    def test_head_operations_serial_and_real_verdict(self):
        p=self.preview('load',[self.row(0,0),self.row(2,2)])
        b=self.start(p);self.runtime.tick(10);self.runtime.tick(10)
        self.assertEqual(len(self.r.callbacks),1)
        def load(cmd):
            self.assertEqual(cmd,'ACE_LOAD_HEAD HEAD=0 ACE=0 SLOT=0')
            self.a._last_load_ok=True;self.sensors[0]=True
            self.a._head_source[0]={'ace_index':0,'slot':0}
        self.a.gcode.run_script_from_command.side_effect=load
        self.r.callbacks.pop(0)(10);self.runtime.tick(10)
        self.assertEqual(len(self.r.callbacks),1)
        self.assertEqual(sum(t['state']=='complete' for t in self.runtime.status(b['batch'])),1)
    def test_stop_exact_task_and_no_fake_completion(self):
        b=self.start(self.preview('eject',[self.row(0,0),self.row(0,1)]))
        self.runtime.tick(10)
        first,second=self.runtime.status(b['batch'])
        self.assertTrue(self.runtime.stop(first['id'])['accepted'])
        self.n.cancel.assert_called_once_with('native-0')
        self.assertEqual(self.runtime.tasks[first['id']].state,'stopping')
        self.assertEqual(self.runtime.tasks[second['id']].state,'native')
    def test_printing_and_unknown_source_never_eject(self):
        self.sensors[0]=True
        self.assertEqual(self.preview('eject',[self.row(0,0)])['ticket'],'')
        self.print_state='printing'
        self.assertEqual(self.preview('eject',[self.row(0,1)])['ticket'],'')
    def test_printer_becomes_busy_stops_running_native_only(self):
        b=self.start(self.preview('eject',[self.row(0,0),self.row(0,1)]))
        self.runtime.tick(10)
        self.print_state='printing'
        self.runtime.tick(11)
        self.assertEqual([t['state'] for t in self.runtime.status(b['batch'])],['stopping','stopping'])
        self.assertEqual([x.args[0] for x in self.n.cancel.call_args_list],['native-0','native-1'])
        self.a.gcode.run_script_from_command.assert_not_called()
    def test_printer_becomes_busy_before_dispatch(self):
        b=self.start(self.preview('eject',[self.row(0,0)]))
        self.print_state='printing'
        self.runtime.tick(10)
        self.assertEqual(self.runtime.status(b['batch'])[0]['state'],'rejected')
        self.assertEqual(self.started,[])
    def test_preview_rejects_duplicates_and_identity_mismatch(self):
        with self.assertRaises(ValueError):self.preview('load',[self.row(0,0),self.row(1,0)])
        self.assertEqual(self.preview('eject',[dict(ace=0,slot=1,serial='uid:wrong')])['ticket'],'')
        self.assertEqual(self.started,[])


class ActualCompositionTests(unittest.TestCase):
    def test_device_service_real_route_forwarding(self):
        import ast
        service=ROOT/'overlays/233-native-ui/root/usr/local/lib/u1-device-service/device_service.py'
        src=patch_device_service(service.read_text())
        tree=ast.parse(src)
        branch=next(n for n in ast.walk(tree) if isinstance(n,ast.If)
                    and 'route.startswith(\'/material/batch/\')' in ast.unparse(n.test))
        scope={'self':types.SimpleNamespace(command='POST'),
               'route':'/material/batch/preview','data':{'action':'eject','slots':[]},
               'web':Mock(return_value={'ticket':'test','slots':[]})}
        code=compile(ast.Module(body=branch.body,type_ignores=[]),'device_service_batch','exec')
        exec(code,scope)
        scope['web'].assert_called_once_with('material/batch/preview',scope['data'],'POST')
        self.assertEqual(scope['result']['ticket'],'test')
        scope['route']='/material/batch/unrecognized'
        with self.assertRaises(ValueError):exec(code,scope)
    def test_full_staged_provider_and_mount_map(self):
        # Run against the exact original archive, then apply the 1902.7
        # source chain in its original order. NOT a synthetic isolated method.
        sys.path.insert(0,str(FULL))
        try:
            import compose
            from screen_compat import patch_engine,patch_web
            from filament_persistence import patch_persistence_engine,patch_persistence_web
            with tempfile.TemporaryDirectory() as td:
                work=Path(td)
                field=HERE.parent/'multiace-190/artifacts/multiace-1.90.2-233.20261126.tar.gz'
                self.assertEqual(compose.sha(field),compose.FIELD_SHA)
                with tarfile.open(field,'r:gz') as t:t.extractall(work,filter='data')
                provider=next(work.iterdir());engine=provider/'klipper/extras/ace.py';web=provider/'web/backend/main.py'
                engine.write_text(patch_engine(compose.patch_preload(engine.read_text())))
                web.write_text(patch_web(web.read_text()))
                for name in ('engine_routes.patch','web_routes.patch'):
                    subprocess.run(['patch','--batch','--fuzz=0','-p1','-i',str(FULL/name)],cwd=provider,check=True,capture_output=True)
                engine.write_text(patch_persistence_engine(engine.read_text()))
                web.write_text(patch_persistence_web(web.read_text()))
                stage(provider)
                self.assertIn("register_command('ACE_SLOT_EXIT'",engine.read_text())
                self.assertIn("'multiace/material_batch'",engine.read_text())
                self.assertIn('/api/material/batch/{operation}',web.read_text())
                extras=engine.parent
                for name in MODULES:
                    file=extras/(name+'.py')
                    self.assertTrue(file.is_file(),file)
                    compile(file.read_text(),str(file),'exec')
                common=ROOT/'overlays/mods/multiace/70-app-multiace/root/usr/local/share/multiace/common.sh'
                mapping=patch_mount_map(common.read_text())
                for name in MODULES:
                    self.assertIn('extras/%s.py extras/%s.py'%(name,name),mapping)
        finally:sys.path.remove(str(FULL))


if __name__=='__main__':unittest.main()
