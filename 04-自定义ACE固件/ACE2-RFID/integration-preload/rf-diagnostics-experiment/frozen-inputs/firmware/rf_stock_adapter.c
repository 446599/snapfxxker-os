#include "rf_stock_adapter.h"
#ifndef ACE_EXPERIMENTAL_OFFLINE
static struct rf_state *rf_platform_state(void) { return &rf_device_state; }
#endif
static int cleanup_ticket(const struct rf_ticket *t) {
    struct rf_state *state=rf_platform_state();
    if(!state) return 0;
    struct rf_cleanup c;
    int success=0;
    for(uint32_t i=0;i<RF_CLEAN_COUNT;++i) {
        if(rf_claim_cleanup_for(state,t,&c)!=RF_OK) return 0;
        int verified=rf_platform_cleanup(&c);
        if(rf_complete_cleanup(state,&c,rf_platform_now(),verified,&success)!=RF_OK) return 0;
    }
    return success;
}
/* A nonblocking acquisition. There is no RF mutex held across stock work;
 * logical ownership alone remains. Stock blocking calls remain deployment blockers. */
static int begin(uint32_t owner,uint32_t slot,struct rf_operation *op) {
    struct rf_state *state=rf_platform_state();
    struct rf_ticket t;
    if(!state || !rf_platform_ready()) { rf_platform_bad_outcome(owner); return RF_FAULT; }
    uint32_t budget=rf_platform_budget(owner);
    int r=rf_acquire(state,owner,slot,0,0,rf_platform_now(),budget,budget,&t);
    if(r!=RF_OK) return r;
    r=rf_begin_operation(state,&t,rf_platform_now(),op);
    if(r!=RF_OK) {
        (void)rf_cancel(state,&t);
        /* Expiry may occur between acquisition and begin. No stock operation
         * ran, but ownership must still drain through proven cleanup. Ticketed
         * claims cannot touch a subsequent owner. Missing/failed proof retains
         * ownership; never force-release. Revocation still forbids success. */
        (void)cleanup_ticket(&t);
        rf_platform_bad_outcome(owner);
    }
    return r;
}
static int end(struct rf_operation *op) {
    struct rf_state *state=rf_platform_state();
    if(!state) return 0;
    enum rf_proof proof=rf_platform_quiescence(op);
    if(rf_complete_operation(state,op,rf_platform_now(),proof)!=RF_OK) return 0;
    /* Expiry/revocation can already have moved us to CLEANING. */
    (void)rf_finish(state,&op->ticket,rf_platform_now());
    return cleanup_ticket(&op->ticket);
}
static int reject_response(const uint32_t *request,uint8_t *response) {
    /* Stock CMD13/CMD68 response size and status offsets verified in disassembly.
     * Status=1 is the existing rejection code, not a newly advertised BUSY ABI. */
    for(unsigned i=0;i<144;++i) response[i]=0;
    const uint32_t index=*request;
    for(unsigned i=0;i<4;++i) response[i]=(uint8_t)(index>>(8*i));
    response[140]=1;
    return 1;
}
static int reject_control(uint8_t *response) {
    response[0]=1; response[1]=0; response[2]=0; response[3]=0;
    return 1;
}
void rf_background_entry(uint32_t index) {
    struct rf_operation op;
    if(index>1) { rf_platform_bad_outcome(RF_BACKGROUND); return; }
    if(begin(RF_BACKGROUND,index*2,&op)!=RF_OK) return;
    rf_background_original(index); /* Includes E6FC -> E108 tail return. */
    if(!end(&op)) rf_platform_bad_outcome(RF_BACKGROUND);
}
int rf_cmd68_entry(uint32_t command,const uint32_t *req,uint8_t *rsp) {
    struct rf_operation op;
    /* Old packed raw operations span frames without tickets. Do not claim a
     * per-CMD68 lock makes them safe. Intentionally reject ALL legacy extension
     * values; a future explicit session protocol must call rf_* with real tokens. */
    if(!req || !rsp) { rf_platform_bad_outcome(RF_CMD68); return 1; }
    if(*req>3) { rf_platform_bad_outcome(RF_CMD68); return reject_response(req,rsp); }
    if(begin(RF_CMD68,*req,&op)!=RF_OK) return reject_response(req,rsp);
    int result=rf_cmd68_original(command,req,rsp);
    if(!end(&op)) { rf_platform_bad_outcome(RF_CMD68); return reject_response(req,rsp); }
    return result;
}
int rf_cmd13_entry(uint32_t command,const uint32_t *req,uint8_t *rsp) {
    struct rf_operation op;
    if(!req || !rsp) { rf_platform_bad_outcome(RF_CACHE); return 1; }
    if(*req>3) { rf_platform_bad_outcome(RF_CACHE); return reject_response(req,rsp); }
    if(begin(RF_CACHE,*req,&op)!=RF_OK) return reject_response(req,rsp);
    int result=rf_cmd13_original(command,req,rsp);
    if(!end(&op)) { rf_platform_bad_outcome(RF_CACHE); return reject_response(req,rsp); }
    return result;
}
int rf_cmd14_entry(uint32_t command,const uint32_t *req,uint8_t *rsp) {
    struct rf_operation op;
    if(!req || !rsp) { rf_platform_bad_outcome(RF_CONTROL); return 1; }
    if(*req>3) { rf_platform_bad_outcome(RF_CONTROL); return reject_control(rsp); }
    if(begin(RF_CONTROL,*req,&op)!=RF_OK) return reject_control(rsp);
    int result=rf_cmd14_original(command,req,rsp);
    if(!end(&op)) { rf_platform_bad_outcome(RF_CONTROL); return reject_control(rsp); }
    return result;
}
void rf_requeue_entry(uint32_t slot) {
    struct rf_operation op;
    if(slot>3) { rf_platform_bad_outcome(RF_CONTROL); return; }
    if(begin(RF_CONTROL,slot,&op)!=RF_OK) return;
    rf_requeue_original(slot);
    if(!end(&op)) rf_platform_bad_outcome(RF_CONTROL);
}
