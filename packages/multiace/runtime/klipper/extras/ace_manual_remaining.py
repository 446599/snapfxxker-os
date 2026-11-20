"""Publish only the current continuous scan's attributed, returned evidence.

Native CMD68 diagnostics have no current-spool provenance and are not used as
inventory. This module does not move motors or overwrite a spool's stock.
"""
import json
import math
import os
import tempfile
import copy

from .ace_rfid_evidence import estimate_interval, validate_profile, _uid


def _profile_path(ace):
    return os.path.join(ace.paths['config_dir'], 'persistent', 'multiace-spool-profiles.json')


def _profile_data(ace):
    try:
        with open(_profile_path(ace)) as stream:
            text = stream.read(65537)
        if len(text) > 65536:
            raise ValueError('spool profile file exceeds its size limit')
        data = json.loads(text)
    except FileNotFoundError:
        return {'schema': 1, 'profiles': {}}
    if (not isinstance(data, dict) or data.get('schema') != 1
            or not isinstance(data.get('profiles'), dict)):
        raise ValueError('invalid spool profile file')
    return data


def save_profile(ace, uid, profile):
    """Store explicit measured inputs atomically; this never calibrates a spool.

    Callers must supply reader identity/pair and their calibration receipt.
    Invalid existing files are not silently overwritten with an empty set.
    """
    if not _uid(uid):
        raise ValueError('invalid spool UID')
    profile = copy.deepcopy(validate_profile(profile))
    data = _profile_data(ace)
    data['profiles'][uid.upper()] = profile
    encoded = json.dumps(data, ensure_ascii=False, allow_nan=False, sort_keys=True)
    if len(encoded) > 65536:
        raise ValueError('spool profile file exceeds its size limit')
    path = _profile_path(ace)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.spool-profiles-', dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return copy.deepcopy(profile)


def load_profile(ace, uid):
    """Read one explicit profile; absent or invalid data remains unavailable."""
    if not _uid(uid):
        return None
    try:
        data = _profile_data(ace)
        profile = data['profiles'].get(uid.upper(), data['profiles'].get(uid.lower()))
        return copy.deepcopy(profile) if isinstance(profile, dict) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _profile(ace, uid):
    """Read optional measured profiles. Never invent dimensions or density."""
    return load_profile(ace, uid)


def _profile_context(ace, idx, slot, uid):
    """Require the attributed tag to still belong to this live insertion.

    A remembered UID alone is insufficient after unplugging an ACE, replacing
    a spool, or restarting Klipper. This check is read-only and never starts a
    tag scan or a motor operation on behalf of a profile request.
    """
    if (type(idx) is not int or not 0 <= idx < 4
            or type(slot) is not int or not 0 <= slot < 4 or not _uid(uid)):
        raise ValueError('料槽或标签 UID 无效')
    transport = (getattr(ace, '_serials', {}) or {}).get(idx)
    if (transport is None
            or (getattr(ace, '_connected_per_ace', {}) or {}).get(idx) is not True
            or (getattr(ace, '_serial_failed_per_ace', {}) or {}).get(idx, False)
            or (getattr(ace, '_reconnecting_per_ace', {}) or {}).get(idx, False)):
        raise ValueError('ACE 连接已变化，请连接稳定后重新识别此槽标签')
    identity = ace._location_identity(idx)
    device_uid = identity[1] if isinstance(identity, (list, tuple)) and len(identity) == 2 else None
    if (not isinstance(device_uid, (list, tuple)) or len(device_uid) != 3
            or any(type(value) is not int or not 0 <= value < 1 << 32 for value in device_uid)):
        raise ValueError('ACE 身份尚未确认，请重新识别此槽标签')
    epoch = (getattr(ace, '_insert_epochs', {}) or {}).get((idx, slot))
    current_uid = (getattr(ace, '_rc_last_uid', {}) or {}).get((idx, slot))
    stamp = (getattr(ace, '_single_verified_uid', {}) or {}).get((idx, slot))
    if (type(epoch) is not int or epoch <= 0 or not _uid(current_uid)
            or current_uid.upper() != uid.upper()
            or not isinstance(stamp, (list, tuple)) or len(stamp) != 4
            or stamp[0] is not transport or stamp[1] != identity or stamp[2] != epoch
            or not _uid(stamp[3]) or stamp[3].upper() != uid.upper()):
        raise ValueError('此槽标签归属已变化或尚未验证，请先完成此槽标签识别')
    return transport, identity, epoch, uid.upper()


def register_endpoints(ace):
    """Expose the measured profile in the existing slot UI, without motion.

    Requests use {ace, slot, uid, action: read|save, profile: {...}}. The live
    reader identity and antenna pair are supplied here, never by the client.
    Saving only records explicit calibration inputs; the next successful RFID
    scan must independently provide new evidence before remaining mass changes.
    """
    def endpoint(request):
        try:
            idx, slot = request.get_int('ace'), request.get_int('slot')
            uid, action = request.get_str('uid'), request.get_str('action', 'read')
            if action not in ('read', 'save'):
                raise ValueError('标定操作无效')
            context = _profile_context(ace, idx, slot, uid)
            uid = context[3]
            device_uid, pair = list(context[1][1]), slot // 2
            if action == 'save':
                supplied = request.get('profile', None)
                if not isinstance(supplied, dict):
                    raise ValueError('请填写实际测量得到的标定参数')
                profile = copy.deepcopy(supplied)
                profile['reader_device_uid'] = device_uid
                profile['reader_pair'] = pair
                validate_profile(profile)
                # No reactor pause occurs between these checks and the atomic
                # save. A changed live context must never receive this receipt.
                if _profile_context(ace, idx, slot, uid) != context:
                    raise ValueError('标定期间料槽身份已变化，请重新识别后再保存')
                profile = save_profile(ace, uid, profile)
            else:
                # Surface corrupt files to the settings UI; the runtime
                # estimator still treats unavailable profiles as unknown.
                data = _profile_data(ace)
                profile = data['profiles'].get(uid)
                if profile is None:
                    profile = data['profiles'].get(uid.lower())
                if profile is not None:
                    profile = copy.deepcopy(validate_profile(profile))
            matched = (profile is not None
                       and profile.get('reader_device_uid') == device_uid
                       and profile.get('reader_pair') == pair)
            request.send({'ok': True, 'ace': idx, 'slot': slot, 'uid': uid,
                          'profile': profile, 'reader_device_uid': device_uid,
                          'reader_pair': pair, 'calibrated_for_current_reader': matched,
                          'message': ('标定参数已保存；下次成功的标签测量将重新计算余量区间'
                                      if action == 'save' else
                                      '尚无标定参数，余量保持未知' if profile is None else
                                      '此标定属于其他读卡器，请核实并重新标定'
                                      if not matched else '标定参数已读取')})
        except Exception as exc:
            request.send({'ok': False, 'message': str(exc)})

    ace.printer.lookup_object('webhooks').register_endpoint('multiace/spool_profile', endpoint)


def refresh(ace, reader, idx, slot, transport, epoch, uid, seq):
    result = {'state': 'unknown', 'reason': 'no_current_scan_evidence', 'uid': uid or None,
              'ace': idx, 'slot': slot, 'insert_epoch': epoch,
              'complete_turn_confirmed': False,
              'insertion_tracked': type(epoch) is int and epoch > 0}
    evidence = getattr(reader, '_flow14_remaining', None)
    if not isinstance(evidence, dict):
        return result
    try:
        context = reader._continuous_source_context(idx, slot)
        completed = evidence.get('completed_at')
        if (type(completed) not in (int, float) or not math.isfinite(completed)
                or not 0 <= ace.reactor.monotonic() - completed <= 5.0):
            result['reason'] = 'scan_evidence_stale'
            return result
        valid = (transport is not None and ace._serials.get(idx) is transport
                 and ace._connected_per_ace.get(idx) is True
                 and not ace._serial_failed_per_ace.get(idx, False)
                 and type(epoch) is int and epoch > 0
                 and (getattr(ace, '_insert_epochs', {}) or {}).get((idx, slot)) == epoch
                 and getattr(ace, '_tag_op_seq', None) == seq
                 and getattr(ace, '_tag_read_busy', False) is True
                 and getattr(ace, '_tag_op_kind', None) == 'read'
                 and (getattr(ace, '_tag_op_ace', None), getattr(ace, '_tag_op_slot', None)) == (idx, slot)
                 and bool(uid) and (getattr(ace, '_rc_last_uid', {}) or {}).get((idx, slot)) == uid
                 and evidence.get('uid') == uid and evidence.get('ace') == idx and evidence.get('slot') == slot
                 and evidence.get('transport') is transport and evidence.get('source_context') == context
                 and evidence.get('insert_epoch') == epoch and evidence.get('tag_op_seq') == seq
                 and evidence.get('return_confirmed') is True and not reader._motion_uncertain
                 and ace._slot_actions.cancel is False
                 and not getattr(ace, '_swap_in_progress', False)
                 and not getattr(ace, '_unload_all_active', False)
                 and ace._stop_guard.message() is None)
        if not valid:
            result['reason'] = 'identity_or_transport_changed'
            return result
        bounds = evidence.get('circumference_mm')
        if isinstance(bounds, dict):
            result['circumference_mm'] = dict(bounds)
            result['observed_periods'] = evidence.get('periods', 0)
        result['return_residual_mm'] = evidence.get('return_residual_mm')
        result['scan_origin_reusable'] = evidence.get('scan_origin_reusable') is True
        profile = _profile(ace, uid)
        if profile is not None:
            # RFID read-zone calibration belongs to a physical antenna pair.
            # Moving the same spool to another reader needs its own receipt.
            device_uid = context[1][1]
            if (profile.get('reader_device_uid') != list(device_uid)
                    or type(profile.get('reader_pair')) is not int
                    or profile['reader_pair'] != slot // 2):
                result['reason'] = 'reader_calibration_mismatch'
                return result
        result.update(estimate_interval(evidence, profile))
        return result
    except (AttributeError, KeyError, TypeError, ValueError):
        result['reason'] = 'scan_evidence_unavailable'
        return result
