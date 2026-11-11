import ast, pathlib, types, unittest
import atexit, tempfile, tarfile
REPO=pathlib.Path(__file__).resolve().parents[2]
ARCHIVE=REPO/'overlays/233-dev.2-perms.1/root/usr/local/share/233-firmware/multiace-managed-1.27b-233.20261111-single.3.tar.gz'
TMP=tempfile.TemporaryDirectory(prefix='managed-feed-tests-')
atexit.register(TMP.cleanup)
HERE=pathlib.Path(TMP.name)
with tarfile.open(ARCHIVE,'r:gz') as archive:
    for name in ('filament_feed_ace.py','ace_stop_guard.py'):
        member=archive.getmember('multiace-1.27b-233.20261111-single.3/klipper/extras/'+name)
        (HERE/name).write_bytes(archive.extractfile(member).read())
SOURCE=HERE/'filament_feed_ace.py'
GUARD=HERE/'ace_stop_guard.py'

class StopUnconfirmedError(RuntimeError):pass
class CommandError(Exception):pass
class Guard:
    def __init__(self):self.fault=None
    def message(self):return self.fault
    def require_clear(self):
        if self.fault:raise StopUnconfirmedError(self.fault)
class Command:
    def __init__(self,value=1.):self.value=value
    def error(self,*args,**kwargs):
        e=CommandError(kwargs.get('message',args[0] if args else ''));e.info=kwargs;return e
    def get_int(self,key):return 0
    def get_float(self,key,default=None):return .04 if key=='TIME' else self.value

def function(path,class_name,name):
    tree=ast.parse(path.read_text())
    container=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==class_name) if class_name else tree
    node=next(n for n in container.body if isinstance(n,ast.FunctionDef) and n.name==name)
    return ast.Module(body=[node],type_ignores=[])

globals_={'StopUnconfirmedError':StopUnconfirmedError,'FEED_CHANNEL_1':0,'FEED_CHANNEL_NUMS':2,'FEED_MOTOR_DIR_A':1,'FEED_MOTOR_DIR_B':2}
exec(compile(function(GUARD,None,'guard_for_printer'),str(GUARD),'exec'),globals_)
exec(compile(function(GUARD,None,'require_printer_clear'),str(GUARD),'exec'),globals_)
exec(compile(function(SOURCE,'FilamentFeed','cmd_FEED_MOTOR_ONE_CYCLE'),str(SOURCE),'exec'),globals_)
invoke=globals_['cmd_FEED_MOTOR_ONE_CYCLE']

class GuardTests(unittest.TestCase):
    def setUp(self):
        self.guard=Guard();self.ace=types.SimpleNamespace(_stop_guard=self.guard)
        self.calls=[];self.motor=types.SimpleNamespace(max_value=1.,run_one_cycle=lambda *a:self.calls.append(a))
        self.feed=types.SimpleNamespace(printer=types.SimpleNamespace(lookup_object=lambda *a:self.ace),ace=self.ace,motor=self.motor,motor_hang_neutral_time=.04)
    def test_existing_fault_is_standard_command_error_without_motor(self):
        self.guard.fault='STOP fault'
        with self.assertRaises(CommandError) as e:invoke(self.feed,Command())
        self.assertEqual(self.calls,[]);self.assertEqual(e.exception.info['id'],525)
    def test_fault_after_entry_is_standard_command_error(self):
        def race(*args):
            self.guard.fault='new STOP fault'
            raise StopUnconfirmedError(self.guard.fault)
        self.motor.run_one_cycle=race
        with self.assertRaises(CommandError) as e:invoke(self.feed,Command())
        self.assertEqual(str(e.exception),'new STOP fault');self.assertEqual(e.exception.info['code'],210)
    def test_locked_guard_allows_zero_and_negative_cleanup(self):
        self.guard.fault='STOP fault'
        invoke(self.feed,Command(0.));invoke(self.feed,Command(-1.))
        self.assertEqual(self.calls,[(1,0.,.04),(1,-1.,.04)])
    def test_zero_motor_limit_preserves_cleanup(self):
        self.guard.fault='STOP fault';self.motor.max_value=0.
        invoke(self.feed,Command(1.))
        self.assertEqual(self.calls,[(1,1.,.04)])
    def test_clear_guard_preserves_normal_single_cycle(self):
        invoke(self.feed,Command())
        self.assertEqual(self.calls,[(1,1.,.04)])

if __name__=='__main__':unittest.main()
