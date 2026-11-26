"""Wire private native exit requests to the existing field link writer."""
from pathlib import Path
from patch_native_link import stage as stage_guard, replace
HERE=Path(__file__).resolve().parent


def patch_worker(text):
    text=replace(text,'from .native_link_guard import NativeExitLinkGuard\n',
                 'from .native_link_guard import NativeExitLinkGuard\nfrom .native_exit_worker import NativeExitWorker, METHODS as NATIVE_EXIT_METHODS\n')
    text=replace(text,'        self._native_exit = NativeExitLinkGuard(self)\n',
                 '        self._native_exit = NativeExitLinkGuard(self)\n        self._native_exit_worker = NativeExitWorker(self, klipper_block)\n')
    text=replace(text,"                          'flash_led') + DRYER_METHODS + MOTION_METHODS:\n",
                 "                          'flash_led') + DRYER_METHODS + MOTION_METHODS + NATIVE_EXIT_METHODS:\n")
    # STOP is served before queued retries. Other private requests preserve
    # the ordinary driver tick, so frequent status polls cannot starve it.
    text=replace(text,'        # STOP takes precedence over queued busy retries in _tick.\n',
                 '''        if method == 'native_exit_stop':
            return self._native_exit_response(method, request, rid, ok)
        # STOP takes precedence over queued busy retries in _tick.
''')
    text=replace(text,"        if method == 'discover_device':\n",
                 "        if method in NATIVE_EXIT_METHODS:\n            return self._native_exit_response(method, request, rid, ok)\n        if method == 'discover_device':\n")
    text=replace(text,'    # -- dryer, identify --------------------------------------------------\n',
                 '''    def _native_exit_response(self, method, request, rid, ok):
        try:
            return ok(self._native_exit_worker.request(method, request.get('params') or {}))
        except (IOError, OSError):
            raise
        except (ValueError, RuntimeError) as exc:
            return {'id': rid, 'code': FAILED, 'msg': str(exc), 'result': {}}

    # -- dryer, identify --------------------------------------------------
''')
    compile(text,'ace_protocol_ace2k.py','exec')
    return text


def stage(extras):
    extras=Path(extras)
    stage_guard(extras)
    for name in ('native_eject_contract.py','native_unload_wire.py','native_exit_worker.py'):
        text=(HERE/name).read_text()
        text=text.replace('from lane_contract import still','from .ace_protocol import still')
        text=text.replace('from native_eject_contract import','from .native_eject_contract import')
        compile(text,name,'exec')
        (extras/name).write_text(text)
    driver=extras/'ace_protocol_ace2k.py'
    driver.write_text(patch_worker(driver.read_text()))
