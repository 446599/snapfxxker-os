#!/usr/bin/env python3
"""Move only installed MultiACE provider releases off the small OEM filesystem."""
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile

OLD = Path('/oem/apps/multiace')
NEW = Path('/userdata/233-apps/multiace')
VERSION_NAME = re.compile(r'multiace-[A-Za-z0-9][A-Za-z0-9._+-]*\Z')
TOMB = '.relocating-'

def fingerprint(root):
    """Compare contents, symlink targets and metadata, without following links."""
    out = {}
    root_dev = root.lstat().st_dev
    def visit(p):
        s = p.lstat()
        if s.st_dev != root_dev:
            raise ValueError('nested filesystem: ' + str(p))
        key = str(p.relative_to(root))
        meta = (s.st_mode, s.st_uid, s.st_gid)
        if stat.S_ISLNK(s.st_mode):
            out[key] = meta + (os.readlink(p),)
        elif stat.S_ISDIR(s.st_mode):
            out[key] = meta
            for child in sorted(p.iterdir()):
                visit(child)
        elif stat.S_ISREG(s.st_mode):
            h = hashlib.sha256()
            with p.open('rb') as f:
                for block in iter(lambda: f.read(1024 * 1024), b''):
                    h.update(block)
            out[key] = meta + (s.st_size, h.hexdigest())
        else:
            raise ValueError('unsupported provider entry: ' + str(p))
    visit(root)
    return out

def validate_provider(path, name):
    if (not VERSION_NAME.fullmatch(name) or path.is_symlink()
            or not path.is_dir()
            or (path / 'VERSION').read_text().strip() != name[len('multiace-'):]
            or not (path / 'klipper/extras/ace.py').is_file()
            or not (path / 'web/backend/main.py').is_file()):
        raise ValueError('not a recognized managed provider: ' + str(path))

def check_mounts():
    if not os.path.ismount('/userdata') or not os.path.ismount('/oem'):
        raise RuntimeError('OEM/userdata mount not ready')
    if os.stat('/userdata').st_dev == os.stat('/oem').st_dev:
        raise RuntimeError('userdata must be a different filesystem from OEM')
    fs = os.statvfs('/userdata')
    if fs.f_flag & getattr(os, 'ST_NOEXEC', 0):
        raise RuntimeError('userdata is mounted noexec; cannot run provider')
    # A symlink or nested mount cannot redirect a managed storage root.
    for p in (NEW.parent, NEW, OLD):
        if p.is_symlink():
            raise RuntimeError('unexpected storage symlink: ' + str(p))
        if p.exists() and p.stat().st_dev != os.stat('/userdata' if p == NEW or p == NEW.parent else '/oem').st_dev:
            raise RuntimeError('unexpected storage mount: ' + str(p))

def check_not_mounted(path):
    path = str(path)
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        target = line.split()[4]
        target = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), target)
        if target == path or target.startswith(path + '/'):
            raise RuntimeError('provider contains a mount: ' + target)

def link_old(path, dest):
    if path.is_symlink() and path.resolve() == dest.resolve():
        return
    if os.path.lexists(path):
        raise RuntimeError('cannot replace existing rollback path: ' + str(path))
    path.symlink_to(dest)

def migrate(old, new, verify_mounts=True, sync=os.sync):
    old, new = Path(old), Path(new)
    if verify_mounts:
        check_mounts()
    if old.is_symlink() or new.is_symlink():
        raise RuntimeError('unexpected provider-root symlink')
    new.mkdir(parents=True, exist_ok=True)
    if not old.exists():
        return []
    if not old.is_dir():
        raise RuntimeError('legacy provider root is not a directory')
    old_latest = old / 'latest'
    legacy_name = None
    if old_latest.is_symlink():
        target = Path(os.readlink(old_latest))
        if not target.is_absolute() and len(target.parts) == 1 and VERSION_NAME.fullmatch(target.name):
            legacy_name = target.name
        elif target.is_absolute() and target.parent == old and VERSION_NAME.fullmatch(target.name):
            legacy_name = target.name
    moved = []
    # Recover an interrupted deletion only when every remaining byte matches
    # an already completed copy. Never remove an unrelated directory.
    for src in sorted(old.iterdir()):
        name = src.name[len(TOMB):] if src.name.startswith(TOMB) else src.name
        if not VERSION_NAME.fullmatch(name):
            continue
        dest = new / name
        original = old / name
        if src.is_symlink():
            if src.resolve() == dest.resolve() and dest.is_dir():
                continue
            raise RuntimeError('unexpected provider symlink: ' + str(src))
        if not src.is_dir():
            continue
        if verify_mounts:
            check_not_mounted(src)
        interrupted = src.name.startswith(TOMB)
        if interrupted:
            validate_provider(dest, name)
            remaining, complete = fingerprint(src), fingerprint(dest)
            if any(complete.get(k) != v for k, v in remaining.items()):
                raise RuntimeError('interrupted provider copy does not match: ' + name)
        else:
            validate_provider(src, name)
            before = fingerprint(src)
            if not os.path.lexists(dest):
                with tempfile.TemporaryDirectory(prefix='.copy-', dir=new) as tmp:
                    stage = Path(tmp) / name
                    subprocess.run(['cp', '-a', str(src), str(stage)], check=True)
                    if fingerprint(stage) != before:
                        raise RuntimeError('provider copy verification failed: ' + name)
                    stage.rename(dest)
                    sync()
            validate_provider(dest, name)
            if fingerprint(dest) != before or fingerprint(src) != before:
                raise RuntimeError('provider contents differ: ' + name)
            tomb = old / (TOMB + name)
            if os.path.lexists(tomb):
                raise RuntimeError('previous relocation still present: ' + name)
            src.rename(tomb)
            src = tomb
        # Retain a usable fallback for the package manager before releasing
        # old files, but never replace a newer existing latest link.
        if legacy_name == name and not os.path.lexists(new / 'latest'):
            (new / 'latest').symlink_to(name)
        sync()
        shutil.rmtree(src)
        link_old(original, dest)
        sync()
        moved.append(name)
        print('relocated and verified: ' + name, flush=True)
    if legacy_name and not os.path.lexists(new / 'latest'):
        dest = new / legacy_name
        if dest.is_dir() and not dest.is_symlink():
            validate_provider(dest, legacy_name)
            (new / 'latest').symlink_to(legacy_name)
    return moved

def report():
    for p in ('/oem', '/userdata'):
        s = os.statvfs(p)
        print('%s free_bytes=%d free_inodes=%d' % (p, s.f_bavail * s.f_frsize, s.f_favail), flush=True)

if __name__ == '__main__':
    report()
    migrate(OLD, NEW)
    report()
