#include "rf_stock_adapter.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
struct rf_state rf_device_state;
static unsigned locked,ready,stock_calls,cleanup_calls,bad_calls,now,fail_step;
static unsigned now_calls, expire_on_read, expire_in_stock;
static enum rf_proof proof;
uint32_t rf_platform_enter(void) { assert(!locked);locked=1;return 7; }
void rf_platform_leave(uint32_t key) { assert(locked && key==7);locked=0; }
uint32_t rf_platform_now(void) {
    assert(!locked);
    if(++now_calls==expire_on_read) now=100;
    return now;
}
int rf_platform_background_lane(uint32_t reader,uint32_t *lane) {
    /* Explicit synthetic queue-event association supplied by this unit fixture. */
    *lane=reader*2;return 1;
}
int rf_platform_ready(void) { assert(!locked);return (int)ready; }
uint32_t rf_platform_budget(uint32_t owner) { (void)owner;assert(!locked);return 100; }
enum rf_proof rf_platform_quiescence(const struct rf_operation *op) {
    assert(op->sequence && !locked);return proof;
}
int rf_platform_cleanup(const struct rf_cleanup *c) {
    assert(!locked);assert(c->step==cleanup_calls++);return c->step!=fail_step;
}
void rf_platform_bad_outcome(uint32_t owner) { assert(owner && !locked);++bad_calls; }
void rf_background_original(uint32_t index) { assert(index<2 && !locked);++stock_calls; }
int rf_cmd68_original(uint32_t cmd,const uint32_t *req,uint8_t *rsp) {
    assert(cmd==68 && *req<4 && !locked);++stock_calls;
    if(expire_in_stock) now=100;
    memset(rsp,0x5a,144);rsp[140]=0;return 1;
}
int rf_cmd13_original(uint32_t cmd,const uint32_t *req,uint8_t *rsp) {
    assert(cmd==13 && *req<4 && !locked);++stock_calls;
    memset(rsp,0xa5,144);rsp[140]=0;return 1;
}
int rf_cmd14_original(uint32_t cmd,const uint32_t *req,uint8_t *rsp) {
    assert(cmd==14 && *req<4 && !locked);++stock_calls;
    rf_background_entry(0); /* Must fail immediately instead of waiting. */
    *(uint32_t *)rsp=0;return 1;
}
void rf_requeue_original(uint32_t slot) { assert(slot<4 && !locked);++stock_calls; }
static void reset(void) {
    rf_init_exclusive(&rf_device_state,10);ready=1;stock_calls=0;cleanup_calls=0;
    bad_calls=0;now=0;fail_step=99;proof=RF_QUIESCENT;
    now_calls=0;expire_on_read=0;expire_in_stock=0;
}
int main(void) {
    uint32_t req=2,rsp_words[36];uint8_t *rsp=(uint8_t *)rsp_words;
    reset();ready=0;
    assert(rf_cmd68_entry(68,&req,rsp)==1 && rsp[140]==1 && !stock_calls);
    rf_background_entry(0);rf_requeue_entry(0);
    assert(rf_cmd14_entry(14,&req,rsp)==1 && rsp_words[0]==1 && !stock_calls);
    reset();
    assert(rf_cmd68_entry(68,&req,rsp)==1 && rsp[4]==0x5a && !rsp[140]);
    assert(stock_calls==1 && cleanup_calls==6 && rf_device_state.phase==RF_IDLE);
    reset();
    assert(rf_cmd13_entry(13,&req,rsp)==1 && rsp[4]==0xa5 && !rsp[140]);
    assert(stock_calls==1 && cleanup_calls==6);
    reset();
    assert(rf_cmd14_entry(14,&req,rsp)==1 && rsp_words[0]==0);
    assert(stock_calls==1 && cleanup_calls==6); /* Background contender skipped. */
    reset();rf_background_entry(0);assert(stock_calls==1 && cleanup_calls==6);
    reset();rf_requeue_entry(3);assert(stock_calls==1 && cleanup_calls==6);
    for(unsigned op=0;op<256;++op) {
        reset();req=0x80000000u | (op<<16);
        assert(rf_cmd68_entry(68,&req,rsp)==1 && rsp[140]==1 && !stock_calls && !cleanup_calls);
    }
    req=1;
    for(unsigned step=0;step<6;++step) {
        reset();fail_step=step;
        assert(rf_cmd68_entry(68,&req,rsp)==1 && rsp[140]==1 && rsp[4]==0);
        assert(stock_calls==1 && cleanup_calls==step+1 && bad_calls==1 && rf_device_state.phase==RF_UNKNOWN);
        rf_background_entry(0);assert(stock_calls==1);
    }
    reset();proof=RF_UNVERIFIED;
    assert(rf_cmd68_entry(68,&req,rsp)==1 && rsp[140]==1);
    assert(stock_calls==1 && !cleanup_calls && bad_calls==1 && rf_device_state.phase==RF_UNKNOWN);
    reset();struct rf_ticket t;
    assert(rf_acquire(&rf_device_state,RF_HOST,0,1,2,0,100,100,&t)==RF_OK);
    rf_background_entry(0);rf_requeue_entry(0);rf_cmd68_entry(68,&req,rsp);
    rf_cmd13_entry(13,&req,rsp);rf_cmd14_entry(14,&req,rsp);
    assert(!stock_calls && !cleanup_calls && rf_device_state.owner==RF_HOST);
    /* Acquisition at t=0 succeeds; begin at the exact lease deadline fails.
     * Verified cleanup must drain this ticket even though no stock code ran. */
    reset();expire_on_read=2;
    assert(rf_cmd68_entry(68,&req,rsp)==1 && rsp[140]==1);
    assert(!stock_calls && cleanup_calls==6 && bad_calls==1);
    assert(rf_device_state.phase==RF_IDLE);
    cleanup_calls=0;expire_on_read=0;
    assert(rf_cmd68_entry(68,&req,rsp)==1 && rsp[140]==0);
    assert(stock_calls==1 && cleanup_calls==6);
    for(unsigned step=0;step<6;++step) {
        reset();expire_on_read=2;fail_step=step;
        assert(rf_cmd68_entry(68,&req,rsp)==1 && rsp[140]==1);
        assert(!stock_calls && cleanup_calls==step+1 && bad_calls==1);
        assert(rf_device_state.phase==RF_UNKNOWN);
        rf_background_entry(0);
        assert(!stock_calls); /* No timeout-based release after failed proof. */
    }
    reset();expire_in_stock=1;
    assert(rf_cmd68_entry(68,&req,rsp)==1 && rsp[140]==1 && rsp[4]==0);
    assert(stock_calls==1 && cleanup_calls==6 && bad_calls==1);
    assert(rf_device_state.phase==RF_IDLE);
    /* Rejection only writes the four-byte response, even for unaligned output. */
    reset();ready=0;unsigned char bytes[6]={0xa5,0xa5,0xa5,0xa5,0xa5,0xa5};
    assert(rf_cmd14_entry(14,&req,bytes+1)==1);
    assert(bytes[0]==0xa5 && bytes[1]==1 && !bytes[2] && !bytes[3] && !bytes[4] && bytes[5]==0xa5);
    puts("PASS: five adapter entries, fail-closed unready/busy/unverified/cleanup failure, native response preservation in simulation, all 256 legacy raw ops rejected");
    puts("PASS: acquisition/begin exact-deadline cleanup, six failed-begin cleanup faults, late result suppression, unaligned control rejection");
    return 0;
}
