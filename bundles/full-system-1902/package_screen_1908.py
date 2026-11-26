#!/usr/bin/env python3
"""Package the freshly built screen and its matching runtime resources."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys
import tarfile

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[1]
SCREEN_PIN='4edbd50feb36e47d072b5cd4e516b9792b8b4ea9'
SCREEN_VERSION='1.2.0-233.20261126.1908'
NEEDED=('bin/helix-screen','bin/helix-splash','bin/helix-watchdog',
        'ui_xml/settings_panel.xml','ui_xml/firmware_settings.xml',
        'assets/config/themes/defaults/u1-warm.json','config/settings.json.template',
        'assets/config/presets/snapmaker_u1.json')
BINARIES=NEEDED[:3]


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def arm64(h):
    return h[:6]==b'\x7fELF\x02\x01' and int.from_bytes(h[18:20],'little')==183


def verify_screen(archive,report):
    if report.get('source_pin')!=SCREEN_PIN or report.get('screen_version')!=SCREEN_VERSION:
        raise ValueError('wrong screen source/version')
    if sha(archive)!=report.get('sha256'):
        raise ValueError('screen archive checksum mismatch')
    with tarfile.open(archive,'r:gz') as t:
        names={m.name for m in t.getmembers() if m.isfile()}
        if not all('helixscreen/'+x in names for x in NEEDED):
            raise ValueError('incomplete screen runtime')
        for x in BINARIES:
            data=t.extractfile('helixscreen/'+x).read()
            if not arm64(data[:20]) or hashlib.sha256(data).hexdigest()!=report['binaries'][x]:
                raise ValueError('not matching compiled AArch64 binary: '+x)
            if not t.getmember('helixscreen/'+x).mode&0o111:
                raise ValueError('screen executable mode missing')
        if report['binary_sha256']!=report['binaries']['bin/helix-screen']:
            raise ValueError('screen digest mismatch')
        if not all( ('/material/batch/'+op).encode() in t.extractfile('helixscreen/bin/helix-screen').read() for op in ('preview','start','status','stop','parking_preview')):
            raise ValueError('compiled binary missing material batch endpoints')
    return True


def package(source,binaries,output,commit,started):
    sys.path.insert(0,str(REPO/'scripts/native-ui'))
    from package_runtime import collect
    if not re.fullmatch('[0-9a-f]{40}',commit):raise ValueError('invalid integration commit')
    files=collect(source,binaries,Path('/etc/ssl/certs/ca-certificates.crt'),
                  REPO/'bundles/native-ui/licenses')
    for x in NEEDED:
        if x not in files:raise ValueError('missing screen resource '+x)
    for x in BINARIES:
        p=files[x]
        if not arm64(p.read_bytes()[:20]) or p.stat().st_mtime<started:
            raise ValueError('missing newly compiled AArch64 executable '+x)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('wb') as f:
        with gzip.GzipFile(filename='',fileobj=f,mtime=0,compresslevel=6) as gz:
            with tarfile.open(fileobj=gz,mode='w') as t:
                for name,p in sorted(files.items()):
                    t.add(p,arcname='helixscreen/'+name,recursive=False,
                          filter=lambda i:normalize(i))
    record=dict(source_pin=SCREEN_PIN,screen_version=SCREEN_VERSION,
                integration_commit=commit,sha256=sha(output),
                binaries={x:sha(files[x]) for x in BINARIES},
                binary_sha256=sha(files['bin/helix-screen']),
                source_files={x:sha(source/x) for x in (
                    'include/u1_filament_overview.h','include/screen_batch_selection.h',
                    'src/ui/filament/u1_filament_overview.cpp',
                    'src/printer/ams_backend_multiace.cpp',
                    'src/ui/print/ui_panel_print_status.cpp')},
                bytes=output.stat().st_size,hardware_verified=False)
    verify_screen(output,record)
    output.with_suffix('.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(record,indent=2))


def normalize(i):
    i.uid=i.gid=0;i.uname=i.gname='root';i.mtime=0
    return i


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('source',type=Path);p.add_argument('binaries',type=Path)
    p.add_argument('output',type=Path);p.add_argument('commit');p.add_argument('started',type=float)
    a=p.parse_args()
    package(a.source,a.binaries,a.output,a.commit,a.started)
