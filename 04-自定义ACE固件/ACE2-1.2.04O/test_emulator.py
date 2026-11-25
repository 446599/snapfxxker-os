"""Execute compiled Thumb code with native boundaries stubbed; never opens hardware."""
import argparse, json, struct, unittest
from pathlib import Path
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
from unicorn.arm_const import *

BUILD = None
class Firmware:
    def __init__(self):
        self.uc=Uc(UC_ARCH_ARM,UC_MODE_THUMB|UC_MODE_MCLASS)
        self.uc.mem_map(0x08000000,0x40000);self.uc.mem_map(0x20000000,0x10000)
        self.uc.mem_write(0x08008000,Path(json.loads((BUILD/'manifest.json').read_text())['image']).read_bytes())
        self.symbols=json.loads((BUILD/'symbols.json').read_text())
        self.moves=[];self.stops=[];self.registrations=[];self.stop_at=None
        self.uc.hook_add(UC_HOOK_CODE,self.hook)
        self.parent=self.call('ip_factory')
        self.write32(0x20001e64,self.parent)
    def write32(self,a,v):self.uc.mem_write(a,struct.pack('<I',v))
    def read32(self,a):return struct.unpack('<I',self.uc.mem_read(a,4))[0]
    def hook(self,u,a,size,data):
        if a==0x0803f000 or a==self.stop_at:u.emu_stop();return
        if a==self.symbols['ip_stock_malloc']&~1:
            size=u.reg_read(UC_ARM_REG_R0);alloc=(size+15)&~7
            self.write32(0x20004000,0);self.write32(0x20004004,alloc|0x80000000)
            u.reg_write(UC_ARM_REG_R0,0x20004008)
        elif a==self.symbols['ip_stock_feed']&~1:
            ptr=u.reg_read(UC_ARM_REG_R1);args=struct.unpack('<4I',u.mem_read(ptr,16))
            self.moves.append(args);self.write32(u.reg_read(UC_ARM_REG_R2),0)
            u.reg_write(UC_ARM_REG_R0,1)
        elif a==self.symbols['ip_stock_register']&~1:
            self.registrations.append((u.reg_read(UC_ARM_REG_R0),u.reg_read(UC_ARM_REG_R1)))
        elif a==self.symbols['ip_stock_power_stop']&~1:
            self.stops.append(u.reg_read(UC_ARM_REG_R0));u.mem_write(u.reg_read(UC_ARM_REG_R0)+6,b'\0')
        else:return
        u.reg_write(UC_ARM_REG_PC,u.reg_read(UC_ARM_REG_LR))
    def call(self,name,*args):
        for reg in (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3):self.uc.reg_write(reg,0)
        self.uc.reg_write(UC_ARM_REG_SP,0x2000f000);self.uc.reg_write(UC_ARM_REG_LR,0x0803f001)
        for i,arg in enumerate(args):
            if i<4:self.uc.reg_write([UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3][i],arg)
            else:self.write32(0x2000f000+(i-4)*4,arg)
        self.uc.emu_start(self.symbols[name]|1,0x0803f000,count=200000)
        return self.uc.reg_read(UC_ARM_REG_R0)
    def command(self,op=0,slot=0,enabled=0,session=17,epoch=0,expected=0,revision=0):
        data=struct.pack('<4sBBBBIIII',b'AIP1',1,op,slot,enabled,session,epoch,expected,revision)
        self.uc.mem_write(0x20009000,data);self.write32(0x20009180,0)
        result=self.call('ip_dispatch',0,0x20009000,24,0x20009100,0x20009180)
        assert result==1 and self.read32(0x20009180)&65535==48
        b=bytes(self.uc.mem_read(0x20009100,48))
        return dict(code=b[5],configured=b[6],enabled=b[7],session=self.read32(0x20009108),
                    epochs=struct.unpack('<4I',b[16:32]),pending=list(b[32:36]),results=list(b[36:40]),
                    revision=self.read32(0x20009128))
    def edge(self,slot,before,after):
        self.uc.mem_write(0x20001a15+slot*16,bytes([after]))
        return self.call('ip_insert',0,slot*4,before,after)
    def arm(self,slot=0):
        self.command(1);self.edge(slot,0,1);return self.command()['epochs'][slot]

class Cases(unittest.TestCase):
    def setUp(self):self.f=Firmware()
    def test_native_caller_reaches_gpio_and_pwm_all_slots(self):
        from unicorn import UC_HOOK_MEM_WRITE
        for slot in range(4):
            with self.subTest(slot=slot):
                f = Firmware()
                f.uc.mem_map(0x40000000, 0x30000)
                f.uc.mem_map(0xe0000000, 0x100000)
                f.write32(0xe000ed88, 0xf00000)
                f.uc.reg_write(UC_ARM_REG_FPEXC, 0x40000000)
                motor = 0x20001014 + slot * 64
                f.uc.mem_write(motor, struct.pack('<f', 1.0))
                f.uc.mem_write(motor + 4, bytes([slot]))
                f.write32(motor + 56, 0x20009500)
                f.write32(0x20009500, 0x20009600)
                f.write32(0x20009610, 0x0803e001)
                f.command(1, enabled=1)
                f.symbols['_native_caller'] = 0x0800a240
                f.stop_at = f.symbols['ip_stock_start_return']
                f.uc.reg_write(UC_ARM_REG_R8, slot)
                hooks, writes = [], []
                expected_hooks = [f.symbols[name] for name in
                    ('ip_active_hook', 'ip_gpio_hook', 'ip_start_pwm_hook')]
                def boundary(u, address, size, data):
                    if address in (0x08016098, 0x08016164, 0x0803e000):
                        u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))
                    elif address in expected_hooks: hooks.append(address)
                def write(u, access, address, size, value, data):
                    if 0x40000000 <= address < 0x40030000: writes.append((address, value))
                f.uc.hook_add(UC_HOOK_CODE, boundary)
                f.uc.hook_add(UC_HOOK_MEM_WRITE, write)
                f.call('_native_caller', 0, 0, 50, 400)
                self.assertEqual(f.uc.reg_read(UC_ARM_REG_R4), motor)
                self.assertEqual(hooks, expected_hooks)
                self.assertEqual(len(writes), 5)
                self.assertEqual(writes[-1], (0x40000034 + slot * 4, 454))
                self.assertEqual(f.stops, [])

    def test_boot_skips_insert_but_preserves_removal(self):
        self.assertEqual(self.f.edge(0,0,1),1);self.assertEqual(self.f.edge(0,1,0),0)
        self.assertEqual(self.f.command()['pending'],[0]*4);self.assertEqual(self.f.moves,[])
    def test_real_native_motor_objects_allowed_in_enabled_mode(self):
        self.f.command(1, enabled=1)
        # Pinned 98O caller 0x0800A240 constructs r0=0x20001014+(slot<<6)
        # before BL 0x0800ECE8. This is independent of the policy constant.
        for slot in range(4):
            self.assertEqual(self.f.call('ip_motor_allowed', 0x20001014+slot*64), 1)
        self.assertEqual(self.f.stops, [])

    def test_enabled_uses_original_callback(self):
        self.assertEqual(self.f.command(1,enabled=1)['code'],0)
        self.assertEqual(self.f.edge(2,0,1),0);self.assertEqual(self.f.moves,[])
    def test_short_feed_exact_and_duplicate_is_no_move(self):
        e=self.f.arm(2);r=self.f.command(2,slot=2,epoch=e)
        self.assertEqual((r['code'],r['results'][2]),(0,1));self.assertEqual(self.f.moves,[(2,50,400,0)])
        self.f.command(2,slot=2,epoch=e);self.assertEqual(len(self.f.moves),1)
    def test_stale_epoch_or_session_no_motion(self):
        e=self.f.arm();self.assertEqual(self.f.command(2,epoch=e,session=18)['code'],4)
        self.f.edge(0,1,0);self.f.edge(0,0,1)
        self.assertEqual(self.f.command(2,epoch=e)['code'],4);self.assertEqual(self.f.moves,[])
    def test_pending_expires(self):
        e=self.f.arm();self.f.write32(0x2000820c,10001)
        self.assertEqual(self.f.command(2,epoch=e)['code'],5);self.assertEqual(self.f.moves,[])
    def test_mode_CAS_rejects_prior_set(self):
        a=self.f.command(1);b=self.f.command(1,enabled=1,expected=17,revision=a['revision'])
        self.assertEqual(b['enabled'],1)
        replay=self.f.command(1,enabled=0,expected=0,revision=0)
        self.assertEqual(replay['code'],2);self.assertEqual(replay['enabled'],1)
    def test_mode_switch_never_replays_insertion(self):
        self.f.arm();r=self.f.command(1,enabled=1,expected=17,revision=1)
        self.assertEqual(r['pending'],[0]*4);self.assertEqual(self.f.moves,[])
    def test_falling_edge_before_native_motor_write_stops_stale_move(self):
        e=self.f.arm();self.f.command(2,epoch=e);self.f.edge(0,1,0)
        self.assertEqual(self.f.call('ip_motor_allowed',0x20001014),0);self.assertEqual(len(self.f.stops),1)
        self.assertEqual(self.f.command()['results'][0],6)
    def test_active_deadline_stops_without_host(self):
        e=self.f.arm();self.f.command(2,epoch=e);self.f.write32(0x2000820c,20001)
        self.assertEqual(self.f.call('ip_motor_allowed',0x20001014),0)
    def test_manual_native_motion_unaffected_after_release(self):
        e=self.f.arm();self.f.command(2,epoch=e);self.f.command(3,epoch=e)
        self.f.edge(0,1,0)
        self.assertEqual(self.f.call('ip_motor_allowed',0x20001014),1)
    def test_busy_set_and_discard_cannot_release_live_move(self):
        e=self.f.arm();self.f.command(2,epoch=e);self.f.write32(0x20001004,1)
        self.assertEqual(self.f.command(3,epoch=e)['code'],3)
        self.assertEqual(self.f.command(1,enabled=1,expected=17,revision=1)['code'],3)
    def test_other_slots_preserved(self):
        e=self.f.arm();self.f.command(2,epoch=e);self.f.edge(0,1,0)
        self.assertEqual(self.f.call('ip_motor_allowed',0x20001054),1);self.assertEqual(self.f.stops,[])
    def test_actual_gpio_hook_never_writes_after_epoch_changed(self):
        e=self.f.arm();self.f.command(2,epoch=e);self.f.edge(0,1,0)
        self.f.uc.reg_write(UC_ARM_REG_R4,0x20001014);self.f.stop_at=self.f.symbols['ip_stock_start_return']
        self.f.write32(0x20009210,0x87654321)
        self.f.call('ip_gpio_hook',0,0x12345678,0x20009200)
        self.assertEqual(self.f.read32(0x20009210),0x87654321)
    def test_actual_gpio_hook_preserves_native_store_and_registers(self):
        self.f.uc.reg_write(UC_ARM_REG_R4,0x20001014);self.f.stop_at=self.f.symbols['ip_stock_gpio_resume']
        self.f.call('ip_gpio_hook',7,0x12345678,0x20009200)
        self.assertEqual(self.f.read32(0x20009210),0x12345678)
        self.assertEqual(self.f.uc.reg_read(UC_ARM_REG_R0),7)
        self.assertEqual(self.f.uc.reg_read(UC_ARM_REG_SP),0x2000f000)

if __name__=='__main__':
    import sys
    BUILD=Path(sys.argv.pop(1));unittest.main(verbosity=2)
