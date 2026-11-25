#include "typed_results.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static unsigned checks,calls,fail_at,published,auth_active,cycle,io_under_lock;
static unsigned lock_depth,lock_enters,lock_leaves,valid_calls,invalidate_at;
static enum br_result injected,transceive_result,publish_result;
static uint8_t initial_s2,cleared_s2,initial_irq,read_s2,tx_mode,rx_mode,read_error;
static uint8_t states[4][3];
static uint32_t result_bits;
static int read_mode;
static uint32_t stop_epoch;
static struct br_state state;
static struct br_reader reader;
static struct br_transaction transaction;
static struct ar_selection selection;
static const uint8_t key[6]={1,2,3,4,5,6};
#define CHECK(x) do {assert(x);++checks;} while(0)
static void reset(void) {
    calls=fail_at=published=auth_active=cycle=io_under_lock=0;
    lock_depth=lock_enters=lock_leaves=valid_calls=invalidate_at=0;
    injected=BR_TRANSPORT;transceive_result=publish_result=BR_OK;
    initial_s2=cleared_s2=initial_irq=tx_mode=rx_mode=read_error=0;
    read_s2=8;read_mode=0;result_bits=128;stop_epoch=1;
    memset(&state,0,sizeof(state));memset(&reader,0,sizeof(reader));
    transaction=(struct br_transaction){&state,&reader,100000,&stop_epoch,1};
    selection.raw_result=0;
    for(unsigned i=0;i<5;++i) selection.expected_uid[i]=selection.selected_uid[i]=(uint8_t)(i+1);
    selection.expected_uid[4]=selection.selected_uid[4]=4;
    for(unsigned i=0;i<4;++i) {states[i][0]=8;states[i][1]=0x10;states[i][2]=0;}
}
uint32_t br_platform_enter(void) {unsigned old=lock_depth;++lock_depth;++lock_enters;return old;}
void br_platform_leave(uint32_t old) {CHECK(lock_depth==old+1);lock_depth=old;++lock_leaves;}
int br_platform_commit_valid(const struct br_transaction *t) {
    CHECK(lock_depth>0 && t==&transaction);
    ++valid_calls;return !invalidate_at || valid_calls<invalidate_at;
}
static enum br_result io(void) {
    if(lock_depth) ++io_under_lock;
    ++calls;return fail_at && calls==fail_at ? injected : BR_OK;
}
enum br_result br_read(struct br_transaction *t,uint8_t reg,uint8_t *out) {
    CHECK(t==&transaction);enum br_result e=io();
    /* Poison on error deliberately: callers must never interpret this byte. */
    if(e!=BR_OK) {*out=0xff;return e;}
    if(read_mode) {
        if(reg==8) *out=read_s2;
        else if(reg==0x12) *out=tx_mode;
        else if(reg==0x13) *out=rx_mode;
        else if(reg==6) *out=read_error;
        else CHECK(0);
    } else if(!auth_active) {
        if(reg==8) *out=calls==2 ? initial_s2 : cleared_s2;
        else if(reg==4) *out=initial_irq;
        else CHECK(0);
    } else {
        unsigned k=cycle<4?cycle:3;
        if(reg==8) *out=states[k][0];
        else if(reg==4) *out=states[k][1];
        else if(reg==6) {*out=states[k][2];++cycle;}
        else CHECK(0);
    }
    return BR_OK;
}
enum br_result br_write(struct br_transaction *t,uint8_t reg,uint8_t value) {
    CHECK(t==&transaction);enum br_result e=io();
    if(e==BR_OK && reg==1 && value==0x0e) auth_active=1;
    return e;
}
enum br_result br_transceive_exact(struct br_transaction *t,const uint8_t *tx,
    uint32_t n,uint8_t *out,uint32_t cap,uint32_t *bits,uint32_t expected) {
    CHECK(t==&transaction && n==2 && tx[0]==0x30 && cap==16 && expected==128);
    enum br_result e=io();if(e!=BR_OK)return e;
    for(unsigned i=0;i<16;++i)out[i]=(uint8_t)i;
    *bits=result_bits;return transceive_result;
}
enum br_result br_publish(struct br_transaction *t,uint8_t *out,const uint8_t *src,
    uint32_t n,uint32_t *bits,uint32_t value) {
    CHECK(t==&transaction && n==16 && !bits && !value);
    uint32_t k=br_platform_enter();
    enum br_result e=publish_result;
    if(!br_platform_commit_valid(t) || stop_epoch!=t->expected_epoch)e=BR_CANCELLED;
    if(e==BR_OK){memcpy(out,src,n);++published;}
    br_platform_leave(k);return e;
}
static struct ar_result auth(uint32_t limit) {
    struct ar_result r=ar_auth_checked(&transaction,&selection,0x60,4,key,limit);
    CHECK(r.code!=AR_AUTH_NEGATIVE && !io_under_lock && !lock_depth && lock_enters==lock_leaves);
    return r;
}
static struct ar_result read_block(uint8_t out[16]) {
    read_mode=1;
    struct ar_result r=ar_read_checked(&transaction,4,out);
    CHECK(r.code!=AR_AUTH_NEGATIVE && !io_under_lock && !lock_depth && lock_enters==lock_leaves);
    return r;
}
static void unchanged(const uint8_t *out) {for(unsigned i=0;i<16;++i)CHECK(out[i]==0xa5);}

int main(void) {
    struct ar_result r;uint8_t out[16];
    reset();r=auth(3);CHECK(r.code==AR_AUTH_STATUS_ACCEPTED && r.auth_command_issued && r.auth_command_attempted && r.snapshot_valid && r.transport_status_valid);
    unsigned auth_calls=calls,auth_valid=valid_calls;
    for(unsigned e=BR_INVALID;e<=BR_PROTOCOL;++e)for(unsigned n=1;n<=auth_calls;++n) {
        reset();injected=(enum br_result)e;fail_at=n;r=auth(3);
        CHECK(r.code!=AR_AUTH_STATUS_ACCEPTED && r.transport==(enum br_result)e && r.transport_status_valid && calls==n);
    }
    for(unsigned n=1;n<=auth_valid;++n) {
        reset();invalidate_at=n;r=auth(3);CHECK(r.code==AR_CANCELLED && r.transport==BR_CANCELLED);
    }
    for(int n=-2;n<=2;++n)if(n) {reset();selection.raw_result=n;r=auth(3);CHECK(r.code==AR_SELECT_NOT_OK && r.select_result==n && !calls && !r.transport_status_valid);}
    reset();selection.selected_uid[0]^=1;r=auth(3);CHECK(r.code==AR_UID_MISMATCH && !calls);
    reset();selection.selected_uid[4]=selection.expected_uid[4]=0;r=auth(3);CHECK(r.code==AR_INVALID && !calls);
    reset();stop_epoch=2;r=auth(3);CHECK(r.code==AR_CANCELLED && !calls);
    reset();state.quarantined=1;r=auth(3);CHECK(r.code==AR_TRANSPORT_UNKNOWN && !calls);
    reset();r=auth(0);CHECK(r.code==AR_INVALID && !calls);
    reset();r=auth(4097);CHECK(r.code==AR_INVALID && !calls);
    reset();initial_s2=0xff;r=auth(3);CHECK(r.code==AR_INVALID_SNAPSHOT && !r.auth_command_attempted);
    reset();cleared_s2=8;r=auth(3);CHECK(r.code==AR_INVALID_SNAPSHOT && !r.auth_command_attempted);
    reset();initial_irq=0x10;r=auth(3);CHECK(r.code==AR_INVALID_SNAPSHOT && !r.auth_command_attempted);
    /* Every Status2 byte, with clean Idle IRQ/error. In particular BR_OK+FF. */
    for(unsigned v=0;v<256;++v) {
        reset();states[0][0]=(uint8_t)v;r=auth(3);
        CHECK(r.code==((v!=0 && v!=8)?AR_INVALID_SNAPSHOT:v==8?AR_AUTH_STATUS_ACCEPTED:AR_AUTH_NOT_ESTABLISHED));
    }
    for(unsigned v=0;v<256;++v) {
        reset();states[0][1]=(uint8_t)v;r=auth(1);
        CHECK(r.code==((v&~0x11u)?AR_INVALID_SNAPSHOT:(v&1)?AR_NO_REPLY:(v&16)?AR_AUTH_STATUS_ACCEPTED:AR_AUTH_POLL_LIMIT));
    }
    for(unsigned v=1;v<256;++v) {reset();states[0][2]=(uint8_t)v;r=auth(3);CHECK(r.code==AR_READER_ERROR);}
    reset();memset(states,0,sizeof(states));r=auth(3);CHECK(r.code==AR_AUTH_POLL_LIMIT && r.status_polls==3 && r.transport==BR_OK);
    reset();states[0][0]=0;states[0][1]=0;r=auth(3);CHECK(r.code==AR_AUTH_STATUS_ACCEPTED && r.status_polls==2);
    reset();states[0][0]=0;states[0][1]=1;r=auth(3);CHECK(r.code==AR_NO_REPLY);
    reset();states[0][0]=8;states[0][1]=0x11;r=auth(3);CHECK(r.code==AR_NO_REPLY);
    reset();memset(out,0xa5,16);r=read_block(out);CHECK(r.code==AR_BLOCK_READ && published==1);for(unsigned i=0;i<16;++i)CHECK(out[i]==i);
    unsigned read_calls=calls,read_valid=valid_calls;
    for(unsigned e=BR_INVALID;e<=BR_PROTOCOL;++e)for(unsigned n=1;n<=read_calls;++n) {
        reset();memset(out,0xa5,16);injected=(enum br_result)e;fail_at=n;r=read_block(out);
        CHECK(r.code!=AR_BLOCK_READ && r.transport==(enum br_result)e && !published);unchanged(out);
    }
    for(unsigned n=1;n<=read_valid;++n) {
        reset();memset(out,0xa5,16);invalidate_at=n;r=read_block(out);CHECK(r.code==AR_CANCELLED && !published);unchanged(out);
    }
    for(unsigned e=BR_INVALID;e<=BR_PROTOCOL;++e) {
        reset();memset(out,0xa5,16);transceive_result=(enum br_result)e;r=read_block(out);
        CHECK(r.transport==(enum br_result)e && !published);unchanged(out);
        reset();memset(out,0xa5,16);publish_result=(enum br_result)e;r=read_block(out);
        CHECK(r.transport==(enum br_result)e && !published);unchanged(out);
    }
    for(unsigned k=0;k<5;++k) {
        reset();memset(out,0xa5,16);
        if(k==0)read_s2=0xff;
        if(k==1)read_s2=0;
        if(k==2)tx_mode=0xff;
        if(k==3)rx_mode=0xff;
        if(k==4)read_error=1;
        r=read_block(out);CHECK(r.code!=AR_BLOCK_READ && !published);unchanged(out);
    }
    reset();memset(out,0xa5,16);result_bits=127;r=read_block(out);CHECK(r.code==AR_READ_INVALID && !published);unchanged(out);
    printf("PASS %u checks. Typed-interface dependency doubles only; no RF hardware, full CPU, SELECT freshness or cleanup proof.\n",checks);
}
