"""Reviewed candidate staging; installs extra only, never restarts."""
import os,pathlib,hashlib,tempfile
p=pathlib.Path('/home/lava/klipper/klippy/extras/ace_native_guard.py');n=pathlib.Path('/tmp/ace-native-guard-stage/ace_native_guard-run4.py')
h=lambda x:hashlib.sha256(x.read_bytes()).hexdigest()
assert h(p)=='b0faed50b43fe1c03aecbf3b11a654399bfb30cec86097a532f5484e64dfdae3'
assert h(n)=='ecc1ae1400f794def8a6918ee5cd2541bcb47dc23086ce73ee401b01e157d90c'
st=p.stat();fd,tmp=tempfile.mkstemp(dir=p.parent,prefix='.guard-run4-')
try:
 with os.fdopen(fd,'wb') as f:f.write(n.read_bytes());f.flush();os.fsync(f.fileno())
 os.chmod(tmp,st.st_mode&0o7777);os.chown(tmp,st.st_uid,st.st_gid);os.replace(tmp,p)
finally:
 if os.path.exists(tmp):os.unlink(tmp)
print('installed; no restart or motion')
