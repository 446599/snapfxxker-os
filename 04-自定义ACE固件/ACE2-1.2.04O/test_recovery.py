"""Thumb execution of the pinned image and native status/feed hook paths."""
import sys, unittest
from pathlib import Path
import test_emulator as em
from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_SP,UC_ARM_REG_R8

em.BUILD=Path(sys.argv.pop(1)).resolve()

class Recovery(unittest.TestCase):
    def setUp(self):
        self.f=em.Firmware(); self.f.command(1)
        c=0x200012e8
        for n in range(2):
            q=c+n*208+60; head=q+8
            self.f.write32(q,0);self.f.write32(q+4,head)
            self.f.write32(head+4,head);self.f.write32(head+8,head)
    def set32(self,a,v): self.f.write32(a,v)
    def status(self,slot,v): self.set32(0x20001004+slot*4,v)
    def getstatus(self,slot):return self.f.read32(0x20001004+slot*4)
    def tick(self,t): self.set32(0x2000820c,t);return self.f.command()
    def removal(self,slot=0):
        self.status(slot,129); self.f.edge(slot,0,1); self.f.edge(slot,1,0)
    def native_status(self,slot,value):
        f=self.f;f.symbols['native_status']=0x0800ae52;f.stop_at=0x0800aee2
        f.call('native_status',0x20001000+slot*4,value);f.stop_at=None
    def native_feed_entry(self,slot):
        f=self.f;f.symbols['ip_stock_feed']=0x0803e000;f.symbols['native_feed']=0x0800b578;f.stop_at=0x0800b57c
        f.write32(0x20009200,slot);f.call('native_feed',0,0x20009200,0x20009300);f.stop_at=None
    def polls(self,start=0):
        for t in (start,start+1000,start+2001):self.tick(t)
    def test_all_slots_clear_only_own_129_after_empty_and_quiet(self):
        for slot in range(4):
            with self.subTest(slot=slot):
                self.setUp();self.removal(slot)
                for i in range(4):
                    if i!=slot:self.status(i,130+i)
                self.polls();self.assertEqual(self.getstatus(slot),0)
                for i in range(4):
                    if i!=slot:self.assertEqual(self.getstatus(i),130+i)
                self.assertEqual(self.f.moves,[]);self.assertEqual(self.f.stops,[])
    def test_no_removal_no_clear_and_other_errors_untouched(self):
        self.status(0,129);self.polls();self.assertEqual(self.getstatus(0),129)
        for value in (1,2,128,130,131,132,135):
            self.setUp();self.status(0,value);self.f.edge(0,0,1);self.f.edge(0,1,0);self.polls();self.assertEqual(self.getstatus(0),value)
    def test_same_129_native_store_invalidates(self):
        worker = 0x200012e8 + 20
        self.set32(worker, 1)
        self.removal()
        self.native_status(0, 129)
        self.set32(worker, 0)
        self.polls()
        self.assertEqual(self.getstatus(0), 129)
    def test_late_new_129_preserved(self):
        self.removal();self.polls();self.assertEqual(self.getstatus(0),0)
        self.native_status(0,129);self.polls(3000);self.assertEqual(self.getstatus(0),129)
    def test_new_feed_invalidates(self):
        worker = 0x200012e8 + 20
        self.set32(worker, 1)
        self.removal()
        self.native_feed_entry(0)
        self.set32(worker, 0)
        self.polls()
        self.assertEqual(self.getstatus(0), 129)
    def test_other_slot_new_feed_does_not_invalidate(self):
        self.removal();self.native_feed_entry(1);self.polls();self.assertEqual(self.getstatus(0),0)
    def test_reinsert_and_epoch_revoke(self):
        worker = 0x200012e8 + 20
        self.set32(worker, 1)
        self.removal()
        self.f.edge(0, 0, 1)
        self.set32(worker, 0)
        self.polls()
        self.assertEqual(self.getstatus(0), 129)
    def test_busy_motor_worker_and_queued_delay(self):
        for addr in (0x20001014+6,0x200012e8+20,0x200012e8+32,0x200012e8+60,0x200012e8+208+60):
            with self.subTest(addr=hex(addr)):
                self.setUp();self.set32(addr,1);self.removal();self.polls();self.assertEqual(self.getstatus(0),129);self.set32(addr,0);self.polls(3000);self.assertEqual(self.getstatus(0),0)
    def test_invalid_queue_link_fails_closed(self):
        self.set32(0x200012e8+60+8+4,0);self.removal();self.polls();self.assertEqual(self.getstatus(0),129)
    def test_debounced_removal_clears_immediately_without_query(self):
        self.removal()
        self.assertEqual(self.getstatus(0), 0)
        self.assertEqual(self.f.moves, [])

    def test_busy_at_removal_then_ready_on_reinsert(self):
        worker = 0x200012e8 + 20
        self.set32(worker, 1)
        self.removal(3)
        self.assertEqual(self.getstatus(3), 129)
        self.set32(worker, 0)
        self.f.edge(3, 0, 1)
        self.assertEqual(self.getstatus(3), 0)
        self.assertEqual(self.f.command()['pending'], [0, 0, 0, 1])
        self.assertEqual(self.f.moves, [])

    def test_three_second_reinsert_can_arm_one_short_grip(self):
        self.removal(3)
        self.tick(500)
        self.assertEqual(self.getstatus(3), 0)
        self.assertEqual(self.f.moves, [])
        self.f.edge(3, 0, 1)
        reply = self.tick(3000)
        self.assertEqual(reply['pending'], [0, 0, 0, 1])
        self.assertEqual(self.f.moves, [])
        self.assertEqual(self.f.command(3, slot=3, epoch=reply['epochs'][3])['code'], 0)
        self.assertEqual(self.f.moves, [])

    def test_callback_without_native_empty_inlet_keeps_fault(self):
        self.f.edge(3, 0, 1)
        self.status(3, 129)
        self.f.call('ip_insert', 0, 12, 1, 0)
        self.assertEqual(self.f.command()['pending'], [0, 0, 0, 0])
        self.assertEqual(self.getstatus(3), 129)

    def test_real_129_instruction_branch_cancels_pending(self):
        worker = 0x200012e8 + 20
        self.set32(worker, 1)
        self.removal(); self.f.symbols['native_129']=0x0800a28e;self.f.stop_at=0x0800aee2
        self.f.uc.reg_write(UC_ARM_REG_R8,0);self.f.call('native_129',0x20001000)
        self.f.stop_at = None
        self.set32(worker, 0)
        self.polls()
        self.assertEqual(self.getstatus(0), 129)
    def test_raw_129_tail_executes_original_status_store(self):
        self.status(2,130);self.native_status(2,129);self.assertEqual(self.getstatus(2),129)

if __name__=='__main__':unittest.main(verbosity=2)
