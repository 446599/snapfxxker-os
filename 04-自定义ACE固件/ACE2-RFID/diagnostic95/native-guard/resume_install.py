"""Finish partial install after immutable protocol bind mount rejection."""
import hashlib,os,pathlib,tempfile
root=pathlib.Path('/home/lava/printer_data/config');cfg=(root/'printer.cfg').resolve();inc=root/'ace-native-guard.cfg'
extra=pathlib.Path('/home/lava/klipper/klippy/extras/ace_native_guard.py');proto=pathlib.Path('/home/lava/klipper/klippy/extras/ace_protocol_v2.py')
backup=pathlib.Path('/tmp/ace-native-guard-backup-20261107');src=pathlib.Path('/tmp/ace-native-guard-stage/ace_native_guard-new.py')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
assert sha(cfg)==sha(backup/'printer.cfg')=='6e5686a83c178a9be3c8b66cf2da8250fda7bc835dc99e321dc15230125abb7d'
assert sha(proto)==sha(backup/'ace_protocol_v2.py')=='749d1487e3a404c66a881822c1bcfbfb0cd0d6fcf7e6e231e52581a6d86e93ca'
assert sha(extra)=='314ec1a6c2705d37eadede742c86fd13012f7fc1237e79f29ea878c00b7228a4'
assert sha(src)=='b4fa01abf17bad9c261e76310df76afc85463378beb2b5aff79c8739566f963a'
assert inc.read_text()=='[ace_native_guard]\n'
marker=b'#*# <---------------------- SAVE_CONFIG ---------------------->';raw=cfg.read_bytes();assert raw.count(marker)==1

def replace(path,data):
 st=path.stat();fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.native-guard-')
 try:
  with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
  os.chmod(tmp,st.st_mode & 0o7777);os.chown(tmp,st.st_uid,st.st_gid);os.replace(tmp,path)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)
replace(extra,src.read_bytes());replace(cfg,raw.replace(marker,b'[include ace-native-guard.cfg]\n\n'+marker))
print('installed; no restart performed')
