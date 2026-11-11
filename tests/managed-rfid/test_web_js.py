import pathlib,shutil,subprocess,tarfile,tempfile,unittest
REPO=pathlib.Path(__file__).resolve().parents[2]
ARCHIVE=REPO/'overlays/233-dev.2-perms.1/root/usr/local/share/233-firmware/multiace-managed-1.27b-233.20261111-single.3.tar.gz'
class WebTests(unittest.TestCase):
 @unittest.skipUnless(shutil.which('node'),'node is required for web behavior tests')
 def test_scripts_against_packaged_app(self):
  with tempfile.TemporaryDirectory() as tmp,tarfile.open(ARCHIVE) as archive:
   member=next(x for x in archive if x.name.endswith('/web/frontend/app.js'))
   app=pathlib.Path(tmp)/'app.js';app.write_bytes(archive.extractfile(member).read())
   member=next(x for x in archive if x.name.endswith('/web/frontend/index.html'));html=pathlib.Path(tmp)/'index.html';html.write_bytes(archive.extractfile(member).read())
   for script in sorted((pathlib.Path(__file__).parent/'js').glob('*.js')):
    with self.subTest(script=script.name):subprocess.run(['node',str(script),str(app),str(html)],check=True,timeout=15,capture_output=True,text=True)
