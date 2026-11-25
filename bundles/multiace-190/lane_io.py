from math import isfinite

def read_lane_sample(ace, idx, slot):
    """One shared read API. Caller owns motion and the selected connection."""
    if type(slot) is not int or not 0 <= slot < 4:
        return None
    transport = ace._serials.get(idx)
    protocol = getattr(ace._protocols.get(idx), 'NAME', '')
    if transport is None or not ace._connected_per_ace.get(idx):
        return None

    def same_connection():
        return (ace._serials.get(idx) is transport
                and ace._connected_per_ace.get(idx)
                and getattr(ace._protocols.get(idx), 'NAME', '') == protocol)

    def ask(method, params=None):
        if not same_connection():
            return None
        reply = ace._tipform_send(idx, {'method': method, 'params': params or {}}, timeout=2.0)
        return reply if same_connection() else None

    if protocol == 'ace2k':
        reply = ask('get_lane_state', {'index': slot})
        if not accepted(reply):
            return None
        r = reply['result']
        if type(r.get('index')) is not int or r['index'] != slot or type(r.get('present')) is not bool:
            return None
        if (type(r.get('encoder')) is not int or not -(2**31) <= r['encoder'] < 2**31
                or type(r.get('motor')) is not int or not 0 <= r['motor'] < 2**32
                or not isinstance(r.get('state'), str) or r['state'] not in (
                    'ready', 'feeding', 'rollback', 'preloading', 'assisting',
                    'rollback_assisting', 'feed_error')):
            return None
        stamp = r.get('stamp')
        if not isinstance(stamp, (tuple, list)) or len(stamp) != 4:
            return None
        if any(type(t) not in (int, float) or not isfinite(t) for t in stamp):
            return None
        return LaneSample(r['present'], r['state'], r['encoder'], r['motor'], tuple(stamp))
    if protocol == 'v2':
        status = ask('get_status')
        if not accepted(status):
            return None
        gates = ask('get_key_state')
        if not accepted(gates):
            return None
        return stock_sample(status, gates, ask('get_feed_info'), slot)
    return None
