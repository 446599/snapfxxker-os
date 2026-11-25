#!/usr/bin/env python3
"""Guarded package-only deployment of the merged .15.113 MultiACE selector."""
import hashlib, importlib.util, json, os, shlex, sys, tarfile, time, uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent
HERE = Path(__file__).resolve().parent
VER = '1.27b-233.20261122-flow.15.113'
OLD = '1.27b-233.20261122-flow.15.112'
FW = '2.1.2-233-paxx8d8af0d.1'
ARCHIVE = ROOT/'overlays/233-dev.2/root/usr/local/share/233-firmware'/f'multiace-managed-{VER}.tar.gz'
DESC_SOURCE = ROOT/'overlays/233-dev.2/root/usr/local/share/extended-pkg/multiace'
EXPECTED_SHA = 'd49cf0b24776f3403cf9f32c6e293ef651f7a89a7fd86498a88f79c9f72414e8'
APP='/oem/apps/multiace'; CACHE='/userdata/firmware/provider-cache'; RUNTIME='/userdata/multiace-provider-runtime'
DESC='/usr/local/share/extended-pkg/multiace'; WEB='/etc/init.d/S98multiace-web'; KLIPPER='/etc/init.d/S60klipper'

def require(ok,msg):
    if not ok: raise RuntimeError(msg)
def sha(b): return hashlib.sha256(b).hexdigest()
def q(s): return shlex.quote(str(s))
def load_dev():
    p=WORKSPACE/'verification/deploy-u1-178-20261122/device.py'
    spec=importlib.util.spec_from_file_location('u1dev',p); m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
    m.POSTFLASH_HOST_KEY=WORKSPACE/'verification/deploy-paxx8d8af0d-20261124/verified-boot-hostkey.sha256'
    return m

def remote(d,c,cmd,timeout=30,check=True):
    _,o,e=c.exec_command(cmd,timeout=timeout); out=o.read().decode(errors='replace');err=e.read().decode(errors='replace');rc=o.channel.recv_exit_status()
    if check and rc: raise RuntimeError(f'remote rc={rc}: {cmd}\n{err[-1000:]}\n{out[-1000:]}')
    return rc,out.strip(),err.strip()

def compact(d,snap):
    s=snap['status']; a=s.get('ace',{})
    for h in snap['heaters']: require(s.get(h,{}).get('target')==0 and s.get(h,{}).get('power')==0,f'heater active: {h}')
    require(s['webhooks']['state']=='ready','Klipper not ready')
    require(s['print_stats']['state'] in ('standby','complete','cancelled'),'print active or paused')
    require(s['idle_timeout']['state'] in ('Idle','Ready'),'machine not idle')
    require(s['virtual_sdcard']['is_active'] is False,'virtual SD active')
    require(s.get('machine_state_manager',{}).get('main_state')==0,'machine state not idle')
    require(a.get('status')=='ready','ACE not ready')
    require(not a.get('feed_operations') and not a.get('swap_in_progress') and not a.get('unload_all_active'),'ACE operation active')
    require((a.get('slot_action') or {}).get('state') not in ('running','preparing'),'slot action active')
    units=a.get('aces',[]); require(len(units)==4 and all(x.get('connected') and x.get('status')=='ready' and not x.get('fw_hold') for x in units),'not all ACEs ready')
    return {'print':s['print_stats']['state'],'idle':s['idle_timeout']['state'],'units':len(units),'feed_operations':len(a.get('feed_operations',[])),'slot_action':(a.get('slot_action') or {}).get('state')}

def main():
    archive=ARCHIVE.read_bytes(); require(sha(archive)==EXPECTED_SHA,'local archive checksum mismatch')
    with tarfile.open(ARCHIVE,'r:gz') as t:
        members=t.getmembers(); prefix=f'multiace-{VER}/'; files={}
        for x in members:
            require(x.isfile() and x.name.startswith(prefix),'unexpected archive member '+x.name)
            data=t.extractfile(x).read(); rel=x.name[len(prefix):]
            files[rel]={'bytes':len(data),'sha256':sha(data)}
            if rel.endswith('.py'): compile(data,rel,'exec')
    require('VERSION' in files and len(files)>800,'package inventory looks incomplete')
    desc=DESC_SOURCE.read_text()
    desc=desc.replace(f'file:///usr/local/share/233-firmware/multiace-managed-{VER}.tar.gz',f'file://{CACHE}/multiace-managed-{VER}.tar.gz')
    require('PKG_VERSION="'+VER+'"' in desc and f'PKG_SHA256="{EXPECTED_SHA}"' in desc,'descriptor mismatch')
    (HERE/'manifest.json').write_text(json.dumps({'version':VER,'sha256':EXPECTED_SHA,'files':files},indent=2)+'\n')
    (HERE/'package-definition').write_text(desc)
    d=load_dev(); evidence={'device':{},'before':{},'package':{'version':VER,'sha256':EXPECTED_SHA,'bytes':len(archive),'files':len(files)}}
    with d.connect() as c:
        snap=d.snapshot(c); require(snap['mac']=='8c:cd:fe:58:56:1d' and snap['serial']=='ff7684960ab92ead','device identity mismatch')
        require(snap['version']==FW,'unexpected firmware baseline')
        evidence['device']={'mac':snap['mac'],'serial':snap['serial'],'firmware':snap['version'],'boot_id':snap['boot_id']}
        evidence['before']=compact(d,snap)
        latest=remote(d,c,f'readlink {APP}/latest')[1]
        require(latest==f'multiace-{OLD}',f'unexpected current provider link {latest}')
        require(remote(d,c,f'cat {APP}/latest/VERSION')[1]==OLD,'old provider version mismatch')
        old_desc=remote(d,c,f'cat {DESC}')[1]+'\n'
        require(f'PKG_VERSION="{OLD}"' in old_desc,'device package pin changed')
        old_desc_sha=sha(old_desc.encode()); evidence['previous_latest']=latest;evidence['previous_definition_sha256']=old_desc_sha
        # Confirm the current local service endpoint, printer queue, and upgrade lock before staging.
        remote(d,c,'curl -fsS --max-time 6 http://127.0.0.1:7126/api/state >/dev/null && test ! -e /tmp/.system_upgrade.lock && test -x '+KLIPPER)
        queue=json.loads(remote(d,c,'curl -fsS --max-time 5 http://127.0.0.1:7125/server/job_queue/status')[1])
        require(not queue.get('result',{}).get('queued_jobs'),'print queued')
        space=remote(d,c,'df -Pk /userdata | awk \'NR==2{print $4}\'; df -Pi /oem | awk \'NR==2{print $4}\'')[1].splitlines()
        require(int(space[0])*1024>len(archive)*3,'insufficient userdata space')
        require(int(space[1])>1200,'insufficient /oem inodes for version link/installer metadata')
        # Preserve full previous definition off-device and create protected staging paths.
        (HERE/'previous-definition').write_text(old_desc)
        cache_archive=f'{CACHE}/multiace-managed-{VER}.tar.gz'; cache_desc=f'{CACHE}/multiace-definition-{VER}'
        remote(d,c,f'test -w {q(DESC)} && mkdir -p {q(CACHE)} && test ! -e {q(cache_archive)} && test ! -e {q(cache_desc)}')
        with c.open_sftp() as s:
            s.put(str(ARCHIVE),cache_archive+'.part',confirm=True)
            s.put(str(HERE/'package-definition'),cache_desc+'.part',confirm=True)
            s.put(str(HERE/'manifest.json'),f'{CACHE}/manifest-{VER}.json.part',confirm=True)
        require(remote(d,c,f'sha256sum {q(cache_archive)}.part')[1].split()[0]==EXPECTED_SHA,'uploaded archive SHA mismatch')
        require(remote(d,c,f'sha256sum {q(cache_desc)}.part')[1].split()[0]==sha(desc.encode()),'uploaded descriptor SHA mismatch')
        require(remote(d,c,f'sha256sum {q(CACHE+"/manifest-"+VER+".json")}.part')[1].split()[0]==sha((HERE/'manifest.json').read_bytes()),'manifest upload mismatch')
        remote(d,c,f'mv {q(cache_archive)}.part {q(cache_archive)} && mv {q(cache_desc)}.part {q(cache_desc)} && mv {q(CACHE+"/manifest-"+VER+".json")}.part {q(CACHE+"/manifest-"+VER+".json")}')
        name=f'multiace-{VER}'; final=f'{RUNTIME}/{name}'; temp=f'{RUNTIME}/.{name}-{uuid.uuid4().hex}'
        remote(d,c,f'test ! -e {q(final)} && mkdir -m 755 {q(temp)} && tar --strip-components=1 -xzf {q(cache_archive)} -C {q(temp)}')
        # Run the package's own install validator against the extracted runtime on the U1.
        remote(d,c,f'bash -c {q("source \"$1\" && pkg_post_install \"$2\"")} bash {q(cache_desc)} {q(temp)}',timeout=120)
        checker="""import hashlib,json,pathlib,sys
r=pathlib.Path(sys.argv[1]);m=json.load(open(sys.argv[2]));bad=[]
for n,x in m['files'].items():
 p=r/n
 if not p.is_file() or p.stat().st_size!=x['bytes'] or hashlib.sha256(p.read_bytes()).hexdigest()!=x['sha256']:bad.append(n)
if (r/'VERSION').read_text().strip()!=m['version']:bad.append('VERSION')
print(json.dumps({'bad':bad,'files':len(m['files'])}));sys.exit(bool(bad))"""
        check_file=f'{CACHE}/verify-{VER}.py'
        with c.open_sftp() as s:s.put(str((ROOT/'verification/multiace-selector-deploy-20261125/manifest.json')),f'{check_file}.part')
        remote(d,c,f'mv {q(check_file)}.part {q(check_file)}')
        out=remote(d,c,f'/usr/bin/python3 -c {q(checker)} {q(temp)} {q(CACHE+"/manifest-"+VER+".json")}',timeout=120)[1]
        require(not json.loads(out)['bad'],'staged package integrity check failed')
        remote(d,c,f'mv {q(temp)} {q(final)} && ln -s {q(final)} {q(APP+"/"+name)}')
        # Update the persistent provider pin atomically only after fully verifying the staged runtime.
        def atomic_write(src,dst):
            remote(d,c,f'cp -p {q(dst)} {q(CACHE+"/multiace-definition-before-"+VER)} && cp {q(src)} {q(dst+".next")} && chmod --reference={q(dst)} {q(dst+".next")} && mv -f {q(dst+".next")} {q(dst)}')
        evidence['staged_tree']=json.loads(out)
        # Recheck idle immediately before touching services.
        fresh=d.snapshot(c); evidence['before_restart']=compact(d,fresh); require(fresh['boot_id']==snap['boot_id'],'device rebooted while staging')
        web_stopped=False; klipper_stopped=False; latest_switched=False; definition_updated=False
        try:
            atomic_write(cache_desc,DESC); definition_updated=True
            remote(d,c,f'{WEB} stop',timeout=30); web_stopped=True
            remote(d,c,f'{KLIPPER} stop',timeout=90); klipper_stopped=True
            remote(d,c,f'ln -s {q(name)} {q(APP+"/.latest-selector-next")} && mv -Tf {q(APP+"/.latest-selector-next")} {q(APP+"/latest")}')
            latest_switched=True
            require(remote(d,c,f'readlink {APP}/latest')[1]==name,'latest symlink switch failed')
            remote(d,c,f'{KLIPPER} start',timeout=120); klipper_stopped=False
            remote(d,c,f'{WEB} start',timeout=60); web_stopped=False
            deadline=time.monotonic()+180; last='startup pending'; post=None; web=None
            while time.monotonic()<deadline:
                try:
                    post=d.snapshot(c); x=post['status']; a=x.get('ace',{}); units=a.get('aces',[])
                    _,raw,_=remote(d,c,'curl -fsS --max-time 6 http://127.0.0.1:7126/api/state')
                    web=json.loads(raw)
                    if (post['version']==FW and x['webhooks']['state']=='ready' and a.get('status')=='ready'
                        and len(units)==4 and all(u.get('connected') and u.get('status')=='ready' for u in units)
                        and len(web.get('aces',[]))==4):
                        break
                    last=f"not ready: klipper={x['webhooks']['state']} ace={a.get('status')} aces={len(web.get('aces',[]))}"
                except Exception as e:last=str(e)
                time.sleep(3)
            else: raise RuntimeError('post-deploy readiness timed out: '+last)
            after=compact(d,post)
            targets=remote(d,c,'curl -fsS --max-time 5 http://127.0.0.1:7126/api/acefw/status')[1]
            targets_json=json.loads(targets)
            evidence['firmware_status_http']=200
            require({t['id'] for t in targets_json.get('targets',[])}=={'open','ace2k'},'firmware target API missing target(s)')
            require(remote(d,c,f'cat {APP}/latest/VERSION')[1]==VER,'active version mismatch')
            # Make sure live Klipper reports freshly read protocol version and all modules imported.
            units=[{'idx':a.get('idx'),'protocol':a.get('protocol'),'firmware':a.get('firmware'),'firmware_live':a.get('firmware_live'),'connected':a.get('connected')} for a in web.get('aces',[])]
            require(len(units)==4 and all(u['connected'] and u['firmware_live'] for u in units),'live ACE firmware versions were not refreshed')
            evidence['after']=after;evidence['firmware_targets']=targets_json.get('targets');evidence['aces']=units;evidence['ace_flash_test']='not run; selector API verified without flashing any ACE'
        except Exception as failure:
            rollback=[]
            try:
                if web_stopped: remote(d,c,f'{WEB} stop',timeout=30,check=False)
                if klipper_stopped or latest_switched: remote(d,c,f'{KLIPPER} stop',timeout=90,check=False)
                if latest_switched:
                    remote(d,c,f'ln -s {q(latest)} {q(APP+"/.latest-selector-rollback")} && mv -Tf {q(APP+"/.latest-selector-rollback")} {q(APP+"/latest")}')
                if definition_updated:
                    remote(d,c,f'cp {q(CACHE+"/multiace-definition-before-"+VER)} {q(DESC+".rollback")} && mv -f {q(DESC+".rollback")} {q(DESC)}')
                if klipper_stopped or latest_switched: remote(d,c,f'{KLIPPER} start',timeout=120,check=False)
                if web_stopped or latest_switched: remote(d,c,f'{WEB} start',timeout=60,check=False)
                if remote(d,c,f'readlink {APP}/latest')[1]!=latest: rollback.append('latest link did not return to previous version')
                if remote(d,c,f'cat {DESC}')[1]+'\n'!=old_desc: rollback.append('provider definition did not return to previous bytes')
            except Exception as recovery: rollback.append(str(recovery))
            (HERE/'deployment-failure.json').write_text(json.dumps({'error':repr(failure),'rollback_errors':rollback,'previous_latest':latest},indent=2)+'\n')
            raise RuntimeError(f'{failure}; rollback_errors={rollback}') from failure
        (HERE/'deployment-evidence.json').write_text(json.dumps(evidence,indent=2,ensure_ascii=False)+'\n')
        print(json.dumps(evidence,indent=2,ensure_ascii=False))

if __name__=='__main__':
    try: main()
    except Exception as e:
        print('DEPLOYMENT FAILED:',repr(e),file=sys.stderr);raise
