/* Executes the exact linked integration C sources on the HOST. MMIO and IRQ
 * instructions are explicit test doubles; no CPU/RTOS/physical proof is made. */
#include "integration_peripherals.h"
#include <stdlib.h>
#include "event_mock.h"
#define ex_state (ex_storage_current()->ex)
#define rf_device_state (ex_storage_current()->rf)
#define CHECK(x) do {assert(x); ++assertions;} while(0)
static unsigned mask, stock_calls, mutate_commit;
int decode_native_tag(uint8_t *,const uint8_t *,int,void *);
int decode_cmd68_tag(uint8_t *,const uint8_t *,int);
int decode_cmd68_uid_tag(uint8_t *,const uint8_t *,int);
uint32_t rf_platform_enter(void) {
    unsigned prior=mask; mask=1;
    if(mutate_commit && f.sr_reads==mutate_commit) {
        ++rf_device_state.generation; mutate_commit=0;
    }
    return prior;
}
void rf_platform_leave(uint32_t prior) { assert(mask==1); mask=prior; }
uint32_t br_platform_enter(void) { return rf_platform_enter(); }
void br_platform_leave(uint32_t prior) { rf_platform_leave(prior); }
void experiment_reset_gate(void) { abort(); }
/* A supposedly unreachable stock body aborts rather than manufacturing success. */
void rf_background_original(uint32_t n) { (void)n; ++stock_calls; abort(); }
int rf_cmd68_original(uint32_t n,const uint32_t *p,uint8_t *r) {
    (void)n;(void)p;(void)r;++stock_calls;abort();
}
int rf_cmd13_original(uint32_t n,const uint32_t *p,uint8_t *r) {
    (void)n;(void)p;(void)r;++stock_calls;abort();
}
int rf_cmd14_original(uint32_t n,const uint32_t *p,uint8_t *r) {
    (void)n;(void)p;(void)r;++stock_calls;abort();
}
void rf_requeue_original(uint32_t n) { (void)n;++stock_calls;abort(); }
int br_stock_select_original(struct br_stock_context *c,uint8_t *r,int n) {
    (void)c;(void)r;(void)n;++stock_calls;abort();
}
static void reset(unsigned budget) {
    /* Only test fixtures erase persistent state. There is no recovery function
     * in the image and ex_offline_begin itself never clears these objects. */
    memset(&f,0,sizeof(f));memset(&reader,0,sizeof(reader));
    mock_storage_reset();
    mock_handle=ex_event_factory();CHECK(mock_handle!=0);
    mask=stock_calls=mutate_commit=0;
    f.cr1=0x44;f.sr=2;f.cs_odr=0x1000;f.aux_odr=0x2000;f.rx_count=16;f.irq=0x30;
    reader.spi=SPI;reader.cs_port=CS;reader.cs_mask=0x1000;reader.aux_port=AUX;reader.aux_mask=0x2000;
    for(unsigned i=0;i<64;i++) f.rx[i]=(uint8_t)(i^0x5a);
    f.registers[0x12]=0x42;
    if(budget) CHECK(ex_offline_begin(&reader,budget)==0);
}
static void fault_check(void) {
    CHECK(ex_state.stop_requested==1 && ex_state.stop_epoch==1);
    CHECK(ex_state.transport.quarantined==1 && rf_device_state.phase==RF_UNKNOWN);
    CHECK(rf_device_state.revoked==1 && !stock_calls && !mask);
    unsigned before=f.sr_reads;
    CHECK(br_stock_read(&reader,0x12)==0xff);
    br_stock_write(&reader,0x12,0x22);
    CHECK(f.sr_reads==before && ex_state.stop_epoch==1);
    CHECK(ex_offline_begin(&reader,4000)==1);
}
static int trans(uint8_t *rx,uint32_t *bits) {
    const uint8_t tx[]={0x30,4};
    return br_stock_transceive_checked(&reader,12,tx,2,rx,bits,0x0800e217);
}
int main(void) {
    uint8_t rx[66],tx[2]={0x30,4};uint32_t bits;
    reset(0); CHECK(br_stock_read(&reader,0x12)==0xff && !f.sr_reads); fault_check();
    reset(6); CHECK(br_stock_read(&reader,0x12)==0x42);
    CHECK(ex_state.transaction.polls_left==0 && f.sr_reads==6);
    br_stock_write(&reader,0x12,0x44); fault_check();
    reset(1000);struct br_reader other=reader;
    CHECK(br_stock_read(&other,0x12)==0xff && !f.sr_reads); fault_check();
    reset(1000);bits=0xdeadbeef;memset(rx,0xa5,sizeof(rx));
    CHECK(br_stock_transceive_sized(&reader,12,tx,0,rx,16,&bits,128)==1);
    CHECK(!f.sr_reads && bits==0xdeadbeef && rx[0]==0xa5);fault_check();
    reset(1000);bits=0xdeadbeef;
    CHECK(br_stock_transceive_checked(&reader,4,tx,2,rx,&bits,0x0800e187)==1);
    CHECK(!f.sr_reads && bits==0xdeadbeef);fault_check();
    reset(4000);bits=0xdeadbeef;memset(rx,0xa5,sizeof(rx));
    CHECK(trans(rx+1,&bits)==0 && bits==128 && !memcmp(rx+1,f.rx,16));
    CHECK(rx[0]==0xa5 && rx[17]==0xa5 && !ex_state.stop_requested);
    unsigned total=f.sr_reads;
    CHECK(ex_finalize()==1);fault_check(); /* no whole-operation success */
    for(unsigned p=1;p<=total;p++) {
        reset(4000);bits=0xdeadbeef;memset(rx,0xa5,sizeof(rx));f.fail_at=p;f.error_mask=0x40;
        CHECK(trans(rx+1,&bits)==1 && bits==0xdeadbeef);
        for(unsigned i=0;i<sizeof(rx);i++) CHECK(rx[i]==0xa5);
        fault_check();
        reset(4000);bits=0xdeadbeef;memset(rx,0xa5,sizeof(rx));f.cancel_at=p;
        CHECK(trans(rx+1,&bits)==1 && bits==0xdeadbeef);
        for(unsigned i=0;i<sizeof(rx);i++) CHECK(rx[i]==0xa5);
        fault_check();
    }
    for(unsigned b=1;b<total;b++) {
        reset(b);bits=0xdeadbeef;memset(rx,0xa5,sizeof(rx));
        CHECK(trans(rx+1,&bits)==1 && bits==0xdeadbeef && f.sr_reads==b);
        CHECK(rx[1]==0xa5);fault_check();
    }
    reset(4000);bits=0xdeadbeef;memset(rx,0xa5,sizeof(rx));f.irq=0;
    CHECK(trans(rx+1,&bits)==1 && bits==0xdeadbeef && f.sr_reads==4000);fault_check();
    reset(4000);bits=0xdeadbeef;f.irq=1;CHECK(trans(rx,&bits)==1);fault_check();
    for(unsigned bit=1;bit<=128;bit<<=1) {
        reset(4000);bits=0xdeadbeef;f.reader_error=bit;CHECK(trans(rx,&bits)==1);fault_check();
    }
    for(unsigned n=0;n<256;n++) {
        reset(4000);f.rx_count=n;bits=0xdeadbeef;memset(rx,0xa5,sizeof(rx));
        CHECK(trans(rx+1,&bits)==(n==16?0:1));
        CHECK(rx[0]==0xa5 && rx[17]==0xa5);
        if(n!=16) {CHECK(bits==0xdeadbeef && rx[1]==0xa5);fault_check();}
    }
    reset(4000);bits=0xdeadbeef;memset(rx,0xa5,sizeof(rx));mutate_commit=total;
    CHECK(trans(rx+1,&bits)==1 && bits==0xdeadbeef && rx[1]==0xa5);fault_check();
    for(unsigned mutation=0;mutation<6;mutation++) {
        reset(4000);
        uint32_t *fields[]={&rf_device_state.boot,&rf_device_state.generation,
            &rf_device_state.connection,&rf_device_state.session,&rf_device_state.sequence,
            &ex_state.stop_epoch};
        ++*fields[mutation];CHECK(br_stock_read(&reader,0x12)==0xff && !f.sr_reads);
        CHECK(ex_state.stop_requested && ex_state.transport.quarantined);
    }
    for(unsigned n=0;n<256;n++) {
        uint8_t response[146];uint32_t request=n<4?n:(0x80000000u|(n<<16));
        reset(0);memset(response,0xa5,sizeof(response));
        CHECK(rf_cmd68_entry(68,&request,response+1)==1);
        CHECK(response[0]==0xa5 && response[145]==0xa5);
        CHECK(response[141]==1 && response[1]==(uint8_t)request);
        for(unsigned j=5;j<141;j++) CHECK(response[j]==0);
        fault_check();
    }
    reset(0);uint8_t response[144];uint32_t request=2;
    CHECK(rf_cmd13_entry(13,&request,response)==1 && response[140]==1);fault_check();
    reset(0);CHECK(rf_cmd14_entry(14,&request,response)==1 && response[0]==1);fault_check();
    reset(0);rf_background_entry(0);fault_check();
    reset(0);rf_requeue_entry(0);fault_check();
    reset(4000);memset(rx,0xa5,sizeof(rx));
    CHECK(ex_reject_select(0,rx,1)==1 && rx[0]==0xa5 && !f.sr_reads);fault_check();
    reset(4000);CHECK(ex_reject_halt(0)==1 && !f.sr_reads);fault_check();
    reset(4000);CHECK(ex_decoder_ready()==0 && !f.sr_reads);fault_check();
    uint8_t decoded[164];
    for(unsigned which=0;which<3;which++) {
        reset(4000);memset(decoded,0xa5,sizeof(decoded));
        int result=which==0 ? decode_native_tag(decoded,rx,16,0) :
            which==1 ? decode_cmd68_tag(decoded,rx,16) : decode_cmd68_uid_tag(decoded,rx,0);
        CHECK(result==-2 && !f.sr_reads);
        for(unsigned i=0;i<sizeof(decoded);i++) CHECK(decoded[i]==0xa5);
        fault_check();
    }
    /* The actual RF binding obtains its state through the validated handle.
     * A real fallback32 or missing/corrupt handle cannot use hidden globals. */
    reset(0);mock_storage_reset();mock_expansion_fail=1;
    mock_handle=ex_event_factory();CHECK(mock_handle!=0);
    CHECK(ex_storage_current()==0 && rf_platform_state()==0);
    CHECK(br_stock_read(&reader,0x12)==0xff && !f.sr_reads);
    CHECK(ex_offline_begin(&reader,1000)==1 && ex_finalize()==1);
    CHECK(rf_cmd68_entry(68,&request,response)==1 && response[140]==1);
    CHECK(!f.sr_reads && !stock_calls && !mock_tail_reads);
    mock_handle=0;CHECK(br_stock_read(&reader,0x12)==0xff && !f.sr_reads);
    printf("PASS %u integration assertions; %u SPI polls per successful fixture transfer. "
           "Exact integration C, MMIO/IRQ doubles only. No physical STOP or quiescence proof.\n",assertions,total);
    return 0;
}
