#!/usr/bin/env python3
"""Serialize init-script invocations; kernel releases the lock on process exit."""
import fcntl
import os
from pathlib import Path
import subprocess
import sys

script = Path(sys.argv[1])
name = script.name
if name not in ('S98multiace-web', 'S58u1-device-service', 'S47multiace-storage'):
    raise SystemExit('unknown 233 service')
directory = Path('/run/233-service-locks')
directory.mkdir(mode=0o755, exist_ok=True)
fd = os.open(directory / (name + '.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
try:
    fcntl.flock(fd, fcntl.LOCK_EX)
    env = dict(os.environ, U1_LOCKED_SERVICE=name, PYTHONDONTWRITEBYTECODE='1')
    raise SystemExit(subprocess.call(['/bin/sh', str(script), *sys.argv[2:]], env=env))
finally:
    os.close(fd)
