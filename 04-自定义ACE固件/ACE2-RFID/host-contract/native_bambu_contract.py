"""Offline contract for a future verified MCU+host RFID integration.

No I/O, cache mutation, spool binding, or caller-supplied flag can activate
this module in production. A supervisor must independently attest firmware
installation, request correlation and physical slot attribution. 0x0102 from
unverified 60O is deliberately rejected. This is NOT a hardware RF lock.
"""
import re

_UID = re.compile(r'^[0-9A-F]{8}$')


def candidate_from_response(response, *, expected_seq, target_slot,
                            expected_uid, target_ace_uid, firmware_deployment,
                            connection_generation, session_generation,
                            attribution_check, transaction_check,
                            trusted_releases=None):
    """Return data only for a correlated, verified response; otherwise None.

    firmware_deployment is a record from a trusted installer/identity manager,
    not metadata from the RFID response. The installer must have verified the
    specific candidate image, device identity, live version and connection
    generation; this record alone is not cryptographic device attestation.
    attribution_check is the supervisor's independent, fail-closed physical
    attribution validator. Its implementation is required before deployment.
    trusted_releases defaults to EMPTY; no existing experimental image is
    approved. It maps exact build hashes to expected reported versions. The
    transaction validator must verify a firmware-enforced RF ownership lease
    for this actual device, request and session, not a user-supplied boolean.
    """
    if (type(target_slot) is not int or not 0 <= target_slot <= 3 or
            type(expected_seq) is not int or not 0 <= expected_seq <= 65535 or
            not isinstance(response, dict) or not isinstance(target_ace_uid, str) or
            not target_ace_uid or type(connection_generation) is not int or
            connection_generation < 1 or type(session_generation) is not int or
            session_generation < 1 or not isinstance(expected_uid, str) or
            not _UID.fullmatch(expected_uid) or expected_uid.startswith('88') or
            not callable(attribution_check) or not callable(transaction_check) or
            not isinstance(trusted_releases, dict)):
        return None
    d = firmware_deployment
    if (not isinstance(d, dict) or d.get('deployment_verified') is not True or
            d.get('device_uid_verified') is not True or d.get('app_alive') is not True or
            not isinstance(d.get('image_sha256'), str) or
            not re.fullmatch(r'[0-9a-f]{64}', d.get('image_sha256', '')) or
            d.get('image_sha256') not in trusted_releases or
            d.get('device_uid') != target_ace_uid or
            d.get('firmware_version') != trusted_releases.get(d.get('image_sha256')) or
            d.get('rf_exclusion_verified') is not True or
            d.get('register_restore_verified') is not True or
            d.get('stack_margin_verified') is not True or
            not isinstance(d.get('firmware_version'), str) or
            type(d.get('connection_generation')) is not int or
            type(d.get('session_generation')) is not int or
            d.get('connection_generation') != connection_generation or
            d.get('session_generation') != session_generation):
        return None
    if (response.get('_v2_frame_valid') is not True or
            response.get('_v2_payload_valid') is not True or
            response.get('_v2_command') != 68 or
            type(response.get('id')) is not int or response['id'] != expected_seq or
            type(response.get('code')) is not int or response['code'] != 0):
        return None
    r = response.get('result')
    if (not isinstance(r, dict) or
            any(r.get(k) is not True for k in ('_index_present','_code_present',
                '_field2_present','_sku_present','_type_present','_sku_ascii','_type_ascii','_known_optional_valid','_color_present',
                '_color_opaque')) or
            type(r.get('index')) is not int or r['index'] != target_slot or
            type(r.get('code')) is not int or r['code'] != 0):
        return None
    tag = r.get('tag')
    if not isinstance(tag, dict) or type(tag.get('field2')) is not int or tag['field2'] != 0x0102:
        return None
    if r.get('sku') != 'SM' + expected_uid:
        return None
    material = r.get('type')
    if (not isinstance(material, str) or not 3 <= len(material) <= 16 or
            not material.strip() or any(not 0x20 <= ord(c) <= 0x7e for c in material)):
        return None
    rgb = r.get('color')
    if (not isinstance(rgb, list) or len(rgb) != 3 or
            any(type(x) is not int or not 0 <= x <= 255 for x in rgb)):
        return None
    try:
        held = transaction_check(target_ace_uid, expected_seq, connection_generation,
                                 session_generation)
        attributed = attribution_check(target_ace_uid, target_slot, expected_uid,
                                       connection_generation, session_generation)
    except Exception:
        return None
    if held is not True or attributed is not True:
        return None
    # Temperatures intentionally absent, even if cmd13 ever provides zeros.
    return {'uid': expected_uid, 'type': material, 'color': list(rgb),
            'brand': 'Bambu Lab', 'format': 'mifare'}
