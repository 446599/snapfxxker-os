"""Attach the material batch service to the composed 1902.7 provider.

This is the feature integration entry, not a firmware build. CI runs this
stage on the original pinned field archive with the exact existing 1902.7
route/persistence patches applied first.
"""
from pathlib import Path
import sys
from patch_host_native_exit import stage as stage_host
from patch_scoped_runtime import replace

HERE=Path(__file__).resolve().parent
MODULES=('native_link_guard','native_eject_contract','native_unload_wire',
         'native_exit_worker','cloud_route_owner','host_native_exit','material_batch_runtime')


def patch_engine(s):
    s=replace(s,"            os.path.join(self.paths['config_dir'], 'extended', 'multiace', 'native_exit_journal.json'))\n",
        """            os.path.join(self.paths['config_dir'], 'extended', 'multiace', 'native_exit_journal.json'))
        from .material_batch_runtime import MaterialBatchRuntime
        self._material_batch = MaterialBatchRuntime(self, self._native_exit_host)
""")
    s=replace(s,"        self._native_exit_host.install_timer()\n",
                "        self._native_exit_host.install_timer()\n        self._material_batch.install()\n")
    s=replace(s,"        self._native_exit_host.close()\n",
                "        self._material_batch.close()\n        self._native_exit_host.close()\n")
    s=replace(s,"        self.gcode.register_command(\n            'ACE_START_DRYING'",
        """        self.printer.lookup_object('webhooks').register_endpoint(
            'multiace/material_batch', self._material_batch.webhook)
        self.gcode.register_command('ACE_SLOT_EXIT', self._material_batch.single_eject,
            desc='Native exit. Confirm actual parked tip using CONFIRM_PARKED=1 only after inspection.')
        self.gcode.register_command(
            'ACE_START_DRYING'""")
    s=replace(s,"            'native_exit_tasks': self._native_exit_host.status() if hasattr(self,'_native_exit_host') else [],\n",
        """            'native_exit_tasks': self._native_exit_host.status() if hasattr(self,'_native_exit_host') else [],
            'material_batch_tasks': self._material_batch.status() if hasattr(self,'_material_batch') else [],
""")
    # Native control/status RPCs do not mean the whole ACE is busy.
    s=replace(s,"        if (request.get('method') not in TELEMETRY_METHODS\n",
        """        if (request.get('method') not in TELEMETRY_METHODS
                and not request.get('method','').startswith('native_exit_')
""")
    compile(s,'ace.py','exec')
    return s


WEB = '''
@app.post("/api/material/batch/{operation}")
async def material_batch_operation(operation: str, body: dict) -> dict:
    if operation not in ("preview","start","status","stop","parking_preview"):
        raise HTTPException(404, "Unknown material batch operation")
    try:
        response = await _mr_post("/printer/multiace/material_batch",
            {"op": operation, "payload": json.dumps(body, ensure_ascii=False)},
            timeout=12.0)
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Material service unavailable: %s" % exc)
    result = response.get("result")
    if not isinstance(result, dict):
        raise HTTPException(502, "Invalid material service result")
    if result.get("error"):
        raise HTTPException(400, str(result["error"]))
    return result

'''


def patch_web(s):
    s=replace(s,'@app.get("/api/aces")\n',WEB+'@app.get("/api/aces")\n')
    compile(s,'main.py','exec')
    return s


def patch_device_service(s):
    s=replace(s,'            elif self.command == "POST" and route == "/spoolman/sync":\n',
        '''            elif self.command == "POST" and route.startswith("/material/batch/"):
                operation = route[len("/material/batch/"):]
                if operation not in ("preview","start","status","stop","parking_preview"):
                    raise ValueError("Unknown material batch operation")
                result = web("material/batch/" + operation, data, "POST")
            elif self.command == "POST" and route == "/spoolman/sync":
''')
    compile(s,'device_service.py','exec')
    return s


def stage(provider,service=None):
    provider=Path(provider); extras=provider/'klipper/extras'
    stage_host(extras)
    runtime=(HERE/'material_batch_runtime.py').read_text().replace(
        'from host_native_exit import','from .host_native_exit import')
    compile(runtime,'material_batch_runtime.py','exec')
    (extras/'material_batch_runtime.py').write_text(runtime)
    engine=extras/'ace.py'
    engine.write_text(patch_engine(engine.read_text()))
    web=provider/'web/backend/main.py'
    web.write_text(patch_web(web.read_text()))
    if service is not None:
        service=Path(service)
        service.write_text(patch_device_service(service.read_text()))


def patch_mount_map(s):
    anchor='extras/ace_protocol_ace2k.py extras/ace_protocol_ace2k.py\n'
    return replace(s,anchor,anchor+''.join('extras/%s.py extras/%s.py\n'%(name,name) for name in MODULES))
