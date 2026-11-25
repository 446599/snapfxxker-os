#!/usr/bin/env python3
"""One-device bench installer. Default is no-flash preflight (temporarily releases/resumes serial ownership).

Uses an unchanged copy of the installed multiACE OTA transport. Its extra
private image entry exists only in this process; web release gates stay intact.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time
import urllib.request
import ace2_ota as ota

HERE = Path(__file__).resolve().parent
PORT = '/dev/serial/by-path/platform-fed00000.usb-usb-0:1.3:1.0'
UID = (808780566, 17445170, 942880330)
HASH = '3863df3742bf23f9b7c8ae297be5ddaeb1b0eac423ce1a7ac574f96429d5d853'
REVIEW_APPROVED = True
TRANSPORT_HASH = 'c67395d7299bb0de82944a13a2b659043fc6f25696c1951f19d1de3757199f1f'
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def request(path, data=None):
    req = urllib.request.Request('http://127.0.0.1'+path,
        data=None if data is None else json.dumps(data).encode(),
        headers={'Content-Type': 'application/json'})
    with opener.open(req, timeout=15) as response:
        return json.load(response)

def save(name, data):
    (HERE/name).write_text(json.dumps(data, indent=2)+'\n')

def progress(pct, message):
    row = {'time': time.time(), 'pct': pct, 'message': message}
    with (HERE/'progress.jsonl').open('a') as f:
        f.write(json.dumps(row)+'\n')
    print(json.dumps(row), flush=True)

def state(held=False):
    s = request('/multiace/api/state')
    assert s['printer_state'] in ('idle', 'ready'), 'printer not idle'
    assert not s['tag_op']['busy'] and not s['swap_in_progress'] and not s['unload_all_active']
    assert s['calibration']['state'] in ('idle', 'complete', 'completed', 'cancelled', 'error')
    a = next(x for x in s['aces'] if x['idx'] == 0)
    assert a['serial_path'] == PORT and a['fw_hold'] is held
    assert not a['auto_dry_running'] and not a['auto_dry']['enabled']
    assert a['dryer']['status'] == 'stop'
    assert a['feed_assist'] == -1
    assert not any(x.get('state') not in ('empty', 'ready') for x in a['slots'])
    if not held:
        assert a['connected'] and a['status'] == 'ready'
    return s

def identify():
    t = ota.ACE2Transport(PORT)
    try:
        packets = t.send_recv(0, b'', timeout=2)
        assert len(packets) == 1 and packets[0]['is_resp'], 'missing DISCOVER response'
        f = ota.pb_decode(packets[0]['payload'])
        actual = tuple(ota.get_field(f, i) for i in (1, 2, 3))
        assert actual == UID, 'different ACE UID: '+repr(actual)
        version = ota.get_ace_version(t)
        assert version and version[0] in ('V1.1.95O', 'V1.1.97O') and not version[1]
        assert ota.ace_app_alive(t) is True, 'application not responding'
        return {'uid': actual, 'version': version, 'app_alive': True}
    finally:
        t.close()

def main():
    if not REVIEW_APPROVED:
        raise RuntimeError('97 manifest review and exact image values required')
    parser = argparse.ArgumentParser()
    parser.add_argument('--flash', action='store_true')
    args = parser.parse_args()
    assert hashlib.sha256(Path(ota.__file__).read_bytes()).hexdigest() == TRANSPORT_HASH
    image = HERE/'ACE2-97O-candidate.bin'
    assert hashlib.sha256(image.read_bytes()).hexdigest() == HASH
    rollback = HERE/'ACE2-Open-V1.1.60O.bin'
    assert hashlib.sha256(rollback.read_bytes()).hexdigest() == '35d154abb9b6e037f9c6e10640bdce05fa62e2ecaa47fc5b3b21dac9607e5360'
    rollback91 = HERE/'ACE2-233-V1.1.91O.bin'
    assert hashlib.sha256(rollback91.read_bytes()).hexdigest() == '34a8a3c76b8e127262baf05fb22639ee9f2ce588450eb04559c1c15d506fe323'
    rollback92 = HERE/'ACE2-92O-diagnostic.bin'
    assert hashlib.sha256(rollback92.read_bytes()).hexdigest() == '411079789ce2f64654ef47b6d1be7abddbbf0d4a396c5a7e20304a302df9afcb'
    rollback95 = HERE/'ACE2-95O-diagnostic.bin'
    assert hashlib.sha256(rollback95.read_bytes()).hexdigest() == '487b229840d65acc388926e9bae5502d12fdcd1b99372012b7461e6d24a3d26a'
    rollback93 = HERE/'ACE2-93O-diagnostic.bin'
    assert hashlib.sha256(rollback93.read_bytes()).hexdigest() == '18e70aede9a1d4614a1b3ecd85fdb498a9bb42e778bcde621c9e34597a65cb52'
    fw = ota.load_image(str(image), '1.1.97O', 'a9538adc68118de39574f017e1fe0ee7')
    assert len(fw.data) == 78898 and fw.image_crc == 0x159d
    assert fw.data[-8:] == bytes.fromhex('61a5635a65a5325a')
    # Exact private bench target, not a general force/allow-unknown switch.
    ota.KNOWN_FIRMWARE['1.1.97O'] = {
        'size': 78898, 'crc': 0x159d, 'md5': 'a9538adc68118de39574f017e1fe0ee7',
        'announce': '1.1.31', 'tested': 'offline reviewed private bench candidate',
    }
    ota.check_known(fw)
    save('state-before-'+('flash' if args.flash else 'dry')+'.json', state())
    attempted_release = False
    try:
        attempted_release = True
        request('/printer/gcode/script', {'script': 'ACE_FW_RELEASE ACE=0'})
        state(held=True)
        identity = identify()
        save('identity-before-'+('flash' if args.flash else 'dry')+'.json', identity)
        if args.flash:
            assert identity['version'][0] == 'V1.1.95O', 'do not repeat candidate flash'
        result = ota.flash(PORT, fw, progress, dry_run=not args.flash, force=False)
        save('flash-result.json' if args.flash else 'dry-run-result.json', result)
        print(json.dumps(result), flush=True)
        if args.flash:
            assert result.get('verified') and result.get('app_alive') is True, 'new application not verified'
            after = identify()
            assert after['version'][0] == 'V1.1.97O'
            save('identity-after.json', after)
    finally:
        if attempted_release:
            request('/printer/gcode/script', {'script': 'ACE_FW_RESUME ACE=0'})

if __name__ == '__main__':
    main()
