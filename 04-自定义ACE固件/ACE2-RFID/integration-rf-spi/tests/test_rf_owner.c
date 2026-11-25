#include "rf_owner.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <limits.h>
static unsigned lock_depth, calls;
uint32_t rf_platform_enter(void) { assert(!lock_depth); ++lock_depth; ++calls; return 0x19; }
void rf_platform_leave(uint32_t key) { assert(key==0x19 && lock_depth==1); --lock_depth; }
static struct rf_ticket acquire(struct rf_state *s,unsigned owner,unsigned now) {
    struct rf_ticket t={0};
    assert(rf_acquire(s,owner,0,owner==RF_HOST?9:0,owner==RF_HOST?8:0,now,100,200,&t)==RF_OK);
    assert(!lock_depth); return t;
}
static void cleanup(struct rf_state *s,unsigned now,int expected) {
    struct rf_cleanup c; int success=-1;
    for(unsigned i=0;i<RF_CLEAN_COUNT;++i) {
        assert(rf_claim_cleanup(s,&c)==RF_OK); assert(c.step==i); assert(!lock_depth);
        assert(rf_complete_cleanup(s,&c,now,1,&success)==RF_OK);
        assert(success==(i+1==RF_CLEAN_COUNT?expected:0));
    }
    assert(s->phase==RF_IDLE && s->owner==RF_NONE);
}
static void invariants(struct rf_state *s) {
    assert(!lock_depth);
    assert(s->phase!=RF_IDLE || (!s->in_flight && !s->cleaning_claimed && s->owner==RF_NONE));
    assert(!s->cleaning_claimed || (s->phase==RF_CLEANING && !s->in_flight));
    assert(s->cleanup_step<=RF_CLEAN_COUNT);
}
static void happy_and_pairs(void) {
    for(unsigned owner=RF_BACKGROUND;owner<=RF_CACHE;++owner) {
        struct rf_state s; rf_init_exclusive(&s,77);
        struct rf_ticket t=acquire(&s,owner,0),other;
        struct rf_operation op;
        for(unsigned rival=RF_BACKGROUND;rival<=RF_CACHE;++rival)
            assert(rf_acquire(&s,rival,3,rival==RF_HOST?10:0,rival==RF_HOST?11:0,1,100,100,&other)==RF_BUSY);
        assert(rf_begin_operation(&s,&t,1,&op)==RF_OK);
        assert(rf_begin_operation(&s,&t,2,&op)==RF_BUSY);
        assert(rf_finish(&s,&t,2)==RF_BUSY);
        assert(rf_claim_cleanup(&s,(struct rf_cleanup *)&other)==RF_BUSY);
        assert(rf_complete_operation(&s,&op,3,RF_QUIESCENT)==RF_OK);
        assert(rf_complete_operation(&s,&op,3,RF_QUIESCENT)==RF_STALE);
        /* Inter-frame gap still has an owner. */
        assert(rf_acquire(&s,RF_CMD68,2,0,0,4,100,100,&other)==RF_BUSY);
        assert(rf_begin_operation(&s,&t,4,&op)==RF_OK);
        assert(rf_complete_operation(&s,&op,5,RF_QUIESCENT)==RF_OK);
        assert(rf_finish(&s,&t,6)==RF_OK); cleanup(&s,7,1);
        other=acquire(&s,RF_CMD68,8); (void)other;
        assert(rf_cancel(&s,&t)==RF_STALE);
        assert(rf_complete_operation(&s,&op,9,RF_QUIESCENT)==RF_STALE);
        invariants(&s);
    }
}
static void faults(void) {
    for(unsigned failed=0;failed<RF_CLEAN_COUNT;++failed) {
        struct rf_state s;rf_init_exclusive(&s,1);struct rf_ticket t=acquire(&s,RF_HOST,0),dummy;
        assert(rf_finish(&s,&t,1)==RF_OK);
        for(unsigned step=0;step<=failed;++step) {
            struct rf_cleanup c;int ok;
            assert(rf_claim_cleanup(&s,&c)==RF_OK);
            assert(rf_complete_cleanup(&s,&c,2,step!=failed,&ok)==(step==failed?RF_FAULT:RF_OK));
            assert(!ok);
        }
        assert(s.phase==RF_UNKNOWN);
        assert(rf_acquire(&s,RF_BACKGROUND,0,0,0,2000,100,100,&dummy)==RF_FAULT);
        assert(rf_cancel(&s,&t)==RF_OK && s.phase==RF_UNKNOWN);
        invariants(&s);
    }
    struct rf_state s;rf_init_exclusive(&s,1);struct rf_ticket t=acquire(&s,RF_HOST,0);
    struct rf_operation op;struct rf_cleanup c;
    assert(rf_begin_operation(&s,&t,1,&op)==RF_OK);
    assert(rf_complete_operation(&s,&op,2,RF_UNVERIFIED)==RF_FAULT);
    assert(s.phase==RF_UNKNOWN && s.in_flight);
    assert(rf_claim_cleanup(&s,&c)==RF_BUSY);
}
static void expiry_cancel_disconnect(void) {
    for(unsigned event=0;event<3;++event) for(unsigned active=0;active<2;++active) {
        struct rf_state s;rf_init_exclusive(&s,1);struct rf_ticket t=acquire(&s,RF_HOST,0);
        struct rf_operation op;
        if(active) assert(rf_begin_operation(&s,&t,1,&op)==RF_OK);
        if(event==0) rf_expire(&s,100);
        if(event==1) assert(rf_cancel(&s,&t)==RF_OK);
        if(event==2) assert(rf_disconnect(&s,9)==RF_OK);
        assert(s.revoked && s.phase==(active?RF_DRAINING:RF_CLEANING));
        assert(rf_renew(&s,&t,100,100)==RF_BUSY);
        if(active) assert(rf_complete_operation(&s,&op,101,RF_QUIESCENT)==RF_OK);
        cleanup(&s,102,0);
    }
    /* Revocation after finish and between cleanup stages suppresses success. */
    for(unsigned event=0;event<3;++event) {
        struct rf_state s;rf_init_exclusive(&s,1);struct rf_ticket t=acquire(&s,RF_HOST,0);
        assert(rf_finish(&s,&t,1)==RF_OK);
        struct rf_cleanup c;int success=0;
        for(unsigned step=0;step<RF_CLEAN_COUNT;++step) {
            assert(rf_claim_cleanup(&s,&c)==RF_OK);
            if(step==RF_CLEAN_COUNT-1) {
                if(event==0) assert(rf_cancel(&s,&t)==RF_OK);
                if(event==1) assert(rf_disconnect(&s,9)==RF_OK);
                /* event2 expires at final commit itself; no timer callback needed. */
            }
            assert(rf_complete_cleanup(&s,&c,event==2&&step==RF_CLEAN_COUNT-1?100:2,1,&success)==RF_OK);
        }
        assert(!success && s.phase==RF_IDLE);
    }
}
static void stale_and_wrap(void) {
    struct rf_state s;rf_init_exclusive(&s,99);
    struct rf_ticket t=acquire(&s,RF_HOST,UINT32_MAX-20),bad=t;
    struct rf_operation op;
    bad.boot++;assert(rf_begin_operation(&s,&bad,1,&op)==RF_STALE);
    bad=t;bad.connection++;assert(rf_cancel(&s,&bad)==RF_STALE);
    bad=t;bad.session++;assert(rf_finish(&s,&bad,1)==RF_STALE);
    assert(rf_renew(&s,&t,1,UINT32_MAX)==RF_INVALID);
    assert(rf_renew(&s,&t,1,200)==RF_OK && s.deadline==s.hard_deadline);
    assert(rf_renew(&s,&t,179,1)==RF_BUSY && s.revoked);
    cleanup(&s,180,0);
    t=acquire(&s,RF_CMD68,181);
    s.sequence=UINT32_MAX;
    assert(rf_begin_operation(&s,&t,182,&op)==RF_FAULT && s.phase==RF_UNKNOWN);
    rf_init_exclusive(&s,100);s.generation=UINT32_MAX;
    assert(rf_acquire(&s,RF_CMD68,0,0,0,0,1,1,&bad)==RF_FAULT);
    rf_init_exclusive(&s,0);
    assert(rf_acquire(&s,RF_CMD68,0,0,0,0,1,1,&bad)==RF_FAULT);
}
static void stale_cleanup(void) {
    struct rf_state s;rf_init_exclusive(&s,1);struct rf_ticket t=acquire(&s,RF_CMD68,0);
    assert(rf_finish(&s,&t,1)==RF_OK);
    struct rf_cleanup c,bad;int success;
    assert(rf_claim_cleanup(&s,&c)==RF_OK);bad=c;bad.sequence++;
    assert(rf_complete_cleanup(&s,&bad,2,1,&success)==RF_STALE);
    bad=c;bad.step++;assert(rf_complete_cleanup(&s,&bad,2,1,&success)==RF_STALE);
    assert(rf_complete_cleanup(&s,&c,2,1,&success)==RF_OK);
    assert(rf_complete_cleanup(&s,&c,2,1,&success)==RF_STALE);
    assert(s.cleanup_step==1);
    assert(rf_claim_cleanup_for(&s,0,&c)==RF_INVALID);
    struct rf_ticket old=t;
    old.generation--;
    struct rf_state before=s;
    assert(rf_claim_cleanup_for(&s,&old,&c)==RF_STALE);
    assert(!memcmp(&s,&before,sizeof(s)));
    for(unsigned i=1;i<RF_CLEAN_COUNT;++i) {
        assert(rf_claim_cleanup_for(&s,&t,&c)==RF_OK);
        assert(rf_complete_cleanup(&s,&c,2,1,&success)==RF_OK);
    }
    old=t;t=acquire(&s,RF_CMD68,3);
    assert(rf_finish(&s,&t,4)==RF_OK);before=s;
    assert(rf_claim_cleanup_for(&s,&old,&c)==RF_STALE);
    assert(!memcmp(&s,&before,sizeof(s)));
    cleanup(&s,5,1);
}
static unsigned rnd=1;
static unsigned random32(void) { rnd=rnd*1664525u+1013904223u;return rnd; }
static void randomized(void) {
    for(unsigned seed=1;seed<=50;++seed) {
        struct rf_state s;rf_init_exclusive(&s,seed);rnd=seed;
        struct rf_ticket t={0};struct rf_operation op={0};struct rf_cleanup c={0};
        for(unsigned now=0;now<2000;++now) {
            int ok;unsigned action=random32()%10;
            switch(action) {
            case 0:(void)rf_acquire(&s,RF_HOST,random32()%4,9,8,now,50,100,&t);break;
            case 1:(void)rf_begin_operation(&s,&t,now,&op);break;
            case 2:(void)rf_complete_operation(&s,&op,now,random32()%10?RF_QUIESCENT:RF_UNVERIFIED);break;
            case 3:(void)rf_finish(&s,&t,now);break;
            case 4:(void)rf_cancel(&s,&t);break;
            case 5:(void)rf_renew(&s,&t,now,30);break;
            case 6:rf_expire(&s,now);break;
            case 7:(void)rf_claim_cleanup(&s,&c);break;
            case 8:(void)rf_complete_cleanup(&s,&c,now,random32()%10!=0,&ok);break;
            case 9:(void)rf_disconnect(&s,random32()%2?9:10);break;
            }
            invariants(&s);
        }
    }
}
int main(void) {
    happy_and_pairs();faults();expiry_cancel_disconnect();stale_and_wrap();stale_cleanup();randomized();
    printf("PASS: 25 owner pairs, six cleanup-stage faults, expiry/cancel/disconnect, stale tickets, wrap, 100000 random events; %u metadata sections\n",calls);
    return 0;
}
