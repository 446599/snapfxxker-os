import pathlib,hashlib,os,shutil,time,json
src=pathlib.Path('/oem/apps/multiace/multiace-1.27b/klipper/extras/ace.py');live=pathlib.Path('/home/lava/klipper/klippy/extras/ace.py');candidate=pathlib.Path('/tmp/dual-boot-rescan-stage/ace.py');old='7e247bd1063cda7fa31b48e99ee0fd026cfbc6fa22aeaf736d387de180d48f02';new='ef04d435bb9536cf4017e4a15a3926cc687d624a3d0986ebc91acfc79495c30e'
h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
# S60 stop unmounts modules; live path is remounted by S60 start.
assert h(src)==old and h(candidate)==new
backup=pathlib.Path('/oem/apps/multiace/rfid-backups')/('dual-boot-rescan-'+time.strftime('%Y%m%dT%H%M%S'));backup.mkdir();shutil.copy2(src,backup/'ace.py');assert h(backup/'ace.py')==old
(backup/'manifest.json').write_text(json.dumps({'old':old,'new':new,'method':'in-place while service stopped'},indent=2))
def overwrite(source):
 with src.open('r+b') as out,source.open('rb') as inp:out.seek(0);shutil.copyfileobj(inp,out);out.truncate();out.flush();os.fsync(out.fileno())
try:
 overwrite(candidate);assert h(src)==new
except Exception:
 overwrite(backup/'ace.py');raise
print(json.dumps({'installed':new,'backup':str(backup)}))
