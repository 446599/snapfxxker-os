#include "stock_bounded_bridge.h"
#if defined(__arm__) || defined(__thumb__)
_Static_assert(offsetof(struct br_stock_context,reader)==4,"stock ctx reader offset");
#endif
static struct br_transaction *transaction(const struct br_reader *reader) {
    struct br_transaction *t=br_platform_transaction(reader);
    if(!t || !t->state || t->reader!=reader || !t->stop_epoch) {
        br_platform_denied(reader,BR_INVALID); return 0;
    }
    return t;
}
uint8_t br_stock_read(const struct br_reader *r,uint8_t reg) {
    struct br_transaction *t=transaction(r); uint8_t out=0xff;
    if(!t) return out;
    enum br_result e=br_read(t,reg,&out);
    if(e!=BR_OK) br_platform_denied(r,e);
    return out;
}
void br_stock_write(const struct br_reader *r,uint8_t reg,uint8_t val) {
    struct br_transaction *t=transaction(r);
    if(!t) return;
    enum br_result e=br_write(t,reg,val);
    if(e!=BR_OK) br_platform_denied(r,e);
}
int br_stock_halt(struct br_stock_context *ctx) {
    if(!ctx) {br_platform_denied(0,BR_INVALID);return 1;}
    struct br_transaction *t=transaction(ctx->reader);
    if(!t) return 1;
    enum br_result e=br_halt_checked(t);
    if(e!=BR_OK) br_platform_denied(ctx->reader,e);
    return e==BR_OK ? 0 : 1;
}
int br_stock_select(struct br_stock_context *ctx,uint8_t *out,int mode) {
    if(!ctx || !out) return 1;
    struct br_transaction *t=transaction(ctx->reader);
    if(!t || t->state->quarantined) return 1;
    uint8_t staged[22]={0};
    int result=br_stock_select_original(ctx,staged,mode);
    /* Never manufacture a successful UID after a void write or byte-read fault.
     * Do not copy partial collision/selection output on any nonzero return. */
    if(result || t->state->quarantined) return 1;
    return br_publish(t,out,staged,sizeof(staged),0,0)==BR_OK ? 0 : 1;
}
int br_stock_transceive_sized(const struct br_reader *r,uint32_t command,
    const uint8_t *tx,uint32_t n,uint8_t *rx,uint32_t cap,uint32_t *bits,
    uint32_t expected) {
    struct br_transaction *t=transaction(r);
    if(!t) return 1;
    if(!rx || !bits || command!=12) { br_platform_denied(r,BR_INVALID); return 1; }
    enum br_result e=br_transceive_exact(t,tx,n,rx,cap,bits,expected);
    if(e!=BR_OK) br_platform_denied(r,e);
    return e==BR_OK ? 0 : 1;
}
int br_stock_transceive_checked(const struct br_reader *r,uint32_t command,
    const uint8_t *tx,uint32_t n,uint8_t *rx,uint32_t *bits,uintptr_t pc) {
    uint32_t cap,expected;
    /* Return PC is captured by the assembly thunk, never taken from transport.
     * Proven call sites in exact hash-checked V1.1.31; appended callers must use
     * sized API. Exact semantic lengths also reject malformed shorter replies.
     * Capacity is a destination bound, not the unchecked RC522 FIFO count. */
    switch(pc & ~(uintptr_t)1) {
    case 0x0800e186: {
        /* Only original E108's exact HLTA request, not a general raw Transmit
         * capability. Its output pointers are unused and are not published. */
        struct br_transaction *t=transaction(r);
        if(!t) return 1;
        if(command!=4 || !tx || n!=2 || tx[0]!=0x50 || tx[1]!=0) {
            br_platform_denied(r,BR_INVALID);return 1;
        }
        enum br_result e=br_halt_checked(t);
        if(e!=BR_OK) br_platform_denied(r,e);
        return e==BR_OK ? 0 : 1;
    }
    case 0x0800dc40: cap=1; expected=8; break;   /* SAK */
    case 0x0800dd04: cap=5; expected=40; break;  /* UID + BCC */
    case 0x0800e00c: cap=2; expected=16; break;  /* ATQA */
    case 0x0800e216: cap=16; expected=128; break;/* page read */
    default:
        /* Unknown callers never inherit a capacity or a Transmit capability. */
        br_platform_denied(r,BR_INVALID); return 1;
    }
    return br_stock_transceive_sized(r,command,tx,n,rx,cap,bits,expected);
}
