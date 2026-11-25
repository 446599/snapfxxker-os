#include "bounded_rc522.h"
/* This is the register-level MFRC522 HLTA sequence, replacing E108's exact
 * command-4 request. No claim is made about a clone's electrical behavior or
 * actual tag state. The image still refuses RF_QUIESCENT and cannot boot. */
static const uint8_t restored_regs[]={0x02,0x12,0x13,0x0d,0x2a,0x2b,0x2c,0x2d,0x14};
static enum br_result fail(struct br_transaction *t,enum br_result e) {
    if(t && t->state) {
        if(!t->state->quarantined) t->state->cause=(uint32_t)e;
        t->state->quarantined=1;
    }
    return e;
}
static enum br_result restore_registers(struct br_transaction *t,const uint8_t *saved) {
    uint8_t v;enum br_result e;
#define W(r,v) do {e=br_write(t,(r),(v));if(e!=BR_OK)return e;} while(0)
#define R(r,v) do {e=br_read(t,(r),&(v));if(e!=BR_OK)return e;} while(0)
    W(0x01,0);W(0x0d,saved[3]&0x7f);
    R(0x08,v);W(0x08,v&0xf7); /* Crypto1 is always off after HALT. */
    W(0x0a,0x80);W(0x04,0x7f);
    for(unsigned i=0;i<sizeof(restored_regs);i++) {
        if(i!=3) W(restored_regs[i],saved[i]);
    }
    R(0x01,v);if(v&0x0f) return fail(t,BR_TRANSPORT);
    R(0x08,v);if(v&0x08) return fail(t,BR_TRANSPORT);
    R(0x0a,v);if(v&0x7f) return fail(t,BR_TRANSPORT);
    R(0x04,v);if(v&0x7f) return fail(t,BR_TRANSPORT);
    for(unsigned i=0;i<sizeof(restored_regs);i++) {
        R(restored_regs[i],v);
        uint8_t want=i==3 ? saved[i]&0x7f : saved[i];
        if(v!=want) return fail(t,BR_TRANSPORT);
    }
    return BR_OK;
#undef W
#undef R
}
enum br_result br_halt_checked(struct br_transaction *t) {
    uint8_t saved[sizeof(restored_regs)],v,irq;
    enum br_result e,result=BR_OK;
    if(!t || !t->state || !t->reader) return BR_INVALID;
#define W(r,v) do {e=br_write(t,(r),(v));if(e!=BR_OK)return fail(t,e);} while(0)
#define R(r,v) do {e=br_read(t,(r),&(v));if(e!=BR_OK)return fail(t,e);} while(0)
    for(unsigned i=0;i<sizeof(restored_regs);i++) R(restored_regs[i],saved[i]);
    W(0x01,0);W(0x0d,0);W(0x12,saved[1]|0x80);W(0x13,saved[2]|0x80);
    R(0x08,v);W(0x08,v&0xf7);
    /* Exact stock timer(1) arithmetic: 13560*1-1, prescaler=0. This is a
     * configuration match, not a measured wall-clock deadline on a board. */
    W(0x2a,0x80);W(0x2b,0);W(0x2c,0x34);W(0x2d,0xf7);
    W(0x02,0x80);W(0x04,0x7f);W(0x0a,0x80);
    W(0x09,0x50);W(0x09,0x00);W(0x01,0x04);
    /* Transmit does not use Transceive's StartSend. Do not fabricate success
     * from silence, TimerIRQ alone, an IdleIRQ alone or a frozen host tick. */
    do {R(0x04,irq);} while(!(irq&0x73));
    R(0x06,v);
    if(v || (irq&0x02)) result=BR_READER_ERROR;
    else if(irq&0x20) result=BR_PROTOCOL; /* Unexpected receive indication. */
    else if(!(irq&0x40)) result=BR_NO_REPLY; /* No checked TxIRQ completion. */
    e=restore_registers(t,saved);
    if(e!=BR_OK) return fail(t,e);
    uint32_t key=br_platform_enter();
    int live=br_platform_commit_valid(t) &&
        (!t->stop_epoch || *t->stop_epoch==t->expected_epoch);
    br_platform_leave(key);
    if(!live) return fail(t,BR_CANCELLED);
    return result==BR_OK ? BR_OK : fail(t,result);
#undef W
#undef R
}
