#include "event_mock.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
char pq_verified_queue_lifetime,pq_verified_all_queue_sources;
static uint8_t low[0x2600];
uint32_t pq_test_ipsr;
static uint32_t irq,receive_before_return,forced_failure,send_calls,copy_calls,attack;
static unsigned checks;
#define CHECK(x) do {assert(x);++checks;} while(0)
#define Q0 0x20003000u
#define Q1 0x20003100u
#define OTHER 0x20003200u
#define ITEM 0x20002000u
#define OUT 0x20002020u
static void *map(uint32_t a,uint32_t n) {
    if(a>=EX_HEAP_FIRST_PAYLOAD-8) return ex_test_map(a,n);
    assert(a>=0x20000000u && n<=sizeof(low) && a-0x20000000u<=sizeof(low)-n);
    return low+(a-0x20000000u);
}
uint32_t pq_test_read(uint32_t a,uint32_t n) {
    uint32_t v=0;memcpy(&v,map(a,n),n);return v;
}
void pq_test_write(uint32_t a,uint32_t v) {memcpy(map(a,4),&v,4);}
static void byte(uint32_t a,uint32_t v) {*(uint8_t *)map(a,1)=(uint8_t)v;}
uint32_t ph_irq_enter(void) {uint32_t old=irq;irq=1;return old;}
void ph_irq_leave(uint32_t old) {irq=old;}
uint32_t pq_stock_copy(uint32_t d,uint32_t s,uint32_t n) {
    ++copy_calls;memcpy(map(d,n),map(s,n),n);return d;
}
uint32_t pq_enqueue_original(uint32_t q,uint32_t item) {
    uint32_t w=pq_test_read(q+4,4),n=pq_test_read(q+56,4);
    (void)pq_stock_copy(w,item,1);
    pq_test_write(q+4,w==q+81?q+80:w+1);pq_test_write(q+56,n+1);return 0;
}
static uint32_t receive(uint32_t q) {
    uint32_t n=pq_test_read(q+56,4);CHECK(n>0);
    uint32_t p=pq_test_read(q+12,4);p=p==q+81?q+80:p+1;
    pq_test_write(q+12,p);
    CHECK(pq_receive_copy(OUT,p,1,q,n)==n-1);
    return pq_test_read(OUT,1);
}
uint32_t pq_stock_send(uint32_t q,uint32_t item,uint32_t wait) {
    (void)wait;++send_calls;CHECK(irq==0);
    if(forced_failure || pq_test_read(q+56,4)>=2) return 0;
    if(attack) {
        uint32_t mode=attack;attack=0;
        if(mode==1) pq_test_write(0x20001dd0,0x20004100);
        if(mode==2) pq_test_ipsr=16;
        CHECK(pq_enqueue_entry(q,item,mode==2?0x08017a45:0x080167fb,
                                mode==3?0x08001235:0x0801ffff)==0);
        (void)receive(q);
        struct pq_token out;CHECK(!pq_candidate(q==Q0?0:1,&out));
        pq_test_write(0x20001dd0,0x20004000);pq_test_ipsr=0;
    }
    CHECK(pq_enqueue_entry(q,item,0x080167fbu,0x0801ffffu)==0);
    if(receive_before_return) (void)receive(q);
    return 1;
}
static void queue(uint32_t q) {
    ex_test_store32(q-8,0);ex_test_store32(q-4,0x80000060u);
    memset(ex_test_map(q,82),0,82);
    pq_test_write(q,q+80);pq_test_write(q+4,q+80);pq_test_write(q+8,q+82);
    pq_test_write(q+12,q+81);pq_test_write(q+60,2);pq_test_write(q+64,1);
}
static uint32_t job(uint32_t lane) {return 0x200012e8u+(lane>>1)*208+lane*32+140;}
static struct pq_storage *reset(void) {
    mock_storage_reset();memset(low,0,sizeof(low));irq=0;
    mock_handle=ex_event_factory();CHECK(mock_handle);
    queue(Q0);queue(Q1);queue(OTHER);
    pq_test_write(0x2000189c,Q0);pq_test_write(0x200018a0,Q1);
    forced_failure=receive_before_return=copy_calls=send_calls=pq_test_ipsr=attack=0;
    pq_test_write(0x20001dd0,0x20004000);
    return &ex_storage_current()->queue;
}
static void linked(uint32_t lane) {
    byte(job(lane),lane);pq_job_enqueued(lane);byte(job(lane)+28,1);
}
static uint32_t send_preload(uint32_t lane,uint32_t item) {
    byte(item,1);return pq_send(lane<2?Q0:Q1,item,UINT32_MAX,PQ_PRELOAD,lane,job(lane));
}
void rf_requeue_entry(uint32_t lane) {
    byte(ITEM+2,6);CHECK(pq_send(lane<2?Q0:Q1,ITEM+2,0,PQ_REQUEUE,UINT32_MAX,0)==1);
}
int main(void) {
    struct pq_token t;
    for(uint32_t lane=0;lane<4;lane++) {
        struct pq_storage *s=reset();linked(lane);uint32_t q=lane<2?Q0:Q1;
        CHECK(!pq_candidate(lane>>1,&t));CHECK(send_preload(lane,ITEM)==1);
        CHECK(!pq_candidate(lane>>1,&t));CHECK(receive(q)==1);
        CHECK(pq_candidate(lane>>1,&t));CHECK(t.lane==lane && t.generation==1);
        CHECK(s->reader[lane>>1].fault==0 && !irq);
        pq_job_unlinked(lane);byte(job(lane)+28,0);CHECK(!pq_candidate(lane>>1,&t));
        linked(lane);CHECK(s->lane[lane].generation==2);
        receive_before_return=1;CHECK(send_preload(lane,ITEM)==1);
        CHECK(pq_candidate(lane>>1,&t) && t.generation==2);CHECK(!irq);
        for(unsigned i=0;i<8;i++) CHECK(!s->pending[i].used);
        for(unsigned i=0;i<4;i++) CHECK(!ex_storage_current()->preload.blocked[i]);
    }
    /* Fixed node reuse invalidates the queued older generation. */
    struct pq_storage *s=reset();linked(0);CHECK(send_preload(0,ITEM)==1);
    pq_job_unlinked(0);linked(0);CHECK(receive(Q0)==1);CHECK(!pq_candidate(0,&t));
    CHECK(send_preload(0,ITEM)==1);CHECK(receive(Q0)==1);CHECK(pq_candidate(0,&t));
    CHECK(t.generation==2 && s->reader[0].fault==0);
    /* Full and failed send do not append a phantom sideband entry. */
    s=reset();linked(0);CHECK(send_preload(0,ITEM)==1);
    byte(ITEM+1,6);CHECK(pq_send(Q0,ITEM+1,0,PQ_REQUEUE,UINT32_MAX,0)==1);
    struct pq_reader before=s->reader[0];CHECK(send_preload(0,ITEM)==0);
    CHECK(!memcmp(&before,&s->reader[0],sizeof(before)));
    CHECK(receive(Q0)==1);CHECK(pq_candidate(0,&t));CHECK(receive(Q0)==6);CHECK(!pq_candidate(0,&t));
    before=s->reader[0];forced_failure=1;CHECK(send_preload(0,ITEM)==0);
    CHECK(!memcmp(&before,&s->reader[0],sizeof(before)));forced_failure=0;
    /* Both control commands and cleanup preserve bytes but never own a lane. */
    for(uint32_t source=PQ_CLEANUP;source<=PQ_CONTROL;source++) {
        uint32_t commands[2]={source==PQ_REQUEUE?6u:0u,8u};
        for(unsigned j=0;j<(source==PQ_CONTROL?2u:1u);j++) {
            s=reset();byte(ITEM,commands[j]);
            CHECK(pq_send(Q0,ITEM,10,source,UINT32_MAX,0)==1);
            CHECK(receive(Q0)==commands[j]);CHECK(!pq_candidate(0,&t));CHECK(!s->reader[0].fault);
        }
    }
    /* Unwrapped source is explicitly unknown, not lane zero / current owner. */
    s=reset();linked(0);byte(ITEM,1);CHECK(pq_enqueue_entry(Q0,ITEM,0,0)==0);
    CHECK(s->reader[0].fault&PQ_UNKNOWN_SOURCE);CHECK(receive(Q0)==1);CHECK(!pq_candidate(0,&t));
    /* Queue handle alias, malformed queue and pending exhaustion grant nothing. */
    s=reset();linked(0);s->lane[0].generation=UINT32_MAX;pq_job_enqueued(0);
    CHECK(!s->lane[0].live && (s->reader[0].fault&PQ_EXHAUSTED));
    s=reset();linked(0);for(unsigned i=0;i<8;i++) s->pending[i].used=1;
    CHECK(send_preload(0,ITEM)==1);CHECK(s->reader[0].fault&PQ_EXHAUSTED);CHECK(receive(Q0)==1);CHECK(!pq_candidate(0,&t));
    s=reset();linked(0);pq_test_write(Q0+64,2);CHECK(send_preload(0,ITEM)==1);
    CHECK(s->reader[0].fault&PQ_BAD_QUEUE);CHECK(!pq_candidate(0,&t));
    /* Other handles receive original queue bytes/count and touch no sideband. */
    s=reset();struct pq_storage saved=*s;byte(ITEM,0xa5);
    CHECK(pq_send(OTHER,ITEM,0,PQ_PRELOAD,0,job(0))==1);
    CHECK(receive(OTHER)==0xa5);CHECK(!memcmp(s,&saved,sizeof(saved)));
    /* Missing extension retains native byte traffic without fake association. */
    reset();uint32_t handle=mock_handle;mock_handle=0;byte(ITEM,1);
    CHECK(pq_send(Q0,ITEM,0,PQ_PRELOAD,0,job(0))==1);
    CHECK(receive(Q0)==1);CHECK(!pq_candidate(0,&t));mock_handle=handle;
    CHECK(!pq_candidate(0,&t));
    /* A blocked sender's item pointer is not an authorization credential. */
    for(unsigned mode=1;mode<=3;mode++) {
        s=reset();linked(0);attack=mode;CHECK(send_preload(0,ITEM)==1);
        CHECK(s->reader[0].fault&PQ_UNKNOWN_SOURCE);CHECK(receive(Q0)==1);
        CHECK(!pq_candidate(0,&t));
    }
    /* Unified revision: malformed TCB values cannot become a queue source. */
    const uint32_t bad_tcb[]={0,0x20002698u,0x20004001u,0x20004002u,0x20004003u,0x20007e88u};
    for(unsigned i=0;i<sizeof(bad_tcb)/sizeof(bad_tcb[0]);i++) {
        s=reset();linked(0);pq_test_write(0x20001dd0,bad_tcb[i]);
        CHECK(send_preload(0,ITEM)==1);CHECK(receive(Q0)==1);
        CHECK(!pq_candidate(0,&t) && (s->reader[0].fault&PQ_UNKNOWN_SOURCE));
        for(unsigned j=0;j<8;j++) CHECK(!s->pending[j].used);
        s=reset();linked(0);CHECK(send_preload(0,ITEM)==1);CHECK(receive(Q0)==1);
        CHECK(pq_candidate(0,&t));pq_test_write(0x20001dd0,bad_tcb[i]);
        pq_preload_requeue(0,job(0));CHECK(receive(Q0)==6);
        CHECK(!pq_candidate(0,&t));
        for(unsigned j=0;j<8;j++) CHECK(!s->pending[j].used);
    }
    /* A sibling merely queued must not revoke the currently consumed lane. */
    s=reset();linked(0);CHECK(send_preload(0,ITEM)==1);CHECK(receive(Q0)==1);
    CHECK(pq_candidate(0,&t) && t.lane==0);linked(1);
    CHECK(pq_candidate(0,&t) && t.lane==0);
    pq_preload_requeue(0,job(0));CHECK(receive(Q0)==6);
    CHECK(pq_candidate(0,&t) && t.lane==0 && t.generation==1);
    CHECK(send_preload(1,ITEM)==1);CHECK(receive(Q0)==1);
    CHECK(pq_candidate(0,&t) && t.lane==1);
    /* Requeue from a different/unknown operation cannot inherit that lane. */
    pq_preload_requeue(0,job(0));CHECK(receive(Q0)==6);CHECK(!pq_candidate(0,&t));
    printf("PASS %u queue assertions; real C/accessor, stock queue-copy doubles; no ownership/STOP or RTOS proof.\n",checks);
    return 0;
}
