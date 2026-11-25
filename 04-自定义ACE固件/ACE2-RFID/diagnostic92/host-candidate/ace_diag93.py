"""Strict admission for private 93O diagnostics; never send on other images."""
UID=(808780566,17445170,942880330)
def request_word(version, uid, lane, field):
    if version != 'V1.1.93O' or tuple(uid or ()) != UID:
        return None
    if type(lane) is not int or lane not in range(4) or type(field) is not int or field not in range(5):
        return None
    return 0x800a0000 | (lane << 8) | field
