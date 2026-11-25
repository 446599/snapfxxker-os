/* Exercise real RF binding/transport, with fixture time and MMIO only. */
#define main original_integration_main
#include "test_integration.c"
#undef main

static void binding_case(int rebind,int hard) {
    reset(0);reader.aux_port=0x40010c00u;
    rf_init_exclusive(&rf_device_state,1);
    struct rf_ticket ticket;struct rf_operation op;
    CHECK(rf_acquire(&rf_device_state,RF_BACKGROUND,0,0,0,0,100,200,&ticket)==RF_OK);
    CHECK(rf_begin_operation(&rf_device_state,&ticket,0,&op)==RF_OK);
    if(rebind)CHECK(rf_platform_bind_operation(&op,RF_BACKGROUND,0));
    unsigned budget=ex_state.transaction.polls_left;
    if(hard)rf_device_state.hard_deadline=50;
    ex_state.offline_now=hard?50:100;
    CHECK(!rf_platform_bind_operation(&op,RF_BACKGROUND,0));
    CHECK(rf_device_state.revoked && rf_device_state.phase==RF_DRAINING);
    CHECK(ex_state.transaction.polls_left==budget && !f.sr_reads && !mask);
}
int main(void) {
    for(unsigned masked=0;masked<2;masked++) {
        reset(1000);mask=masked;ex_state.offline_now=100;
        uint8_t out=0xa5;
        CHECK(br_read(&ex_state.transaction,0x12,&out)!=BR_OK);
        CHECK(out==0xa5 && !f.sr_reads && !f.dr_writes && mask==masked);
        CHECK(rf_device_state.revoked && ex_state.transport.quarantined);
        mask=0;
    }
    reset(1000);ex_state.offline_now=99;CHECK(br_stock_read(&reader,0x12)==0x42);
    reset(1000);rf_device_state.hard_deadline=50;ex_state.offline_now=50;
    CHECK(br_stock_read(&reader,0x12)==0xff && !f.sr_reads);fault_check();
    /* Same wraparound convention as rf_owner; neither early nor late expiry. */
    reset(1000);rf_device_state.deadline=rf_device_state.hard_deadline=5;
    ex_state.offline_now=UINT32_MAX-2;CHECK(br_stock_read(&reader,0x12)==0x42);
    ex_state.offline_now=4;CHECK(br_stock_read(&reader,0x12)==0x42);
    unsigned before=f.sr_reads;ex_state.offline_now=5;
    CHECK(br_stock_read(&reader,0x12)==0xff && f.sr_reads==before);fault_check();
    /* Every SR-read boundary, including final completion, plus a stuck SR. */
    for(unsigned kind=1;kind<=3;kind++)for(unsigned p=1;p<=6;p++) {
        reset(1000);f.transition_at=p;f.transition_kind=kind;f.transition_now=100;
        uint8_t out=0xa5;
        CHECK(br_read(&ex_state.transaction,0x12,&out)!=BR_OK);
        CHECK(out==0xa5 && f.sr_reads==p && f.dr_writes==f.transition_dr_writes);
        CHECK((f.cs_odr&reader.cs_mask) && !(f.aux_odr&reader.aux_mask));
        CHECK(!mask && !(ex_state.owner_task&1u));
    }
    reset(1000);f.stall_at=1;f.stall_value=0x80;
    f.transition_at=4;f.transition_kind=1;f.transition_now=100;
    CHECK(br_stock_read(&reader,0x12)==0xff && f.sr_reads==4 && !f.dr_writes);fault_check();
    /* Bulk publication is denied too; late data may not become slot metadata. */
    reset(1000);ex_state.offline_now=100;uint8_t dst=0xa5,src=0x42;uint32_t bits=0xdead;
    CHECK(br_publish(&ex_state.transaction,&dst,&src,1,&bits,8)!=BR_OK);
    CHECK(dst==0xa5 && bits==0xdead && !f.sr_reads);
    for(int rebind=0;rebind<2;rebind++)for(int hard=0;hard<2;hard++)binding_case(rebind,hard);
    printf("PASS %u live-lease assertions; fixture-clock expiry and owner changes, not a real-time or physical STOP proof.\n",assertions);
    return 0;
}
