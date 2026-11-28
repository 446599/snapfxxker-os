#!/usr/bin/env python3
"""Repack the pinned successful 1902.15 unified-web image, no MCU changes."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import tempfile

from acceptance_1916 import patch_engine, patch_package_manager, replace_once

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[1]
BASE_VERSION='2.1.2-233-1902.15'
BASE_COMMIT='e8968dfb9e612a43d19d2127b688022095946428'
VERSION='2.1.2-233-1902.16'
PROVIDER='1.90.2-233.20261126-paxx.16'
SCREEN_VERSION='1.2.0-233.20261128.1916'
IDLE='usr/local/lib/u1-device-service/idle_checks.py'
PKGMGR='usr/local/bin/extended-pkg'
RUNTIME='klipper/extras/material_batch_runtime.py'
ENGINE='klipper/extras/ace.py'


def sha(p):
    with p.open('rb') as fp:
        return hashlib.file_digest(fp,'sha256').hexdigest()


def require(ok,message):
    if not ok:
        raise ValueError(message)


def repin(root,package,old_version,new_version,old_archive,new_archive,old_sha,new_sha):
    definition=root/'usr/local/share/extended-pkg'/package
    s=definition.read_text()
    s=replace_once(s,'PKG_VERSION="'+old_version+'"','PKG_VERSION="'+new_version+'"')
    s=replace_once(s,'PKG_URL="file:///'+old_archive+'"','PKG_URL="file:///'+new_archive+'"')
    if old_sha!=new_sha:
        s=replace_once(s,'PKG_SHA256="'+old_sha+'"','PKG_SHA256="'+new_sha+'"')
    else:
        require('PKG_SHA256="'+old_sha+'"' in s,'old checksum pin mismatch')
    definition.write_text(s)


def compose(root,previous,output,commit):
    report=json.loads(previous.read_text())
    require(report['firmware_version']==BASE_VERSION
            and report['integration_commit']==BASE_COMMIT
            and (root/'etc/FULLVERSION').read_text().strip()==BASE_VERSION,
            'expected the exact successful 1902.15 unified web build')
    for path,digest in report['runtime_file_sha256'].items():
        require(sha(root/path)==digest,'base runtime mismatch: '+path)
    for key in ('screen','provider'):
        require(sha(root/report[key+'_archive'])==report[key+'_sha256'],
                'base archive mismatch: '+key)
    require((root/'usr/local/share/u1-web/index.html').is_file(),'unified web missing')
    old=root/report['provider_archive']
    provider_new=old.with_name('multiace-managed-'+PROVIDER+'.tar.gz')
    with tempfile.TemporaryDirectory(prefix='1916-provider-') as td:
        work=Path(td)
        with tarfile.open(old,'r:gz') as archive:
            archive.extractall(work,filter='data')
        roots=list(work.iterdir())
        require(len(roots)==1 and roots[0].is_dir(),'unexpected provider root')
        p=roots[0]
        for path,digest in report['route_file_sha256'].items():
            require(sha(p/path)==digest,'base provider source mismatch: '+path)
        source=REPO/'bundles/ace2k-native-slot-exit/material_batch_runtime.py'
        runtime=source.read_text().replace('from host_native_exit import','from .host_native_exit import')
        runtime=runtime.replace('from park_return import','from .park_return import')
        compile(runtime,RUNTIME,'exec')
        (p/RUNTIME).write_text(runtime)
        (p/ENGINE).write_text(patch_engine((p/ENGINE).read_text()))
        (p/'VERSION').write_text(PROVIDER+'\n')
        for path in report['required_files']:
            require((p/path).is_file(),'missing provider runtime: '+path)
        route_hashes={path:sha(p/path) for path in report['route_file_sha256']}
        with tarfile.open(provider_new,'w:gz',compresslevel=6) as archive:
            archive.add(p,arcname='multiace-'+PROVIDER)
    new_archive=str(provider_new.relative_to(root))
    new_sha=sha(provider_new)
    repin(root,'multiace',report['provider'],PROVIDER,report['provider_archive'],
          new_archive,report['provider_sha256'],new_sha)
    old.unlink()
    report.update(provider=PROVIDER,provider_archive=new_archive,provider_sha256=new_sha,
                  engine_sha256=route_hashes[ENGINE],route_file_sha256=route_hashes)
    # The .14 build already compiled this exact desired binary. The failure
    # was deployment, not screen code. Give the unchanged archive a new
    # package identity; preserve the original binary build provenance.
    old_screen=root/report['screen_archive']
    new_screen=old_screen.with_name('helixscreen-233-'+SCREEN_VERSION+'.tar.gz')
    require(not new_screen.exists(),'new screen archive already exists')
    old_screen.rename(new_screen)
    screen_archive=str(new_screen.relative_to(root))
    repin(root,'helixscreen',report['screen_package_version'],SCREEN_VERSION,
          report['screen_archive'],screen_archive,report['screen_sha256'],report['screen_sha256'])
    report.update(screen_build_package_version=report['screen_package_version'],
                  screen_package_version=SCREEN_VERSION,screen_archive=screen_archive)
    # Use the same shared checker in the device service, not fabricated
    # location_hold/slot_action status placeholders.
    idle_source=REPO/'overlays/233-native-ui/root'/IDLE
    shutil.copyfile(idle_source,root/IDLE)
    compile((root/IDLE).read_text(),IDLE,'exec')
    (root/PKGMGR).write_text(patch_package_manager((root/PKGMGR).read_text()))
    (root/'etc/FULLVERSION').write_text(VERSION+'\n')
    changed={IDLE,PKGMGR,'etc/FULLVERSION',
             'usr/local/share/extended-pkg/multiace','usr/local/share/extended-pkg/helixscreen'}
    report['runtime_files']=sorted(set(report['runtime_files'])|changed)
    report['runtime_file_sha256'].update({x:sha(root/x) for x in changed})
    report.update(firmware_version=VERSION,integration_commit=commit,
                  acceptance_base_commit=BASE_COMMIT,hardware_verified=False,
                  scope='1902.16 P0 acceptance fixes, retained unified web, shared verified-length park guard; no closed-loop thermal calibration or hardware validation')
    output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('root','previous','output'):
        parser.add_argument(key,type=Path)
    parser.add_argument('commit')
    a=parser.parse_args()
    compose(a.root,a.previous,a.output,a.commit)
