#define main original_integration_main
#include "test_integration.c"
#undef main
static const uint8_t cfg_regs[]={0x02,0x12,0x13,0x0d,0x2a,0x2b,0x2c,0x2d,0x14};
static const uint8_t cfg_values[]={0x55,0x42,0x12,0x03,0x83,0x12,0x44,0x55,0x03};
static void halt_reset(unsigned budget) {
    reset(budget);f.halt_mode=1;f.halt_irq=0x40;f.irq=0;
    for(unsigned i=0;i<sizeof(cfg_regs);i++) f.registers[cfg_regs[i]]=cfg_values[i];
    f.registers[8]=8;
}
static int halt_call(void) {struct br_stock_context ctx={0,&reader};return br_stock_halt(&ctx);}
int main(void) {
    halt_reset(10000);CHECK(halt_call()==0);
    unsigned total=f.sr_reads;
    CHECK(!ex_state.stop_requested && ex_state.transaction.polls_left==10000-total);
    for(unsigned i=0;i<sizeof(cfg_regs);i++) CHECK(f.registers[cfg_regs[i]]==cfg_values[i]);
    CHECK(!(f.registers[8]&8) && !f.registers[1] && !f.registers[10] && !f.irq);
    CHECK(rf_device_state.phase==RF_ACTIVE && rf_device_state.in_flight);
    CHECK(ex_finalize()==1);fault_check(); /* register completion is not quiescence */
    for(unsigned p=1;p<=total;p++) {
        halt_reset(10000);f.fail_at=p;f.error_mask=0x40;
        CHECK(halt_call()==1 && f.sr_reads==p);fault_check();
        halt_reset(10000);f.cancel_at=p;
        CHECK(halt_call()==1 && f.sr_reads<=p);
        CHECK(f.dr_writes==f.cancelled_dr_writes);fault_check();
    }
    for(unsigned budget=1;budget<total;budget++) {
        halt_reset(budget);CHECK(halt_call()==1 && f.sr_reads==budget);fault_check();
    }
    halt_reset(512);f.halt_irq=0;
    CHECK(halt_call()==1 && f.sr_reads==512);fault_check();
    const unsigned bad_irq[]={1,2,0x10,0x20,0x60,0x42};
    for(unsigned i=0;i<sizeof(bad_irq)/sizeof(bad_irq[0]);i++) {
        halt_reset(10000);f.halt_irq=bad_irq[i];CHECK(halt_call()==1);fault_check();
    }
    for(unsigned bit=1;bit<=128;bit<<=1) {
        halt_reset(10000);f.reader_error=bit;CHECK(halt_call()==1);fault_check();
    }
    const uint8_t poisoned[]={1,2,0x12,0x13,0x0d,0x2a,0x2b,0x2c,0x2d,0x14,0x0a,4};
    for(unsigned i=0;i<sizeof(poisoned);i++) {
        halt_reset(10000);f.poison_reg=poisoned[i];CHECK(halt_call()==1);fault_check();
    }
    halt_reset(10000);mutate_commit=total;
    CHECK(halt_call()==1);fault_check();
    /* A revoked owner is rejected before another register touches SPI. */
    halt_reset(10000);rf_device_state.revoked=1;rf_device_state.phase=RF_DRAINING;
    CHECK(halt_call()==1 && !f.sr_reads);fault_check();
    halt_reset(10000);CHECK(br_stock_read(&reader,0x12)==0x42);
    unsigned before=f.sr_reads;rf_device_state.revoked=1;rf_device_state.phase=RF_DRAINING;
    br_stock_write(&reader,0x12,1);CHECK(f.sr_reads==before);fault_check();
    /* Borrowing the global transaction from another task or ISR is denied.
     * Busy exchange guard also refuses same-task nested register access. */
    halt_reset(10000);mock_task=0x20003100;
    CHECK(halt_call()==1 && !f.sr_reads);fault_check();
    for(unsigned irq=1;irq<=255;irq++) {
        halt_reset(10000);mock_ipsr=irq;
        CHECK(halt_call()==1 && !f.sr_reads);fault_check();
    }
    halt_reset(10000);CHECK(br_platform_exchange_claim(&ex_state.transaction));
    CHECK(halt_call()==1 && !f.sr_reads);fault_check();
    br_platform_exchange_finish(&ex_state.transaction);CHECK(!(ex_state.owner_task&1u));
    halt_reset(10000);mock_ipsr=3;
    CHECK(br_read(&ex_state.transaction,0x12,&f.registers[0])==BR_CANCELLED && !f.sr_reads);
    mock_ipsr=0;br_platform_denied(&reader,BR_CANCELLED);fault_check();
    for(unsigned mode=0;mode<3;mode++) for(unsigned p=1;p<=total;p++) {
        halt_reset(10000);f.nested_at=p;f.nested_mode=mode;
        CHECK(halt_call()==1 && f.sr_reads==p);fault_check();
        CHECK(!(ex_state.owner_task&1u));
    }
    halt_reset(10000);uint8_t published=0xa5;tail_output=&published;
    CHECK(br_read(&ex_state.transaction,0x12,&published)==BR_OK);
    CHECK(tail_fire && published==0x42 && ex_state.stop_requested);
    tail_output=0;fault_check(); /* STOP occurs after atomic publication */
    uint8_t tx[]={0x50,0},rx[2]={0xa5,0xa5};uint32_t bits=0xdeadbeef;
    halt_reset(10000);
    CHECK(br_stock_transceive_checked(&reader,4,tx,2,rx,&bits,0x0800e187)==0);
    CHECK(rx[0]==0xa5 && rx[1]==0xa5 && bits==0xdeadbeef);
    for(unsigned n=0;n<4;n++) {
        halt_reset(10000);CHECK(br_stock_transceive_checked(&reader,4,tx,n==2?3:n,rx,&bits,0x0800e187)==1);
        CHECK(!f.sr_reads);fault_check();
    }
    halt_reset(10000);tx[1]=1;
    CHECK(br_stock_transceive_checked(&reader,4,tx,2,rx,&bits,0x0800e187)==1 && !f.sr_reads);fault_check();
    halt_reset(10000);tx[1]=0;
    CHECK(br_stock_transceive_checked(&reader,4,tx,2,rx,&bits,0x0800e189)==1 && !f.sr_reads);fault_check();
    /* Real adapter does not fabricate even-lane ownership from reader index. */
    reset(0);uint32_t lane=0xdeadbeef;
    CHECK(!rf_platform_background_lane(0,&lane) && lane==0xdeadbeef);
    rf_background_entry(0);fault_check();
    /* New stock operation binding uses the checked shared state and exact
     * descriptor. This is a caller/descriptor fixture, not original RTOS. */
    reset(0);rf_init_exclusive(&rf_device_state,123);struct rf_ticket ticket;
    struct rf_operation op;reader.aux_port=0x40010c00u;
    CHECK(rf_acquire(&rf_device_state,RF_CMD68,0,0,0,0,100,100,&ticket)==RF_OK);
    CHECK(rf_begin_operation(&rf_device_state,&ticket,0,&op)==RF_OK);
    CHECK(rf_platform_bind_operation(&op,RF_CMD68,0)==1);
    CHECK(ex_state.transaction.reader==&reader && ex_state.transaction.polls_left==8192);
    ex_state.transaction.polls_left=7;
    CHECK(rf_platform_bind_operation(&op,RF_CMD68,0)==1 && ex_state.transaction.polls_left==7);
    CHECK(rf_platform_bind_operation(&op,RF_CMD68,2)==0);
    struct rf_ticket other;
    CHECK(rf_acquire(&rf_device_state,RF_BACKGROUND,2,0,0,0,100,100,&other)==RF_BUSY);
    CHECK(!rf_platform_ready() && rf_platform_quiescence(&op)==RF_UNVERIFIED);
    printf("PASS %u HALT/runtime assertions; HALT register path %u SPI polls. "
        "No physical tag/cleanup/STOP or RTOS proof.\n",assertions,total);
}
