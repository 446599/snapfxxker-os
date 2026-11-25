#include "experimental_binding.h"

/* Both readers share ONE immutable transaction identity and ONE fault latch.
 * Mismatched reader/owner/epoch never receives an independent poll budget. */
static int valid_locked(const struct br_transaction *t) {
    struct ex_storage *b=ex_storage_current();
    if(!b) return 0;
    const struct rf_state *s=&b->rf;
    const struct rf_operation *op=&b->ex.operation;
    return b->ex.initialized==0x45585031u && !b->ex.stop_requested &&
        !b->ex.transport.quarantined && t==&b->ex.transaction &&
        t->state==&b->ex.transport && t->reader &&
        t->stop_epoch==&b->ex.stop_epoch &&
        t->expected_epoch==b->ex.stop_epoch &&
        s->phase==RF_ACTIVE && s->owner==RF_HOST && !s->revoked &&
        s->in_flight && s->boot==op->ticket.boot &&
        s->generation==op->ticket.generation &&
        s->connection==op->ticket.connection && s->session==op->ticket.session &&
        s->sequence==op->sequence;
}

void br_platform_denied(const struct br_reader *r, enum br_result result) {
    (void)r;
    uint32_t key=rf_platform_enter();
    struct ex_storage *b=ex_storage_current();
    if(!b) { rf_platform_leave(key); return; }
    if(!b->ex.stop_requested) {
        b->ex.stop_requested=1;
        if(b->ex.stop_epoch!=UINT32_MAX) ++b->ex.stop_epoch;
        b->ex.last_result=(uint32_t)result;
    }
    if(!b->ex.transport.quarantined) b->ex.transport.cause=(uint32_t)result;
    b->ex.transport.quarantined=1;
    b->rf.phase=RF_UNKNOWN;
    b->rf.revoked=1;
    /* Metadata only. This is NOT a physical motor/heater STOP or RF cleanup.
     * Unknown ownership is retained, and no recovery reports success. */
    rf_platform_leave(key);
}
struct br_transaction *br_platform_transaction(const struct br_reader *r) {
    uint32_t key=rf_platform_enter();
    struct ex_storage *b=ex_storage_current();
    int ok=b && valid_locked(&b->ex.transaction) && r==b->ex.transaction.reader;
    rf_platform_leave(key);
    return ok ? &b->ex.transaction : 0;
}
int br_platform_commit_valid(const struct br_transaction *t) {
    return valid_locked(t); /* br_publish already holds the metadata lock. */
}
void ex_stop_request(void) { br_platform_denied(0,BR_CANCELLED); }
int ex_offline_begin(const struct br_reader *r,uint32_t polls) {
    if(!r || !polls || polls>1000000u) return 1;
    uint32_t key=rf_platform_enter();
    struct ex_storage *b=ex_storage_current();
    if(!b || b->ex.initialized || b->ex.stop_requested || b->ex.transport.quarantined) {
        rf_platform_leave(key); return 1;
    }
    b->ex.initialized=0x45585031u;
    rf_init_exclusive(&b->rf,0x45585031u);
    struct rf_ticket t;
    int result=rf_acquire(&b->rf,RF_HOST,0,1,1,0,100,100,&t);
    if(result==RF_OK) result=rf_begin_operation(&b->rf,&t,0,&b->ex.operation);
    b->ex.transaction.state=&b->ex.transport;
    b->ex.transaction.reader=r;
    b->ex.transaction.polls_left=polls;
    b->ex.transaction.stop_epoch=&b->ex.stop_epoch;
    b->ex.transaction.expected_epoch=b->ex.stop_epoch;
    rf_platform_leave(key);
    if(result!=RF_OK) { br_platform_denied(r,BR_INVALID); return 1; }
    return 0;
}

/* Missing contracts are resolved as explicit FAILURE, never success stubs. The
 * stock adapters therefore reject before invoking stock wait/queue routines. */
uint32_t rf_platform_now(void) {
    struct ex_storage *b=ex_storage_current(); return b ? b->ex.offline_now : 0;
}
int rf_platform_ready(void) { return 0; }
uint32_t rf_platform_budget(uint32_t owner) { (void)owner; return 0; }
enum rf_proof rf_platform_quiescence(const struct rf_operation *op) {
    (void)op; return RF_UNVERIFIED;
}
int rf_platform_cleanup(const struct rf_cleanup *c) { (void)c; return 0; }
void rf_platform_bad_outcome(uint32_t owner) {
    (void)owner; br_platform_denied(0,BR_QUARANTINED);
}
int ex_finalize(void) {
    struct ex_storage *b=ex_storage_current();
    if(!b) return 1;
    /* Even successful register traffic cannot release ownership, publish whole
     * operation success or make a later operation eligible without proof. */
    (void)rf_complete_operation(&b->rf,&b->ex.operation,
        rf_platform_now(),rf_platform_quiescence(&b->ex.operation));
    rf_platform_bad_outcome(RF_HOST);
    return 1;
}
int ex_decoder_ready(void) { rf_platform_bad_outcome(RF_CACHE); return 0; }
int ex_reject_select(struct br_stock_context *ctx,uint8_t *out,int mode) {
    (void)ctx; (void)out; (void)mode;
    br_platform_denied(0,BR_INVALID);
    return 1; /* Do not enter stock 13C70's unbounded nested-state assertion. */
}
int ex_reject_halt(void *ctx) {
    (void)ctx; br_platform_denied(0,BR_INVALID); return 1;
}
void ex_reject_worker(void) {
    rf_platform_bad_outcome(RF_BACKGROUND);
    experiment_reset_gate(); /* No stock startup GPIO/cache access. Never returns. */
}
