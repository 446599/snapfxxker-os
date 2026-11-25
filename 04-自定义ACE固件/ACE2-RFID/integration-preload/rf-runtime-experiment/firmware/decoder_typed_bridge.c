#include "decoder_typed_bridge.h"
#include "stock_bounded_bridge.h"
/* Bounded digital abort, NOT verified whole-operation/analog quiescence.
 * No fresh budget, release, flag clearing or recovery on transport failure. */
static enum br_result abort_registers(struct br_transaction *t) {
    enum br_result e;uint8_t v;
#define W(reg,val) do {e=br_write(t,(reg),(val));if(e!=BR_OK)return e;} while(0)
#define R(reg) do {e=br_read(t,(reg),&v);if(e!=BR_OK)return e;} while(0)
    W(1,0);R(8);W(8,v&0xf7);W(0x0d,0);W(0x0a,0x80);W(4,0x7f);
    R(1);if(v&0x0f)return BR_PROTOCOL;
    R(8);if(v&8)return BR_PROTOCOL;
    R(0x0d);if(v&0x80)return BR_PROTOCOL;
    R(0x0a);if(v&0x7f)return BR_PROTOCOL;
    R(4);if(v&0x7f)return BR_PROTOCOL;
    /* Does not restore baseline CRC/IRQ/timer/antenna settings. */
    return BR_OK;
#undef W
#undef R
}
struct ar_result br_decoder_probe(const struct br_reader *reader,
    const struct ar_selection *selection,const uint8_t key[6]) {
    struct ar_result result={0};result.code=AR_INVALID;result.transport=BR_INVALID;
    struct br_transaction *t=br_platform_transaction(reader);
    if(!t || t->reader!=reader || !t->state || !t->stop_epoch) {
        br_platform_denied(reader,BR_INVALID);return result;
    }
    result=ar_auth_checked(t,selection,0x60,4,key,35);
    if(result.code==AR_AUTH_STATUS_ACCEPTED) {
        uint8_t blocks[4][16];
        for(unsigned i=0;i<4;i++) {
            result=ar_read_checked(t,(uint8_t)(4+(i&1)),blocks[i]);
            if(result.code!=AR_BLOCK_READ) break;
        }
        if(result.code==AR_BLOCK_READ) {
            for(unsigned i=0;i<32;i++) if(((uint8_t *)blocks)[i]!=((uint8_t *)blocks)[i+32]) {
                result.code=AR_READ_INVALID;break;
            }
        }
    }
    enum br_result cleanup=abort_registers(t);
    if(cleanup!=BR_OK) {
        result.code=cleanup==BR_CANCELLED ? AR_CANCELLED :
            cleanup==BR_TIMEOUT ? AR_TRANSPORT_TIMEOUT : AR_TRANSPORT_UNKNOWN;
        result.transport=cleanup;
    }
    /* All outcomes still lack whole-operation cleanup/identity proof. Record
     * operation refusal without inventing wrong-key AUTH_NEGATIVE or handing
     * the observation to the preload sticky-failure source. */
    br_platform_denied(reader,result.transport==BR_OK ? BR_PROTOCOL : result.transport);
    return result;
}
