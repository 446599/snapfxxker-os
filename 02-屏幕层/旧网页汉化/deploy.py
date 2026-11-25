"""Install the validated page over an explicitly authorized temporary SSH connection."""
from pathlib import Path
import base64
import hashlib
import json
import os
import shlex
import sys
import paramiko

ROOT = Path(__file__).resolve().parent
HOST = os.environ.get('U1_HOST', '192.168.2.159')
manifest = json.loads((ROOT / 'dist/manifest.json').read_text())
content = (ROOT / 'dist/index.html').read_bytes()
assert hashlib.sha256(content).hexdigest() == manifest['localized_sha256']
assert json.loads((ROOT / 'dist/test-results.json').read_text())['result'] == 'pass'

remote_code = r'''
import base64, hashlib, json, os, pathlib, tempfile, sys
assert os.geteuid() == 0, 'Root access required for the page file'
payload = json.load(sys.stdin)
m = payload['manifest']
target = pathlib.Path(m['target'])
directory = pathlib.Path('/oem/printer_data/config/extended/firmware-ui-zh')
backup = directory / 'index.original.html'
debug = pathlib.Path('/oem/.debug')
new = base64.b64decode(payload['content'])
old = target.read_bytes()
sha = lambda data: hashlib.sha256(data).hexdigest()
assert sha(old) in [m['original_sha256'], m['localized_sha256']], 'Original page changed; refusing to overwrite'
assert sha(new) == m['localized_sha256'], 'Transfer checksum mismatch'
directory.mkdir(parents=True, exist_ok=True)

def atomic_write(path, content, mode=0o644):
    with tempfile.NamedTemporaryFile(dir=str(path.parent), delete=False) as f:
        temporary = pathlib.Path(f.name)
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(str(temporary), mode)
    os.replace(str(temporary), str(path))

if backup.exists():
    assert sha(backup.read_bytes()) == m['original_sha256'], 'Backup checksum mismatch'
else:
    assert sha(old) == m['original_sha256'], 'Original backup is unavailable'
    atomic_write(backup, old)

record = dict(m)
previous_record = directory / 'manifest.json'
if previous_record.exists():
    previous = json.loads(previous_record.read_text())
    record['debug_was_present'] = previous['debug_was_present']
else:
    record['debug_was_present'] = debug.exists()
record['system_persistence_enabled'] = True
record['backup'] = str(backup)
created_debug = not debug.exists()
try:
    atomic_write(directory / 'index.html', new)
    atomic_write(directory / 'manifest.json', json.dumps(record, ensure_ascii=False, indent=2).encode())
    atomic_write(directory / 'restore.py', base64.b64decode(payload['restore']), 0o644)
    if created_debug:
        with debug.open('xb') as f:
            f.flush()
            os.fsync(f.fileno())
    atomic_write(target, new)
    assert sha(target.read_bytes()) == m['localized_sha256']
    os.sync()
except Exception:
    atomic_write(target, old)
    if created_debug and debug.exists(): debug.unlink()
    raise
print(json.dumps({'result':'installed', 'target':str(target), 'sha256':sha(target.read_bytes()),
    'backup':str(backup), 'debug_was_present':record['debug_was_present'],
    'system_persistence_enabled':debug.exists(), 'printer_restart_required':False}))
'''

restore = r'''
"""Restore the original page. An optional flag restores the old boot-reset policy."""
from pathlib import Path
import hashlib, json, os, sys, tempfile
root = Path(__file__).resolve().parent
m = json.loads((root/'manifest.json').read_text())
target = Path(m['target'])
old = (root/'index.original.html').read_bytes()
sha = lambda b: hashlib.sha256(b).hexdigest()
assert sha(old) == m['original_sha256'], 'Backup checksum mismatch'
assert sha(target.read_bytes()) in [m['localized_sha256'],m['original_sha256']], 'Current page belongs to a different version; refusing to overwrite'
with tempfile.NamedTemporaryFile(dir=str(target.parent),delete=False) as f:
    name=f.name; f.write(old); f.flush(); os.fsync(f.fileno())
os.chmod(name,0o644)
os.replace(name,str(target))
if '--restore-boot-reset' in sys.argv and not m['debug_was_present']:
    # Explicit opt-in: the next boot will also discard unrelated system edits.
    Path('/oem/.debug').unlink(missing_ok=True)
os.sync()
print('Original English page restored.')
'''
(ROOT / 'dist/restore.py').write_text(restore)
payload = {'manifest':manifest, 'content':base64.b64encode(content).decode(), 'restore':base64.b64encode(restore.encode()).decode()}
client = paramiko.SSHClient()
client.load_host_keys(str(ROOT.parent / '.local/printer-ui_known_hosts'))
extra_known_hosts = os.environ.get('U1_KNOWN_HOSTS')
if extra_known_hosts:
    client.load_host_keys(extra_known_hosts)
if HOST != '192.168.2.159' and not client.get_host_keys().lookup(HOST):
    previous_keys = client.get_host_keys().lookup('192.168.2.159')
    if not previous_keys:
        raise RuntimeError('Previously verified printer host key is unavailable')
    for key_type, key in previous_keys.items():
        client.get_host_keys().add(HOST, key_type, key)
client.set_missing_host_key_policy(paramiko.RejectPolicy())
try:
    client.connect(HOST, username='root', password=os.environ.get('U1_SSH_PASSWORD', 'snapmaker'), allow_agent=False, look_for_keys=False, timeout=10)
    stdin, stdout, stderr = client.exec_command('python3 -c ' + shlex.quote(remote_code), timeout=30)
    stdin.write(json.dumps(payload)); stdin.flush(); stdin.channel.shutdown_write()
    text = stdout.read().decode(); error = stderr.read().decode()
    status = stdout.channel.recv_exit_status()
    if status: raise RuntimeError(error)
    result = json.loads(text)
    (ROOT / 'dist/install-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))
finally:
    client.close()
