import os,pathlib,tempfile,shutil,hashlib
root=pathlib.Path('/home/lava/printer_data/config');cfg=(root/'printer.cfg').resolve();inc=root/'ace-native-guard.cfg';extra=pathlib.Path('/home/lava/klipper/klippy/extras/ace_native_guard.py');proto=pathlib.Path('/home/lava/klipper/klippy/extras/ace_protocol_v2.py');src=pathlib.Path('/tmp/dual-slot4-trace-stage/ace_native_guard.py');backup=pathlib.Path('/tmp/dual-slot4-trace-backup')
h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
assert h(cfg)=='6e5686a83c178a9be3c8b66cf2da8250fda7bc835dc99e321dc15230125abb7d' and h(proto)=='749d1487e3a404c66a881822c1bcfbfb0cd0d6fcf7e6e231e52581a6d86e93ca' and h(src)=='de7d7d182097e08310b371faf20c2875ce2834a15842433c9931d942400dc5b9'
assert not extra.exists() and not inc.exists() and not backup.exists()
raw=cfg.read_bytes();mark=b'#*# <---------------------- SAVE_CONFIG ---------------------->';assert raw.count(mark)==1
backup.mkdir(mode=0o700);shutil.copy2(cfg,backup/'printer.cfg');shutil.copy2(proto,backup/'ace_protocol_v2.py');shutil.copy2(src,extra);inc.write_text('[ace_native_guard]\n');st=cfg.stat();os.chown(inc,st.st_uid,st.st_gid)
fd,tmp=tempfile.mkstemp(dir=cfg.parent,prefix='.native97-')
with os.fdopen(fd,'wb') as f:f.write(raw.replace(mark,b'[include ace-native-guard.cfg]\n\n'+mark));f.flush();os.fsync(f.fileno())
os.chmod(tmp,st.st_mode&0o7777);os.chown(tmp,st.st_uid,st.st_gid);os.replace(tmp,cfg)
print('installed; no restart')
