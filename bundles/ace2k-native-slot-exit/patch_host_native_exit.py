"""Stage actual engine ownership hooks and native worker recovery mask."""
from pathlib import Path
from patch_scoped_runtime import replace, patch_engine as scope_engine, patch_driver as scope_driver
from patch_scoped_rfid import patch_engine as scope_rfid
from patch_native_worker import stage as stage_worker
HERE=Path(__file__).resolve().parent


def patch_engine(s):
    # Init before any normal threaded request can be enqueued.
    s=replace(s,'        self._install_atomic_save_variables()\n',
        '''        self._install_atomic_save_variables()
        from .host_native_exit import HostNativeExit
        self._native_exit_host = HostNativeExit(self,
            os.path.join(self.paths['config_dir'], 'extended', 'multiace', 'native_exit_journal.json'))
''')
    s=replace(s,'    def _handle_ready(self):\n',
        '    def _handle_ready(self):\n        self._native_exit_host.install_timer()\n')
    s=replace(s,'    def _handle_disconnect(self):\n',
        '    def _handle_disconnect(self):\n        self._native_exit_host.close()\n')
    s=replace(s,'    def send_request_to(self, idx, request, callback):\n',
        '''    def send_request_to(self, idx, request, callback):
        host = getattr(self, '_native_exit_host', None)
        if host is not None:
            if host.blocks(idx, request):
                if callback is not None:
                    callback(self=self,response={'code':1,'msg':'FORBIDDEN','result':{}})
                return
            if request.get('method') == 'set_lanes' and self._is_native_ace2k(idx):
                request['params']['native_exit_recovery_mask'] = host.mask(idx,recovery_only=True)
''')
    s=replace(s,'        return in_use & 0x0F, by_hand & 0x0F\n',
        '''        host = getattr(self, '_native_exit_host', None)
        if host is not None:
            in_use |= host.mask(idx,recovery_only=True)
        return in_use & 0x0F, by_hand & 0x0F
''')
    s=replace(s,"            'api_version': ACE_API_VERSION,\n",
        "            'api_version': ACE_API_VERSION,\n            'native_exit_tasks': self._native_exit_host.status() if hasattr(self,'_native_exit_host') else [],\n")
    compile(s,'ace.py','exec')
    return s


def patch_recovery_driver(s):
    # Run before _tick, not after it: autonomous relax must see the hold.
    s=replace(s,"        if method == 'native_exit_stop':\n",
        '''        if method == 'set_lanes':
            recovery = (request.get('params') or {}).get('native_exit_recovery_mask')
            if type(recovery) is int and 0 <= recovery <= 15:
                self._native_exit.recovery_mask = recovery
        if method == 'native_exit_stop':
''')
    compile(s,'ace_protocol_ace2k.py','exec')
    return s


def stage(extras):
    extras=Path(extras)
    stage_worker(extras)
    for name in ('cloud_route_owner.py','host_native_exit.py'):
        t=(HERE/name).read_text()
        t=t.replace('from cloud_route_owner import','from .cloud_route_owner import')
        t=t.replace('from native_eject_contract import','from .native_eject_contract import')
        t=t.replace('from lane_contract import LaneSample','from .ace_protocol import LaneSample')
        compile(t,name,'exec')
        (extras/name).write_text(t)
    driver=extras/'ace_protocol_ace2k.py'
    driver.write_text(patch_recovery_driver(scope_driver(driver.read_text())))
    engine=extras/'ace.py'
    engine.write_text(patch_engine(scope_rfid(scope_engine(engine.read_text()))))
