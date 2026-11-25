#include "bounded_rc522.h"
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
    uint8_t registers[64],rx[64];
} f;
static struct br_reader reader;
static struct br_state state;
static struct br_transaction txn;
static unsigned assertions;
uint32_t br_test_load(uintptr_t a) {
    if(a==SPI) return f.cr1;
    if(a==SPI+8) {
        ++f.sr_reads;
        if(f.cancel_at==f.sr_reads) ++f.epoch;
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
        } else {
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
uint32_t br_platform_enter(void) {
    uint32_t old=f.irq_mask; f.irq_mask=1; ++f.critical_count;
    if(f.commit_cancel) ++f.epoch;
    return old;
}
void br_platform_leave(uint32_t key) { assert(f.irq_mask==1); f.irq_mask=key; }
int br_platform_commit_valid(const struct br_transaction *t) {
    assert(t==&txn && f.irq_mask==1); return (int)f.owner_valid;
}
static void reset(uint32_t budget) {
    memset(&f,0,sizeof(f));memset(&reader,0,sizeof(reader));memset(&state,0,sizeof(state));
    f.owner_valid=1;f.cr1=0x44;f.sr=2;f.cs_odr=0x1000;f.aux_odr=0x2000;f.rx_count=16;f.irq=0x30;
    reader.spi=SPI;reader.cs_port=CS;reader.cs_mask=0x1000;reader.aux_port=AUX;reader.aux_mask=0x2000;
    txn=(struct br_transaction){&state,&reader,budget,&f.epoch,0};
    for(unsigned i=0;i<64;i++) f.rx[i]=(uint8_t)(i^0x5a);
    f.registers[0x12]=0x42;
}
#define CHECK(x) do {assert(x);++assertions;} while(0)
static void check_quarantine(uint8_t expected) {
    uint8_t out=expected;unsigned n=f.sr_reads;
    CHECK(state.quarantined && (f.cs_odr&0x1000) && !(f.aux_odr&0x2000));
    CHECK(br_read(&txn,0x12,&out)==BR_QUARANTINED && out==expected && f.sr_reads==n);
}
static enum br_result trans(uint8_t *rx,uint32_t cap,uint32_t *bits) {
    const uint8_t tx[2]={0x30,4};return br_transceive(&txn,tx,2,rx,cap,bits);
}
int main(void) {
    uint8_t out; uint8_t rx[66]; uint32_t bits; enum br_result r;
    reset(6);out=0xa5;CHECK(br_read(&txn,0x12,&out)==BR_OK && out==0x42 && !state.quarantined);
    CHECK(f.sr_reads==6 && txn.polls_left==0 && f.dr_writes==2 && f.dr_reads==2);
    reset(6);CHECK(br_write(&txn,0x12,0xa6)==BR_OK && f.registers[0x12]==0xa6);
    /* Each of six polls: initial idle, address TX/RX, data TX/RX, final idle. */
    const uint32_t stalled[6]={0x82,0,2,0,2,0x82};
    for(unsigned p=1;p<=6;p++) {
        reset(12);out=0xa5;f.stall_at=p;f.stall_value=stalled[p-1];
        CHECK(br_read(&txn,0x12,&out)==BR_TIMEOUT && out==0xa5 && f.sr_reads==12);
        check_quarantine(0xa5);
    }
    for(unsigned p=1;p<=6;p++) for(unsigned e=0x10;e<=0x40;e<<=1) {
        reset(20);out=0xa5;f.fail_at=p;f.error_mask=e;
        CHECK(br_read(&txn,0x12,&out)==BR_TRANSPORT && out==0xa5 && f.sr_reads==p);
        check_quarantine(0xa5);
    }
    for(unsigned p=1;p<=6;p++) {
        reset(20);out=0xa5;f.cancel_at=p;
        CHECK(br_read(&txn,0x12,&out)==BR_CANCELLED && out==0xa5);
        check_quarantine(0xa5);
    }
    reset(0);out=0xa5;CHECK(br_read(&txn,0x12,&out)==BR_TIMEOUT && !f.sr_reads);check_quarantine(0xa5);
    reset(10);f.sr|=1;out=0xa5;CHECK(br_read(&txn,0x12,&out)==BR_TRANSPORT && !f.dr_writes);check_quarantine(0xa5);
    reset(10);f.cr1=0;out=0xa5;CHECK(br_read(&txn,0x12,&out)==BR_TRANSPORT && !f.dr_writes);check_quarantine(0xa5);
    reset(10);f.drop_assert=1;out=0xa5;CHECK(br_read(&txn,0x12,&out)==BR_TRANSPORT && !f.dr_writes);check_quarantine(0xa5);
    reset(10);f.drop_restore=1;out=0xa5;CHECK(br_read(&txn,0x12,&out)==BR_TRANSPORT && state.quarantined && out==0xa5);
    reset(10);out=0xa5;CHECK(br_read(&txn,64,&out)==BR_INVALID && !f.sr_reads && !state.quarantined);
    reset(10);f.epoch=1;out=0xa5;CHECK(br_read(&txn,0x12,&out)==BR_CANCELLED && !f.sr_reads && state.quarantined);
    /* Shared state prevents the other reader from reusing the shared SPI. */
    reset(10);state.quarantined=1;struct br_reader other=reader;txn.reader=&other;
    CHECK(br_write(&txn,0x12,3)==BR_QUARANTINED && !f.dr_writes);
    reset(1000);memset(rx,0xa5,sizeof(rx));bits=0xdeadbeef;
    CHECK(trans(rx+1,64,&bits)==BR_OK && bits==128 && !memcmp(rx+1,f.rx,16));
    CHECK(rx[0]==0xa5 && rx[17]==0xa5 && !state.quarantined);
    unsigned total=f.sr_reads;
    /* Every individual status read of a full success path, including cleanup. */
    for(unsigned p=1;p<=total;p++) {
        reset(1000);memset(rx,0xa5,sizeof(rx));bits=0xdeadbeef;f.fail_at=p;f.error_mask=0x40;
        CHECK(trans(rx+1,64,&bits)==BR_TRANSPORT && bits==0xdeadbeef);
        for(unsigned i=0;i<66;i++) CHECK(rx[i]==0xa5);
        CHECK(state.quarantined && f.sr_reads<=p);
    }
    /* STOP at every successful-path status read, including final cleanup. */
    for(unsigned p=1;p<=total;p++) {
        reset(1000);memset(rx,0xa5,sizeof(rx));bits=0xdeadbeef;f.cancel_at=p;
        CHECK(trans(rx+1,64,&bits)==BR_CANCELLED && bits==0xdeadbeef);
        for(unsigned i=0;i<66;i++) CHECK(rx[i]==0xa5);
        CHECK(state.quarantined && f.sr_reads<=p);
    }
    for(unsigned b=0;b<total;b++) {
        reset(b);memset(rx,0xa5,sizeof(rx));bits=0xdeadbeef;
        CHECK(trans(rx+1,64,&bits)==BR_TIMEOUT && bits==0xdeadbeef && f.sr_reads==b);
        CHECK(state.quarantined && rx[1]==0xa5);
    }
    reset(1000);f.irq=0;bits=0xdeadbeef;memset(rx,0xa5,sizeof(rx));
    CHECK(trans(rx+1,64,&bits)==BR_TIMEOUT && f.sr_reads==1000 && bits==0xdeadbeef && state.quarantined);
    reset(1000);f.irq=1;bits=0xdeadbeef;
    CHECK(trans(rx+1,64,&bits)==BR_NO_REPLY && bits==0xdeadbeef && !state.quarantined);
    for(unsigned e=1;e<=0x80;e<<=1) {
        reset(1000);f.reader_error=e;bits=0xdeadbeef;
        CHECK(trans(rx+1,64,&bits)==BR_READER_ERROR && bits==0xdeadbeef);
    }
    for(unsigned n=0;n<=255;n++) {
        reset(3000);f.rx_count=n;bits=0xdeadbeef;memset(rx,0xa5,sizeof(rx));r=trans(rx+1,16,&bits);
        if(!n || n>16) CHECK(r==BR_CAPACITY && bits==0xdeadbeef && rx[1]==0xa5);
        else CHECK(r==BR_OK && bits==8*n && rx[0]==0xa5 && rx[n+1]==0xa5);
    }
    for(unsigned last=0;last<8;last++) {
        reset(1000);f.registers[12]=last;bits=0xdeadbeef;
        CHECK(trans(rx+1,64,&bits)==BR_OK && bits==(last?120+last:128));
    }
    reset(1000);f.bad_cleanup=1;bits=0xdeadbeef;memset(rx,0xa5,sizeof(rx));
    CHECK(trans(rx+1,64,&bits)==BR_TRANSPORT && bits==0xdeadbeef && state.quarantined && rx[1]==0xa5);
    reset(1000);f.commit_cancel=1;bits=0xdeadbeef;memset(rx,0xa5,sizeof(rx));
    CHECK(trans(rx+1,64,&bits)==BR_CANCELLED && bits==0xdeadbeef && state.quarantined);
    CHECK(rx[1]==0xa5 && f.irq_mask==0 && f.critical_count==1);
    reset(1000);f.owner_valid=0;bits=0xdeadbeef;memset(rx,0xa5,sizeof(rx));
    CHECK(trans(rx+1,64,&bits)==BR_CANCELLED && bits==0xdeadbeef && state.quarantined && rx[1]==0xa5);
    reset(1000);f.irq_mask=1;CHECK(trans(rx+1,64,&bits)==BR_OK && f.irq_mask==1);
    const uint8_t exact_tx[2]={0x30,4};
    for(unsigned n=1;n<=64;n++) for(unsigned last=0;last<8;last++) {
        reset(3000);f.rx_count=n;f.registers[12]=last;bits=0xdeadbeef;memset(rx,0xa5,sizeof(rx));
        r=br_transceive_exact(&txn,exact_tx,2,rx+1,64,&bits,128);
        if(n==16 && last==0) CHECK(r==BR_OK && bits==128);
        else CHECK(r==BR_PROTOCOL && bits==0xdeadbeef && rx[1]==0xa5);
    }
    const uint8_t tx[1]={0};
    reset(1000);CHECK(br_transceive(&txn,tx,0,rx,64,&bits)==BR_INVALID && !f.dr_writes);
    reset(1000);CHECK(br_transceive(&txn,tx,65,rx,64,&bits)==BR_INVALID && !f.dr_writes);
    printf("PASS %u assertions; full transfer %u status polls; exhaustive SPI-fault prefix and budget cutoffs\n",assertions,total);
    return 0;
}
