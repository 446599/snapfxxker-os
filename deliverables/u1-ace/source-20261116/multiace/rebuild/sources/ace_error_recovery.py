"""Current-operation status and operator confirmation of a head's source."""
import copy


class ErrorRecovery:
    def __init__(self, ace):
        self.ace = ace
        self.active = False
        hooks = ace.printer.lookup_object('webhooks')
        hooks.register_endpoint('multiace/slot_recover', self.endpoint)
        hooks.register_endpoint('multiace/head_source', self.source_endpoint)

    def stamp(self, request):
        return True

    def allows_wire(self, request):
        return True

    def task_status(self):
        return self.ace._slot_actions.status()

    def status(self):
        return {'active': False, 'stop_fault': None,
                'queued_reads': len(getattr(self.ace, '_insert_read_queue', [])),
                'slots': [{'ace': idx, 'slot': slot, 'can_check': False,
                           'can_clear': False, 'needs_clear': False,
                           'detail': '', 'prompt': '', 'ticket': ''}
                          for idx in self.ace._connected_per_ace for slot in range(4)]}

    def endpoint(self, request):
        request.send({'started': False, 'message': '旧任务记录已自动释放，无需手动清错'})

    def confirm_source(self, head, idx, slot):
        a = self.ace
        if (type(head) is not int or type(idx) is not int or type(slot) is not int
                or not 0 <= head < 4 or not 0 <= slot < 4 or not 0 <= idx < len(a._ace_devices)):
            raise ValueError('供料槽选择无效')
        ps = a.printer.lookup_object('print_stats', None)
        if getattr(ps, 'state', '') not in ('standby', 'complete', 'cancelled', 'paused'):
            raise RuntimeError('请先暂停打印或等待设备空闲')
        path = a._path_calibration
        if (a._swap_in_progress or a._unload_all_active or getattr(a, '_tag_read_busy', False)
                or path.task.get('active') or a._slot_actions.state.get('state') in ('preparing', 'running')
                or any(op['state'] not in ('complete', 'cancelled', 'error')
                       for op in a._path_operations.status())):
            raise RuntimeError('请等待当前耗材操作结束')
        if not a._connected_per_ace.get(idx) or idx in a._fw_update_hold:
            raise RuntimeError('所选 ACE 尚未在线')
        if path._head(idx, slot, head) != head:
            raise RuntimeError('所选料槽不属于该喷头的 Multi／Head 绑定')
        if not path.head_sensor(head):
            raise RuntimeError('喷头当前无料，无需确认来源')
        gates = a._gate_status_per_ace.get(idx) or []
        if slot >= len(gates) or gates[slot] != 1:
            raise RuntimeError('所选 ACE 槽入口未检测到耗材')
        existing = a._head_source.get(head)
        if isinstance(existing, dict) and not existing.get('load_failed'):
            if (existing.get('ace_index'), existing.get('slot')) != (idx, slot):
                raise RuntimeError('已有当前供料记录，请先核对所选料槽')
        old_sources = copy.deepcopy(a._head_source)
        rows = a._info_per_ace.get(idx, {}).get('slots') or []
        material = next((row for n, row in enumerate(rows)
                         if isinstance(row, dict) and row.get('index', n) == slot), {})
        source = {'ace_index': idx, 'slot': slot, 'type': material.get('type', ''),
                  'brand': material.get('brand', ''), 'color': a._device_color_hex(material),
                  'load_failed': False}
        source = a._overlay_override(idx, slot, source)
        a._head_source[head] = source
        try:
            a._save_head_source()
        except Exception:
            a._head_source = old_sources
            raise
        a._ghost_heads.discard(head)
        return {'saved': True, 'head': head, 'ace': idx, 'slot': slot}

    def source_endpoint(self, request):
        try:
            result = self.confirm_source(request.get_int('head'), request.get_int('ace'),
                                         request.get_int('slot'))
        except (RuntimeError, ValueError) as exc:
            result = {'saved': False, 'message': str(exc)}
        request.send(result)
