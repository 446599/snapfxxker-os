#define main core_test_main
#include "test_bounded_rc522.c"
#undef main
#include "stock_bounded_bridge.h"
static unsigned denied,lookup_fail,select_fault,original_calls;
struct br_transaction *br_platform_transaction(const struct br_reader *r) {
    (void)r; return lookup_fail ? 0 : &txn;
}
void br_platform_denied(const struct br_reader *r,enum br_result e) {
    (void)r; ++denied;
    if(!state.quarantined) state.cause=e;
    state.quarantined=1;
}
int br_stock_select_original(struct br_stock_context *ctx,uint8_t *out,int mode) {
    (void)mode; ++original_calls;
    if(select_fault==1) { f.fail_at=f.sr_reads+1;f.error_mask=0x40; }
    br_stock_write(ctx->reader,0x12,0x22);
    (void)br_stock_read(ctx->reader,0x12);
    for(unsigned i=0;i<22;i++) out[i]=(uint8_t)i;
    return select_fault==2 ? 2 : 0; /* Adversarial stock success after void fault. */
}
static void reset_bridge(uint32_t b) {
    reset(b);denied=lookup_fail=select_fault=original_calls=0;
}
int main(void) {
    core_test_main();
    uint8_t tx[2]={0x30,4},rx[66];uint32_t bits;
    const uintptr_t pcs[4]={0x0800dc41,0x0800dd05,0x0800e00d,0x0800e217};
    const unsigned capacities[4]={1,5,2,16};
    for(unsigned s=0;s<4;s++) for(unsigned n=0;n<256;n++) {
        reset_bridge(4000);f.rx_count=n;bits=0xdeadbeef;memset(rx,0xa5,sizeof(rx));
        int e=br_stock_transceive_checked(&reader,12,tx,2,rx+1,&bits,pcs[s]);
        CHECK(e==(n==capacities[s]?0:1));
        CHECK(rx[0]==0xa5 && rx[capacities[s]+1]==0xa5);
        if(e) CHECK(bits==0xdeadbeef && rx[1]==0xa5);
        else CHECK(bits==8*capacities[s]);
    }
    reset_bridge(1000);bits=0xdeadbeef;
    CHECK(br_stock_transceive_checked(&reader,12,tx,2,rx,&bits,0x0800e187)==1);
    CHECK(denied==1 && state.quarantined && f.sr_reads==0);
    reset_bridge(1000);lookup_fail=1;
    CHECK(br_stock_read(&reader,0x12)==0xff && state.quarantined && !f.sr_reads);
    br_stock_write(&reader,0x12,1);CHECK(!f.dr_writes);
    struct br_stock_context ctx={0,&reader};
    reset_bridge(1000);select_fault=1;memset(rx,0xa5,sizeof(rx));
    CHECK(br_stock_select(&ctx,rx,1)==1 && state.quarantined && rx[0]==0xa5 && original_calls==1);
    unsigned calls=original_calls,reads=f.sr_reads;
    CHECK(br_stock_select(&ctx,rx,1)==1 && original_calls==calls && f.sr_reads==reads);
    CHECK(br_stock_transceive_sized(&reader,12,tx,2,rx,16,&bits,128)==1 && f.sr_reads==reads);
    /* No budget refill on an ABI call boundary. */
    reset_bridge(6);CHECK(br_stock_read(&reader,0x12)==0x42 && !txn.polls_left);
    br_stock_write(&reader,0x12,0x44);CHECK(state.quarantined && f.sr_reads==6);
    reset_bridge(1000);select_fault=2;memset(rx,0xa5,sizeof(rx));
    CHECK(br_stock_select(&ctx,rx,1)==1 && rx[0]==0xa5);
    reset_bridge(1000);f.commit_cancel=1;memset(rx,0xa5,sizeof(rx));
    CHECK(br_stock_select(&ctx,rx,1)==1 && state.quarantined && rx[0]==0xa5);
    reset_bridge(1000);memset(rx,0xa5,sizeof(rx));
    CHECK(br_stock_select(&ctx,rx,1)==0 && rx[0]==0 && rx[21]==21 && rx[22]==0xa5);
    printf("PASS %u total core+bridge assertions; stock originals are explicit host doubles, no firmware hooks installed\n",assertions);
}
