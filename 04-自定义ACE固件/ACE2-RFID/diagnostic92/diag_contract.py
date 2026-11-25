"""92O read-only diagnostic protocol; values are raw, never spool estimates."""
import struct

REJECTED = 0xffffffff

def request_word(lane, field):
    if isinstance(lane, bool) or isinstance(field, bool) or lane not in range(4) or field not in range(3):
        raise ValueError('lane must be 0..3 and field 0..2')
    return 0x80000000 | (10 << 16) | (lane << 8) | field

def interpret_word(field, word):
    if field not in range(3) or not isinstance(word, int) or not 0 <= word <= REJECTED:
        raise ValueError('invalid diagnostic field or word')
    if word == REJECTED:
        return {'state': 'rejected_or_ambiguous', 'raw': word}
    value = struct.unpack('<f', struct.pack('<I', word))[0] if field < 2 else word
    return {'state': 'diagnostic_unattributed', 'raw': word, 'value': value,
            'warning': 'single word only; no coherent multiword snapshot or session identity'}
