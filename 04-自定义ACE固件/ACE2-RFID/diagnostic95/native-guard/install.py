"""Exact baseline backup and install; does not restart Klipper or move."""
import hashlib,os,pathlib,shutil,tempfile
root=pathlib.Path('/home/lava/printer_data/config')
cfg=(root/'printer.cfg').resolve();proto=pathlib.Path('/home/lava/klipper/klippy/extras/ace_protocol_v2.py').resolve()
extra=pathlib.Path('/home/lava/klipper/klippy/extras/ace_native_guard.py');inc=root/'ace-native-guard.cfg';backup=pathlib.Path('/tmp/ace-native-guard-backup-20261107')
source=pathlib.Path('/tmp/ace-native-guard-stage/ace_native_guard.py');newproto=pathlib.Path('/tmp/ace-native-guard-stage/ace_protocol_v2.py')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def replace_preserving(target,data):
 st=target.stat();fd,tmp=tempfile.mkstemp(dir=target.parent,prefix='.native-guard-')
 try:
  with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
  os.chmod(tmp,st.st_mode & 0o7777);os.chown(tmp,st.st_uid,st.st_gid);os.replace(tmp,target)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)
assert sha(source)=='5f7d82b71b7071203cfbf8eb58e213e8705110f655b9971f43e8b8764e50dca7'
assert sha(proto)=='749d1487e3a404c66a881822c1bcfbfb0cd0d6fcf7e6e231e52581a6d86e93ca'
assert sha(newproto)=='f3502eabfbb04e42ca5847462e87eb9de3563cd02b13621521594c6a0081eb65'
assert sha(cfg)=='6e5686a83c178a9be3c8b66cf2da8250fda7bc835dc99e321dc15230125abb7d'
assert not extra.exists() and not inc.exists() and not backup.exists()
raw=cfg.read_bytes();marker=b'#*# <---------------------- SAVE_CONFIG ---------------------->'
assert raw.count(marker)==1
updated=raw.replace(marker,b'[include ace-native-guard.cfg]\n\n'+marker)
backup.mkdir(mode=0o700);shutil.copy2(cfg,backup/'printer.cfg');shutil.copy2(proto,backup/'ace_protocol_v2.py')
shutil.copy2(source,extra);inc.write_text('[ace_native_guard]\n')
config_stat=cfg.stat();os.chown(inc,config_stat.st_uid,config_stat.st_gid)
replace_preserving(proto,newproto.read_bytes());replace_preserving(cfg,updated)
print('installed; Klipper not restarted; no movement; backup',backup)
