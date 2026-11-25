def read_lane_sample(ace, idx, slot):
    """One shared read API. Caller owns motion and the selected connection."""
    transport = ace._serials.get(idx)
    protocol = getattr(ace._protocols.get(idx), 'NAME', '')
    if transport is None or not ace._connected_per_ace.get(idx):
        return None

    def ask(method, params=None):
        if ace._serials.get(idx) is not transport:
            return None
        reply = ace._tipform_send(idx, {'method': method, 'params': params or {}}, timeout=2.0)
        return reply if ace._serials.get(idx) is transport else None

    if protocol == 'ace2k':
        reply = ask('get_lane_state', {'index': slot})
        if not accepted(reply):
            return None
        r = reply['result']
        if r.get('index') != slot or type(r.get('present')) is not bool:
            return None
        if type(r.get('encoder')) is not int or type(r.get('motor')) is not int:
            return None
        stamp = r.get('stamp')
        if not isinstance(stamp, (tuple, list)) or len(stamp) != 4:
            return None
        if any(type(t) not in (int, float) for t in stamp):
            return None
        return LaneSample(r['present'], r['state'], r['encoder'], r['motor'], tuple(stamp))
    if protocol == 'v2':
        return stock_sample(ask('get_status'), ask('get_key_state'), ask('get_feed_info'), slot)
    return None
