import hashlib, json, os, pathlib, shutil, time, urllib.request
ROOT=pathlib.Path('/oem/apps/multiace/multiace-1.27b/klipper/extras')
LIVE=pathlib.Path('/home/lava/klipper/klippy/extras')
STAGE=pathlib.Path('/tmp/dual-cmd13-deploy-20261107')
files={'ace.py': ('fcbd0217f16009a71d253f01a73c85c5b11e9262b2db32c7f5f4a45a9ff51183','7e247bd1063cda7fa31b48e99ee0fd026cfbc6fa22aeaf736d387de180d48f02')}
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def safe():
 u='http://127.0.0.1/printer/objects/query?print_stats&idle_timeout&virtual_sdcard&ace&extruder&extruder1&extruder2&extruder3&heater_bed'
 s=json.load(urllib.request.urlopen(u,timeout=5))['result']['status'];a=s['ace'];c=a.get('calibration') or {}
 assert s['print_stats']['state'] in ('standby','complete','cancelled','error'), 'print not idle'
 assert s['idle_timeout']['state']=='Idle' and not s['virtual_sdcard']['is_active'], 'printer active'
 assert not a['tag_op']['busy'] and not a['swap_in_progress'] and not a['unload_all_active'], 'ACE operation active'
 assert a['dryer_status']['status']=='stop', 'dryer active'
 assert all((s[x].get('target') or 0)==0 for x in ['extruder','extruder1','extruder2','extruder3','heater_bed']), 'heater active'
 assert c.get('state') in ('idle','cancelled','complete','completed','error'), 'calibration active'
 assert not any(x.get('fw_hold') for x in a.get('aces',[])), 'firmware operation active'
 for unit in a.get('aces',[]):
  assert not any((sl.get('slot_status') or sl.get('state')) in ('feeding','rollback','rollback_assisting','preloading') for sl in unit.get('slots',[])), 'slot motion active'
 return s
safe()
for n,(old,new) in files.items():
 assert sha(ROOT/n)==old and sha(LIVE/n)==old, 'installed file changed: '+n
 assert sha(STAGE/n)==new, 'staged file mismatch: '+n
 assert os.stat(ROOT/n).st_ino==os.stat(LIVE/n).st_ino and os.stat(ROOT/n).st_dev==os.stat(LIVE/n).st_dev, 'mount identity changed: '+n
backup=pathlib.Path('/oem/apps/multiace/rfid-backups')/('dual-cmd13-'+time.strftime('%Y%m%dT%H%M%S'))
backup.mkdir(parents=True,exist_ok=False)
for n,(old,new) in files.items():
 shutil.copy2(ROOT/n,backup/n); assert sha(backup/n)==old
(backup/'manifest.json').write_text(json.dumps({'files':files,'created':time.time(),'method':'preserve existing bind-mounted inodes'},indent=2)+'\n')
written=[]
def overwrite_in_place(path,source):
 with path.open('r+b') as out,source.open('rb') as inp:
  out.seek(0);shutil.copyfileobj(inp,out);out.truncate();out.flush();os.fsync(out.fileno())
try:
 for n,(old,new) in files.items():
  safe()
  # Append before touching the inode so a partial write also rolls back.
  written.append(n);overwrite_in_place(ROOT/n,STAGE/n)
  assert sha(ROOT/n)==new and sha(LIVE/n)==new, 'readback failed: '+n
except Exception:
 for n in reversed(written): overwrite_in_place(ROOT/n,backup/n)
 raise
print(json.dumps({'result':'installed','backup':str(backup),'sha256':{n:sha(ROOT/n) for n in files}}))
