"""Convert a proven current native cycle result to an estimated weight.

Callers must establish RFID attribution and session lifetime; this module does
not prove either. No persistence, requests or motion are performed here.
"""
import math

def estimate_native(*, session, captured_session, slot, captured_slot,
                    uid, captured_uid, cycle_count, raw_mm, material,
                    observation_time, trusted_attribution):
    result={'state':'pending','remaining_g':None,'uid':None,
            'source':'ace_native_remainder','timestamp':observation_time,
            'reason':'no valid current cycle'}
    if session is None or captured_session != session or slot != captured_slot:
        result.update(state='ambiguous',reason='session or slot changed'); return result
    if (trusted_attribution is not True or not isinstance(uid,str) or
        not isinstance(captured_uid,str) or uid.upper()!=captured_uid.upper() or
        len(uid)!=8 or any(c not in '0123456789ABCDEF' for c in uid.upper())):
        result.update(state='ambiguous',reason='UID attribution unavailable'); return result
    result['uid']=uid.upper()
    if type(cycle_count) is not int or cycle_count < 1: return result
    if type(raw_mm) is not int or not 10000 <= raw_mm <= 330000:
        result.update(state='ambiguous',reason='native length outside model'); return result
    if material != 'PLA':
        result['reason']='density unavailable'; return result
    if not isinstance(observation_time,(int,float)) or isinstance(observation_time,bool) or not math.isfinite(observation_time):
        result['reason']='timestamp unavailable'; return result
    grams=raw_mm * math.pi * (1.75/2)**2 * 1.24 / 1000
    result.update(state='estimate',remaining_g=round(grams),reason=None,
                  quality='ACE geometry estimate, PLA 1.75 mm assumed')
    return result
