"""Read-only 97O diagnostic for a just-attributed manual tag read.

Raw device counters are observations, not a validated current-roll estimate.
"""
import math
import struct


def refresh(ace, reader, idx, slot, transport, epoch, uid, seq):
    result = {'state': 'unknown', 'reason': 'unconfirmed', 'uid': uid or None,
              'ace': idx, 'slot': slot, 'insert_epoch': epoch,
              'complete_turn_confirmed': False, 'insertion_tracked': epoch is not None}
    def valid():
        try:
            return (transport is not None and ace._serials.get(idx) is transport
                    and ace._connected_per_ace.get(idx, False)
                    and not ace._serial_failed_per_ace.get(idx, False)
                    and (getattr(ace, '_insert_epochs', {}) or {}).get((idx, slot)) == epoch
                    and getattr(ace, '_tag_op_seq', None) == seq
                    and getattr(ace, '_tag_op_kind', None) == 'read'
                    and (getattr(ace, '_tag_op_ace', None), getattr(ace, '_tag_op_slot', None)) == (idx, slot)
                    and bool(uid) and (getattr(ace, '_rc_last_uid', {}) or {}).get((idx, slot)) == uid
                    and not getattr(ace, '_swap_in_progress', False)
                    and not getattr(ace, '_unload_all_active', False)
                    and ace._stop_guard.message() is None)
        except Exception:
            return False
    try:
        fw = (ace._ace_models.get(idx) or ('', ''))[1]
        if str(fw or '').strip().upper() not in ('V1.1.97O', 'V1.1.98O', 'V1.2.00O', 'V1.2.01O'):
            result['reason'] = 'unsupported_firmware'; return result
        if not valid():
            result['reason'] = 'identity_or_transport_changed'; return result
        # Only query while the latest framed status reports every lane ready.
        if any(v != 'ready' for v in ace._slot_actions._lane_states(idx).values()) or not valid():
            result['reason'] = 'busy_or_changed'; return result
        def word(field):
            if not valid() or any(v != 'ready' for v in ace._slot_actions._lane_states(idx).values()):
                raise ValueError('lane busy')
            packed = 0x800A0000 | (slot << 8) | field
            response = reader._req(idx, 'filament_identify', {'index': packed})
            if not valid() or not isinstance(response, dict): raise ValueError('transport changed')
            if (response.get('_v2_frame_valid') is not True or response.get('_v2_payload_valid') is not True
                    or response.get('_v2_command') != 68 or type(response.get('code')) is not int or response['code'] != 0
                    or str(response.get('msg', '')).strip().upper() == 'FORBIDDEN'):
                raise ValueError('invalid frame')
            data = response.get('result')
            if not isinstance(data, dict) or data.get('_index_present') is not True or data.get('index') != packed:
                raise ValueError('index mismatch')
            value = data.get('code')
            # protobuf may omit a default-zero scalar; the parser flags it explicitly.
            if (type(value) is not int or not 0 <= value < 0xffffffff or
                    not (data.get('_code_present') is True or (value == 0 and data.get('_code_absent') is True))):
                raise ValueError('invalid diagnostic')
            return value
        count = word(2)
        model = word(4)
        ref_raw, sample_raw = word(0), word(1)
        if word(2) != count or not valid():
            result['reason'] = 'unstable_snapshot'; return result
        ref = struct.unpack('<f', struct.pack('<I', ref_raw))[0]
        sample = struct.unpack('<f', struct.pack('<I', sample_raw))[0]
        result.update(native_sample_count=count, native_model_mm=model,
                      native_reference_mm=ref if math.isfinite(ref) else None,
                      native_sample_mm=sample if math.isfinite(sample) else None)
        result['reason'] = ('insufficient_samples' if count == 0 or model == 0
                            else 'provenance_unconfirmed')
        return result
    except Exception:
        result['reason'] = 'diagnostic_unavailable'
        return result
