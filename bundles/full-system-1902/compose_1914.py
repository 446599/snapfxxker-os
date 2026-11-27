#!/usr/bin/env python3
"""Compose a FULL 1902.14 candidate, including freshly compiled native UI.

Retain the verified 1902.7 composition, then stage the reviewed native
material runtime, map every module, pin the new screen runtime and check
the actual embedded bytes. This script only runs on extracted rootfs.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import tarfile

import compose as base
import runtime_fixes
from hub_port_order import patch_engine as patch_hub_port_order
from offline_ace_scan import patch_engine as patch_offline_ace_scan

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'ace2k-native-slot-exit'))
from patch_material_runtime import stage, patch_mount_map, MODULES
from package_screen_1913 import verify_screen, SCREEN_VERSION, SCREEN_PIN

VERSION='2.1.2-233-1902.14'
PROVIDER=base.FIELD_VERSION+'-paxx.14'
SCREEN_OLD='helixscreen-233-1.2.0-233.20261124.4.tar.gz'
SCREEN_OLD_SHA='2a967f0884c33993c547c4cc4a4260662e04dabb0de4b9d9e7bbbfc830f22ff5'


def require(ok,message):
    if not ok:
        raise ValueError(message)


def set_scalar(s,key,value):
    s,n=re.subn(r'^'+re.escape(key)+r'="[^"\n]*"$',
                lambda _:key+'="'+value+'"',s,flags=re.M)
    require(n==1,'package field missing or ambiguous: '+key)
    return s


def compose(root,field,common,work,screen,screen_report,commit):
    root,work=root.resolve(),work.resolve()
    sm=json.loads(screen_report.read_text())
    require(sm['integration_commit']==commit,'screen built from a different integration commit')
    require(sm['source_pin']==SCREEN_PIN,'screen source pin mismatch')
    verify_screen(screen,sm)
    new=base.compose(root,field,common,work)
    report_path=work/'composition.json'
    runtime_fixes.install(root,report_path)
    companion=root/'usr/local/lib/u1-device-service/device_service.py'
    stage(new,companion)
    engine=new/'klipper/extras/ace.py'
    backend=new/'web/backend/main.py'
    engine.write_text(patch_offline_ace_scan(patch_hub_port_order(engine.read_text())))
    require("register_command('ACE_SLOT_EXIT'" in engine.read_text()
            and "'multiace/material_batch'" in engine.read_text()
            and '/api/material/batch/{operation}' in backend.read_text()
            and 'route.startswith("/material/batch/")' in companion.read_text(),
            'material service route missing')
    report=json.loads(report_path.read_text())
    previous=root/report['provider_archive']
    previous_sha=report['provider_sha256']
    common_path=root/'usr/local/share/multiace/common.sh'
    common_path.write_text(patch_mount_map(common_path.read_text()))
    (new/'VERSION').write_text(PROVIDER+'\n')
    required=sorted(set(report['required_files'])|
                    {'klipper/extras/'+name+'.py' for name in MODULES})
    for name in required:
        require((new/name).is_file(),'missing native provider file: '+name)
    for name in MODULES:
        compile((new/'klipper/extras'/(name+'.py')).read_text(),name,'exec')
    pkg=previous.parent/f'multiace-managed-{PROVIDER}.tar.gz'
    with tarfile.open(pkg,'w:gz',compresslevel=6) as t:
        t.add(new,arcname='multiace-'+PROVIDER)
    digest=base.sha(pkg)
    definition=root/'usr/local/share/extended-pkg/multiace'
    s=definition.read_text()
    require(previous_sha in s and previous.name in s,'old provider pin mismatch')
    s=base.replace_once(s,previous_sha,digest)
    s=base.replace_once(s,previous.name,pkg.name)
    s=base.replace_once(s,'PKG_VERSION="'+base.PROVIDER+'"','PKG_VERSION="'+PROVIDER+'"')
    s,n=re.subn(r'    for required in \\\n.*?; do\n',
        lambda _:'    for required in \\\n'+' \\\n'.join('        '+x for x in required)+'; do\n',s,flags=re.S)
    require(n==1,'required provider file list anchor mismatch')
    definition.write_text(s)
    previous.unlink()
    # Replace the actual released screen archive and its pin, not merely an
    # unreferenced payload. Existing user settings remain in persistent storage.
    old_screen=root/'usr/local/share/233-firmware'/SCREEN_OLD
    require(base.sha(old_screen)==SCREEN_OLD_SHA,'unexpected base screen')
    sd=root/'usr/local/share/extended-pkg/helixscreen'
    st=sd.read_text()
    require(SCREEN_OLD in st and SCREEN_OLD_SHA in st,'base screen definition mismatch')
    screen_dest=old_screen.with_name('helixscreen-233-'+SCREEN_VERSION+'.tar.gz')
    shutil.copyfile(screen,screen_dest)
    require(base.sha(screen_dest)==sm['sha256'],'screen copy mismatch')
    st=set_scalar(st,'PKG_VERSION',SCREEN_VERSION)
    st=set_scalar(st,'PKG_URL','file:///usr/local/share/233-firmware/'+screen_dest.name)
    st=set_scalar(st,'PKG_SHA256',sm['sha256'])
    sd.write_text(st)
    old_screen.unlink()
    fullversion=root/'etc/FULLVERSION'
    require(fullversion.is_file(),'base FULLVERSION missing')
    fullversion.write_text(VERSION+'\n')
    report.update(provider=PROVIDER,provider_archive=str(pkg.relative_to(root)),
                  provider_sha256=digest,required_files=required,engine_sha256=base.sha(engine),
                  web_sha256=base.sha(backend),screen_archive=str(screen_dest.relative_to(root)),
                  screen_sha256=sm['sha256'],screen_binary_sha256=sm['binary_sha256'],
                  screen_source_pin=SCREEN_PIN,screen_package_version=SCREEN_VERSION,
                  integration_commit=commit,firmware_version=VERSION,
                  scope='1902.14 route-specific park return phase 1 candidate; native screen and provider; ACE MCU unchanged; real parked route confirmation required; not hardware validated',
                  hardware_verified=False)
    files=set(report['runtime_files'])|{'usr/local/share/multiace/common.sh',
             'usr/local/share/extended-pkg/multiace','usr/local/share/extended-pkg/helixscreen','etc/FULLVERSION'}
    report['runtime_files']=sorted(files)
    report['runtime_file_sha256']={x:base.sha(root/x) for x in files}
    report['route_file_sha256'].update({x:base.sha(new/x) for x in report['route_file_sha256']})
    report['route_file_sha256'].update({'klipper/extras/'+x+'.py':base.sha(new/'klipper/extras'/(x+'.py')) for x in MODULES})
    report_path.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    return new


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for k in ('root','field','common','work','screen','screen_report'):
        p.add_argument(k,type=Path)
    p.add_argument('commit')
    a=p.parse_args()
    compose(a.root,a.field,a.common,a.work,a.screen,a.screen_report,a.commit)
