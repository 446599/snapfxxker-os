#!/usr/bin/env python3
"""Stage a pinned upstream multiACE 1.90.2 checkout; never modifies a printer."""
import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = json.loads((HERE / 'source-patches.json').read_text())
EXTRAS = Path('multiace/klipper/extras')

def git_blob(path):
    data = path.read_bytes()
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()

def stage(source, target):
    head = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    if head != SPEC['source_commit']:
        raise ValueError('wrong upstream revision: ' + head)
    if target.exists():
        raise ValueError('use a new empty staging destination')
    changes = {}
    for name, digest in SPEC['blobs'].items():
        relative = Path(name) if '/' in name else EXTRAS / name
        path = source / relative
        if git_blob(path) != digest:
            raise ValueError('modified upstream source: ' + name)
        text = path.read_text(encoding='utf-8')
        for old, new in SPEC['edits'][name]:
            if text.count(old) != 1:
                raise ValueError('source anchor mismatch: ' + name)
            text = text.replace(old, new, 1)
        if name == 'ace_protocol.py':
            text += '\n\n' + (HERE / 'lane_contract.py').read_text() + '\n\n' + (HERE / 'lane_io.py').read_text()
            text += '\n\n' + (HERE / 'ace2k_discovery.py').read_text()
        if relative.suffix == '.py':
            compile(text, name, 'exec')
        changes[relative] = text
    shutil.copytree(source, target, ignore=shutil.ignore_patterns('.git', '__pycache__', '*.pyc'))
    for relative, content in changes.items():
        (target / relative).write_text(content, encoding='utf-8')
    print('Prepared 1.90.2 source candidate; existing firmware packages unchanged.')

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('upstream', type=Path)
    p.add_argument('staging', type=Path)
    a = p.parse_args()
    stage(a.upstream, a.staging)
