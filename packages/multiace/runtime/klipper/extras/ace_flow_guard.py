"""Shared route admission for the live flow.14 command and serial paths.

Operation completion does not erase a physical source.  The source map and
dynamic location journal remain authoritative until a verified unload/eject.
The old one-machine location marker is deliberately not a global admission
rule.  No motion, serial polling or automatic source reassignment happens here.
"""
from .ace_path_store import configured_route_heads


MOTION = frozenset(('feed_filament', 'unwind_filament',
                    'feed_or_rollback_raw', 'start_feed_assist',
                    'update_feeding_speed', 'insert_policy_start', 'raw'))


def raw_head_state(ace, head, sensor=None):
    """True/False/None; U1's delayed runout latch is not a position sensor."""
    if sensor is None:
        sensor = ace.printer.lookup_object(
            'filament_motion_sensor e%d_filament' % head, None)
    if sensor is None:
        return None
    if hasattr(sensor, 'runout_buttun_state'):
        value = sensor.runout_buttun_state
    elif getattr(ace, 'host', 'u1') == 'u1':
        return None
    else:
        value = sensor.get_status(0).get('filament_detected')
    # Some button adapters expose the documented binary level as 0/1.
    if type(value) is bool:
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    return None


def source_route(source):
    if not isinstance(source, dict):
        return None
    idx, slot = source.get('ace_index'), source.get('slot')
    if (type(idx) is int and 0 <= idx < 4
            and type(slot) is int and 0 <= slot < 4):
        return idx, slot
    return None


def dynamic_route_blocked(ace, head=None, idx=None, slot=None):
    holder = getattr(ace, '_location_hold', None)
    if holder is None:
        return False
    port, uid = ace._location_identity(idx) if idx is not None else (None, None)
    heads = configured_route_heads(ace, idx, slot) if idx is not None and slot is not None else None
    # This is the general route journal. PATH/PORT/UID in the legacy class
    # describe a historical commissioning incident, not every installation.
    return holder._route_blocked(head, slot, port, uid, heads=heads)


def source_conflict(ace, head, idx, slot):
    source = getattr(ace, '_head_source', {}).get(head)
    if source:
        route = source_route(source)
        if route is None:
            return '喷头 %d 的原料路记录不完整，请先复核来源' % (head + 1)
        if route != (idx, slot):
            return ('喷头 %d 共用料管仍归属 ACE %d 槽 %d；'
                    '请先完成原槽退料，不能覆盖为另一来源'
                    % (head + 1, route[0] + 1, route[1] + 1))
        bound = source.get('connection_identity')
        if bound is not None:
            port, uid = ace._location_identity(idx)
            if (not isinstance(bound, dict) or not isinstance(uid, (list, tuple))
                    or bound.get('port') != port or bound.get('uid') != list(uid)):
                return '原送料设备的身份或物理端口已变化；保留原来源，请复核接管'
    staged = getattr(ace, '_bg_staged', {}).get(head)
    if staged is not None and tuple(staged[:2]) != (idx, slot):
        return '喷头 %d 仍有另一料槽的待完成送料' % (head + 1)
    return None


def wire_rejection(ace, idx, request, serial):
    """Return a reason at the last write boundary, or None to admit.

    STOP and queries always pass this layer. A dynamic position hold permits
    only the exact, still-valid ejection/recovery token checked by SlotActions.
    """
    if not isinstance(request, dict):
        return '无效串口请求'
    method = request.get('method')
    if method not in MOTION:
        return None
    params = request.get('params') or {}
    slot = params.get('index', params.get('slot'))
    if type(slot) is not int or not 0 <= slot < 4:
        # Raw bytes have no trustworthy slot scope. They cannot bypass a
        # running owner or an unresolved physical route.
        holder = getattr(ace, '_location_hold', None)
        if (getattr(ace, '_feed_supervisors', {})
                or any(getattr(ace, '_head_source', {}).values())
                or holder is not None and (not holder.refresh_routes() or holder.routes)):
            return '无明确料槽的运动不能越过现有料路记录'
        return None
    try:
        heads = configured_route_heads(ace, idx, slot)
        for head in heads:
            reason = source_conflict(ace, head, idx, slot)
            if reason:
                return reason
        for owner in list(getattr(ace, '_feed_supervisors', {}).values()):
            if owner.active and owner.head in heads and (owner.idx, owner.slot) != (idx, slot):
                return '共用喷头料管正在由另一送料任务占用'
        if dynamic_route_blocked(ace, idx=idx, slot=slot):
            actions = getattr(ace, '_slot_actions', None)
            if actions is None or not actions.eject_allows(idx, request, serial):
                return '该料路位置尚未确认；请复核原槽并完成退出 ACE'
        return None
    except Exception:
        return '料路归属暂不可确认，未发送运动'
