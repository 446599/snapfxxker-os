#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "preload_auth_bridge.h"
static unsigned lock_depth, enters;
uint32_t rf_platform_enter(void) { assert(!lock_depth); ++enters; lock_depth=1; return 0x1234; }
void rf_platform_leave(uint32_t key) { assert(key==0x1234 && lock_depth==1); lock_depth=0; }
static struct rf_operation rf={ {7,9,0,0}, 11 };
static struct pl_state s;
static struct pl_ticket t;
static struct pl_auth auth;
static void begin(unsigned slot) {
    pl_init_exclusive(&s,3,slot); assert(pl_begin(&s,&t)==PL_OK);
    assert(pl_begin_auth(&s,&t,&rf,slot,&auth)==PL_OK);
}
static void fail(unsigned now,unsigned budget) {
    assert(pl_complete_auth(&s,&auth,PL_AUTH_REJECTED,now,budget)==PL_NEEDS_STOP);
    assert(s.reason==PL_REASON_AUTH_REJECTED);
}
static void stop(unsigned now) {
    struct pl_stop c;
    assert(pl_claim_stop(&s,&t,now,&c)==PL_OK);
    assert(pl_complete_stop(&s,&c,now+1,PL_STOP_PROVEN)==PL_OK);
    assert(s.phase==PL_STOPPED);
}
static void normal(void) {
    for(unsigned slot=0;slot<4;++slot) for(unsigned kind=0;kind<2;++kind) {
        struct pl_start a;
        begin(slot);
        assert(pl_claim_start(&s,&t,&a)==PL_OK);
        assert(pl_retire_start(&s,&a,PL_START_NO_LATE_WRITES)==PL_OK);
        assert(pl_complete_auth(&s,&auth,kind ? PL_AUTH_NOT_ATTEMPTED : PL_AUTH_OK,0,100)==PL_OK);
        assert(s.phase==PL_ACTIVE && s.reason==PL_REASON_NONE);
        /* The successful path can perform another stock rotation/retract. */
        assert(pl_claim_start(&s,&t,&a)==PL_OK);
        assert(pl_retire_start(&s,&a,PL_START_NO_LATE_WRITES)==PL_OK);
        assert(pl_finish_normal(&s,&t,PL_STOP_PROVEN)==PL_OK);
        assert(pl_begin(&s,&t)==PL_OK);
    }
}
static void failure_before_start(void) {
    struct pl_start a;
    begin(1); fail(10,100);
    assert(pl_claim_start(&s,&t,&a)==PL_BUSY);
    assert(pl_begin(&s,&t)==PL_BUSY);
    assert(pl_finish_normal(&s,&t,PL_STOP_PROVEN)==PL_BUSY);
    assert(pl_release_stopped(&s,&t)==PL_BUSY);
    assert(pl_complete_auth(&s,&auth,PL_AUTH_OK,11,100)==PL_STALE);
    stop(11); assert(pl_release_stopped(&s,&t)==PL_OK);
}
static void inflight_start(void) {
    struct pl_start a;
    struct pl_stop c;
    begin(3); assert(pl_claim_start(&s,&t,&a)==PL_OK); fail(10,100);
    assert(pl_claim_stop(&s,&t,11,&c)==PL_OK); /* Issue STOP immediately. */
    assert(pl_complete_stop(&s,&c,12,PL_STOP_PROVEN)==PL_BUSY);
    assert(s.phase==PL_ABORTING); /* Claimed stop proof cannot hide delayed enable. */
    assert(pl_retire_start(&s,&a,PL_START_NO_LATE_WRITES)==PL_NEEDS_STOP);
    stop(13); assert(pl_release_stopped(&s,&t)==PL_OK);
    begin(3); assert(pl_claim_start(&s,&t,&a)==PL_OK); fail(10,100);
    assert(pl_claim_stop(&s,&t,11,&c)==PL_OK);
    assert(pl_retire_start(&s,&a,PL_START_NO_LATE_WRITES)==PL_NEEDS_STOP);
    assert(pl_complete_stop(&s,&c,12,PL_STOP_PROVEN)==PL_STALE);
    assert(s.phase==PL_ABORTING); stop(13);
}
static void all_permutations(void) {
    /* F=AUTH fail, R=start retired, C=STOP claimed, D=STOP proof returned.
     * All 24 orderings, including invalid callbacks, must preserve the latch. */
    unsigned cases=0;
    for(int a=0;a<4;++a) for(int b=0;b<4;++b) for(int c=0;c<4;++c) for(int d=0;d<4;++d) {
        if(a==b||a==c||a==d||b==c||b==d||c==d) continue;
        struct pl_start start; struct pl_stop st={0}; int claimed=0;
        begin(0); assert(pl_claim_start(&s,&t,&start)==PL_OK);
        int events[]={a,b,c,d};
        for(unsigned i=0;i<4;++i) {
            switch(events[i]) {
            case 0: fail(10,100); break;
            case 1: (void)pl_retire_start(&s,&start,PL_START_NO_LATE_WRITES); break;
            case 2: claimed=pl_claim_stop(&s,&t,11,&st)==PL_OK; break;
            case 3: if(claimed) (void)pl_complete_stop(&s,&st,12,PL_STOP_PROVEN); break;
            }
            assert(!(s.phase==PL_STOPPED && s.start_pending));
            assert(s.phase!=PL_IDLE);
        }
        if(s.stop_pending) (void)pl_complete_stop(&s,&st,13,PL_STOP_PROVEN);
        if(s.phase!=PL_STOPPED) stop(14);
        assert(pl_release_stopped(&s,&t)==PL_OK); ++cases;
    }
    assert(cases==24);
}
static void stale_identity(void) {
    begin(2);
    struct pl_auth old=auth, bad=auth;
    uint32_t *fields=(uint32_t *)&bad;
    for(unsigned i=0;i<sizeof(bad)/sizeof(*fields);++i) {
        bad=old; fields[i]^=1;
        struct pl_state before=s;
        assert(pl_complete_auth(&s,&bad,PL_AUTH_REJECTED,10,100)==PL_STALE);
        assert(!memcmp(&before,&s,sizeof s));
    }
    fail(10,100); stop(11); assert(pl_release_stopped(&s,&t)==PL_OK);
    assert(pl_begin(&s,&t)==PL_OK);
    assert(pl_complete_auth(&s,&old,PL_AUTH_REJECTED,20,100)==PL_STALE);
    assert(s.phase==PL_ACTIVE);
    assert(pl_begin_auth(&s,&t,&rf,3,&auth)==PL_STALE); /* wrong sibling */
    struct pl_state peer; pl_init_exclusive(&peer,3,3); struct pl_state saved=peer;
    assert(pl_begin_auth(&s,&t,&rf,2,&auth)==PL_OK); fail(25,100);
    assert(!memcmp(&peer,&saved,sizeof peer));
}
static void stop_proof_and_time(void) {
    for(unsigned proof=0;proof<=15;++proof) {
        struct pl_stop c; begin(0); fail(10,100);
        assert(pl_claim_stop(&s,&t,11,&c)==PL_OK);
        int r=pl_complete_stop(&s,&c,12,proof);
        assert(r==(proof==PL_STOP_PROVEN ? PL_OK : PL_FAULT));
        assert(s.phase==(proof==PL_STOP_PROVEN ? PL_STOPPED : PL_UNKNOWN));
        if(proof!=PL_STOP_PROVEN) {
            assert(pl_begin(&s,&t)==PL_FAULT);
            assert(pl_release_stopped(&s,&t)==PL_BUSY);
        }
    }
    begin(0); fail(0xfffffff0U,32);
    assert(pl_poll(&s,&t,0x0f)==PL_NEEDS_STOP);
    assert(pl_poll(&s,&t,0x10)==PL_FAULT);
    struct pl_stop c; assert(pl_claim_stop(&s,&t,0x11,&c)==PL_OK);
    assert(pl_complete_stop(&s,&c,0x12,PL_STOP_PROVEN)==PL_FAULT);
    assert(s.phase==PL_UNKNOWN); /* A timeout cannot silently become success. */
}
static void faults_and_limits(void) {
    struct pl_start a; begin(0); assert(pl_claim_start(&s,&t,&a)==PL_OK);
    assert(pl_retire_start(&s,&a,PL_START_UNVERIFIED)==PL_FAULT);
    assert(pl_begin(&s,&t)==PL_FAULT);
    begin(0); s.sequence=UINT32_MAX;
    assert(pl_claim_start(&s,&t,&a)==PL_FAULT);
    pl_init_exclusive(&s,3,0); s.generation=UINT32_MAX;
    assert(pl_begin(&s,&t)==PL_FAULT);
    pl_init_exclusive(&s,0,0); assert(pl_begin(&s,&t)==PL_FAULT);
    pl_init_exclusive(&s,3,4); assert(pl_begin(&s,&t)==PL_FAULT);
    begin(0); struct pl_state before=s;
    assert(pl_complete_auth(&s,&auth,PL_AUTH_REJECTED,0,0)==PL_INVALID);
    assert(!memcmp(&before,&s,sizeof s));
    assert(pl_complete_auth(&s,&auth,PL_AUTH_TRANSPORT_UNKNOWN,0,100)==PL_NEEDS_STOP);
    assert(s.reason==PL_REASON_TRANSPORT_UNKNOWN);
}
static void bridge(void) {
    for(int tr=BR_INVALID;tr<=BR_PROTOCOL;++tr) for(int ev=PL_AUTH_NONE;ev<=PL_AUTH_UNCERTAIN;++ev)
        assert(pl_classify_auth(tr,ev)==PL_AUTH_TRANSPORT_UNKNOWN);
    assert(pl_classify_auth(BR_OK,PL_AUTH_NONE)==PL_AUTH_NOT_ATTEMPTED);
    assert(pl_classify_auth(BR_OK,PL_AUTH_VERIFIED)==PL_AUTH_OK);
    assert(pl_classify_auth(BR_OK,PL_AUTH_TERMINAL_NEGATIVE)==PL_AUTH_REJECTED);
    assert(pl_classify_auth(BR_OK,PL_AUTH_UNCERTAIN)==PL_AUTH_TRANSPORT_UNKNOWN);
}
int main(void) {
    normal(); failure_before_start(); inflight_start(); all_permutations();
    stale_identity(); stop_proof_and_time(); faults_and_limits(); bridge();
    assert(!lock_depth && enters>100);
    puts("PASS: normal lanes, narrow failure, 24 start/STOP orderings, stale RF/lane/epoch, proof masks, timeout/wrap, exhaustion and SPI result separation");
}
