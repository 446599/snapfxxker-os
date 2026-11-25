/* Real merged RF adapter + queue C. Only MMIO/RTOS/IRQ endpoints are doubles. */
#define main original_rf_integration_main
#include "test_integration.c"
#undef main
#undef CHECK
#define main original_queue_main
#define reset queue_reset
#define rf_requeue_entry queue_fixture_requeue
#define ph_irq_enter queue_fixture_irq_enter
#define ph_irq_leave queue_fixture_irq_leave
#define pq_test_read queue_fixture_read
#define pq_stock_send queue_fixture_stock_send
#include "test_preload_queue.c"
#undef main
#undef reset
#undef rf_requeue_entry
#undef ph_irq_enter
#undef ph_irq_leave
#undef pq_test_read
#undef pq_stock_send
/* Use ONE mask and current TCB for both modules, rather than unrelated doubles. */
uint32_t ph_irq_enter(void) {return rf_platform_enter();}
void ph_irq_leave(uint32_t k) {rf_platform_leave(k);}
uint32_t pq_test_read(uint32_t a,uint32_t n) {
    return a==0x20001dd0u ? mock_task : queue_fixture_read(a,n);
}
uint32_t pq_stock_send(uint32_t q,uint32_t item,uint32_t wait) {
    CHECK(mask==0);return queue_fixture_stock_send(q,item,wait);
}
static void setup(void) {
    reset(0);queue_reset();mock_task=0x20004000;mock_ipsr=pq_test_ipsr=0;
}
static unsigned observed_requeue_contexts;
void __real_rf_requeue_entry(uint32_t lane);
void __wrap_rf_requeue_entry(uint32_t lane) {
    CHECK(mask==0);
    uint32_t reader_index=lane>>1;
    unsigned found=0;struct pq_storage *s=&ex_storage_current()->queue;
    for(unsigned i=0;i<8;i++)if(s->pending[i].used==3) {
        const struct pq_pending *p=&s->pending[i];
        CHECK(p->lane==lane && p->tcb==mock_task);
        CHECK(p->source==PQ_REQUEUE && p->command==6);
        CHECK(p->queue==(reader_index?Q1:Q0));
        CHECK(p->generation==s->reader[reader_index].candidate.generation);
        ++found;
    }
    CHECK(found==1);++observed_requeue_contexts;
    __real_rf_requeue_entry(lane);
}
static void make_candidate(uint32_t lane) {
    linked(lane);CHECK(send_preload(lane,ITEM)==1);
    CHECK(receive(lane<2?Q0:Q1)==1);
    struct pq_token token;CHECK(pq_candidate(lane>>1,&token) && token.lane==lane);
}
static void no_preload_authority(void) {
    struct ph_storage *p=ph_platform_state();CHECK(p!=0);
    for(unsigned i=0;i<4;i++) CHECK(!p->blocked[i]);
    for(unsigned i=0;i<2;i++) CHECK(!p->reader[i].owned && !p->reader[i].epoch && p->reader[i].lane==UINT32_MAX);
    CHECK(!stock_calls && !f.sr_reads && !mask);
}
int main(void) {
    for(uint32_t lane=0;lane<4;lane++) {
        setup();make_candidate(lane);no_preload_authority();
        CHECK(rf_device_state.phase==RF_UNKNOWN && rf_device_state.revoked && !ex_state.owner_task);
        uint32_t observed=UINT32_MAX;
        CHECK(!rf_platform_background_lane(lane>>1,&observed) && observed==UINT32_MAX);
        rf_background_entry(lane>>1);no_preload_authority();
        CHECK(ex_state.stop_requested && rf_device_state.phase==RF_UNKNOWN);
        /* The actual queue wrapper reaches the actual RF gate with IRQ unmasked.
         * No stock queue/RF work is invoked and its temporary context is cleared. */
        setup();make_candidate(lane);unsigned before=send_calls;
        unsigned previous_contexts=observed_requeue_contexts;
        pq_preload_requeue(lane,job(lane));
        CHECK(observed_requeue_contexts==previous_contexts+1);
        CHECK(send_calls==before && ex_state.stop_requested);
        for(unsigned i=0;i<8;i++) CHECK(!ex_storage_current()->queue.pending[i].used);
        no_preload_authority();
    }
    /* Queue observations neither borrow nor erase a separately owned RF op. */
    setup();CHECK(ex_offline_begin(&reader,1000)==0);
    __typeof__(ex_state) ex_before=ex_state;struct rf_state rf_before=rf_device_state;
    struct ph_storage ph_before=*ph_platform_state();
    make_candidate(0);
    CHECK(!memcmp(&ex_before,&ex_state,sizeof(ex_before)));
    CHECK(!memcmp(&rf_before,&rf_device_state,sizeof(rf_before)));
    CHECK(!memcmp(&ph_before,ph_platform_state(),sizeof(ph_before)));
    /* Unknown queue source poisons only its observation; it is not an owned
     * AUTH failure, fabricated STOP signal or permission to consume RF. */
    byte(ITEM,1);CHECK(pq_enqueue_entry(Q0,ITEM,0,0)==0);
    CHECK(ex_storage_current()->queue.reader[0].fault&PQ_UNKNOWN_SOURCE);
    CHECK(!memcmp(&ex_before,&ex_state,sizeof(ex_before)));
    CHECK(!memcmp(&rf_before,&rf_device_state,sizeof(rf_before)));
    CHECK(!memcmp(&ph_before,ph_platform_state(),sizeof(ph_before)));
    CHECK(br_stock_read(&reader,0x12)==0x42 && !ex_state.stop_requested);
    printf("PASS %u unified isolation assertions; actual RF requeue gate/queue runtime, shared IRQ/TCB doubles. No physical owner/producer proof.\n",checks);
}
