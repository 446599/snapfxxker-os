#!/usr/bin/env python3
"""Integrate the acceptance fixes into the extracted rootfs, not the device."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parents[1] / 'overlays/233-native-ui/root'

def replace_once(s, old, new):
    if s.count(old) != 1:
        raise ValueError('runtime anchor mismatch: ' + old[:100])
    return s.replace(old, new, 1)

def install(root, composition):
    files = []
    def copy(src, rel, mode):
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        dst.chmod(mode)
        files.append(rel)
    copy(HERE/'runtime/multiace-relocate-storage.py', 'usr/local/lib/multiace-relocate-storage.py', 0o644)
    copy(HERE/'runtime/233-service-lock.py', 'usr/local/lib/233-service-lock.py', 0o644)
    copy(HERE/'runtime/S47multiace-storage', 'etc/init.d/S47multiace-storage', 0o755)
    copy(HERE/'runtime/S58u1-device-service', 'etc/init.d/S58u1-device-service', 0o755)

    # Restore the already released companion source set if the base lacked it.
    companion = ['launch.py', 'device_service.py', 'idle_checks.py', 'slot_config.py']
    base = 'usr/local/lib/u1-device-service/'
    if not all((root/(base+n)).is_file() for n in companion):
        for n in companion:
            copy(SOURCE/(base+n), base+n, 0o644)
    for n in companion:
        rel = base+n
        compile((root/rel).read_text(), rel, 'exec')
        if rel not in files:
            files.append(rel)

    # Both boot and restart use the same init entry; the lock is outside
    # persistent storage so it works even while OEM is full.
    rel = 'etc/init.d/S98multiace-web'
    p = root/rel
    s = p.read_text()
    s = replace_once(s, 'PORT="7126"\n', '''PORT="7126"
export PYTHONDONTWRITEBYTECODE=1
if [ "${U1_LOCKED_SERVICE:-}" != S98multiace-web ]; then
    exec /usr/bin/python3 -B /usr/local/lib/233-service-lock.py "$0" "$@"
fi
''')
    s = replace_once(s, '''    [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null
}
''', '''    [ -s "$PIDFILE" ] || return 1
    pid=$(cat "$PIDFILE")
    kill -0 "$pid" 2>/dev/null && tr '\\000' ' ' < "/proc/$pid/cmdline" | grep -Fq 'uvicorn main:app'
}
web_ready() {
    web_pid_alive && curl -fsS --max-time 2 http://127.0.0.1:7126/api/health >/dev/null 2>&1
}
''')
    s = replace_once(s, '''        echo "$NAME already running"
        return 0
''', '''        if web_ready; then echo "$NAME already running"; return 0; fi
        echo "$NAME process exists but is not ready" >&2
        return 1
''')
    s = replace_once(s, '''    touch "$LOGFILE"
''', '''    if ! touch "$LOGFILE"; then
        LOGFILE=/tmp/multiace_web.log
        echo "$NAME persistent log unavailable; using $LOGFILE" >&2
    fi
''')
    s = replace_once(s, '''    sleep 2
    if web_pid_alive; then
        echo "$NAME started"
        return 0
    fi
''', '''    i=0
    while [ "$i" -lt 8 ]; do
        if web_ready; then echo "$NAME started"; return 0; fi
        sleep 1
        i=$((i+1))
    done
''')
    # The backend's actual health endpoint is probed; never interpret PID alone
    # as a successful start. No blanket kill of any other 7126 listener.
    p.write_text(s)
    files.append(rel)

    for rel in files:
        p = root/rel
        if '/init.d/' in rel:
            subprocess.run(['sh','-n',str(p)], check=True)
        if rel.endswith('.py'):
            compile(p.read_text(), rel, 'exec')
    report = json.loads(composition.read_text())
    report['runtime_files'] = sorted(files)
    report['runtime_file_sha256'] = {
        rel: hashlib.sha256((root/rel).read_bytes()).hexdigest()
        for rel in files
    }
    composition.write_text(json.dumps(report, indent=2)+'\n')

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('root', type=Path)
    p.add_argument('composition', type=Path)
    args = p.parse_args()
    install(args.root, args.composition)
