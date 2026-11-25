#include "experimental_binding.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
#define SPI 0x40003800u
#define CS 0x40010c00u
#define AUX 0x40011400u
struct fake {
    uint32_t cr1,sr,cs_odr,aux_odr,epoch,sr_reads,dr_writes,dr_reads,sets,clears;
    uint32_t fail_at,stall_at,stall_value,error_mask,cancel_at,drop_assert,drop_restore;
    uint32_t phase,read_cmd,reg,dr,rx_count,rx_index,irq,reader_error,bad_cleanup;
    uint32_t irq_mask,commit_cancel,owner_valid,critical_count;
    uint32_t halt_mode,halt_irq,halt_started,poison_reg;
    uint32_t typed_mode,auth_status,auth_irq,auth_commands,read_commands;
    uint32_t nested_at,nested_mode,cancelled_dr_writes;
    uint32_t transition_at,transition_kind,transition_now,transition_dr_writes;
    uint8_t registers[64],rx[64];
} f;
static struct br_reader reader;
static unsigned assertions;
void br_test_nested(unsigned mode);
uint32_t br_test_load(uintptr_t a) {
    if(a==SPI) return f.cr1;
    if(a==SPI+8) {
        ++f.sr_reads;
        if(f.transition_at==f.sr_reads) {
            f.transition_dr_writes=f.dr_writes;
            struct ex_storage *b=ex_storage_current();assert(b);
            if(f.transition_kind==1)b->ex.offline_now=f.transition_now;
            if(f.transition_kind==2)++b->rf.generation;
            if(f.transition_kind==3)++b->rf.sequence;
        }
        if(f.nested_at==f.sr_reads) br_test_nested(f.nested_mode);
        if(f.cancel_at==f.sr_reads) {f.cancelled_dr_writes=f.dr_writes;ex_stop_request();}
        if(f.fail_at && f.sr_reads>=f.fail_at) return f.sr|f.error_mask;
        if(f.stall_at && f.sr_reads>=f.stall_at) return f.stall_value;
        return f.sr;
    }
    if(a==SPI+12) {f.sr&=~1u; ++f.dr_reads;return f.dr;}
    if(a==CS+12) return f.cs_odr;
    if(a==AUX+12) return f.aux_odr;
    assert(!"unexpected load");return 0;
}
void br_test_store(uintptr_t a,uint32_t v) {
    if(a==CS+20) {++f.clears;if(!f.drop_assert) f.cs_odr&=~v;f.phase=0;return;}
    if(a==CS+16) {++f.sets;if(!f.drop_restore) f.cs_odr|=v;return;}
    if(a==AUX+20) {f.aux_odr&=~v;return;}
    if(a==SPI+12) {
        ++f.dr_writes; f.sr|=1;
        if(!f.phase) {f.read_cmd=v&0x80;f.reg=(v>>1)&0x3f;f.dr=0;f.phase=1;return;}
        if(f.read_cmd) {
            if(f.reg==4) f.dr=f.irq;
            else if(f.reg==6) f.dr=f.reader_error;
            else if(f.reg==9) f.dr=f.rx[f.rx_index++%64];
            else f.dr=f.registers[f.reg];
            if(f.halt_mode && f.halt_started && f.poison_reg==f.reg) f.dr^=0x01;
        } else {
            if((f.halt_mode || f.typed_mode) && f.reg==4) f.irq&=~v;
            if(f.halt_mode && f.reg==1 && v==4) {f.irq=f.halt_irq;f.halt_started=1;}
            if(f.typed_mode && f.reg==1 && v==14) {f.registers[8]=f.auth_status;f.irq=f.auth_irq;++f.auth_commands;}
            if(f.typed_mode && f.reg==1 && v==12) {f.irq=0x30;++f.read_commands;}
            if(f.reg==1 && v==12) {f.registers[10]=(uint8_t)f.rx_count;f.rx_index=0;}
            else if(f.reg==10 && v&0x80) f.registers[10]=0;
            f.registers[f.reg]=(uint8_t)v;
            if(f.reg==10 && v&0x80) f.registers[10]=0;
            if(f.reg==1 && v==0 && f.bad_cleanup && f.dr_writes>30) f.registers[1]=12;
            f.dr=0;
        }
        f.phase=0;return;
    }
    assert(!"unexpected store");
}
void br_test_settle(void) {}
