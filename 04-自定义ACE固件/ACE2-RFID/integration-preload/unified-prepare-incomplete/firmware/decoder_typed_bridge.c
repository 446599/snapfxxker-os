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
struct br_decoder_report br_decoder_probe(const struct br_reader *reader,
    const struct ar_selection *selection,const uint8_t key[6]) {
    struct br_decoder_report report={0};report.cleanup=BR_INVALID;
    struct ar_result result={0};result.code=AR_INVALID;result.transport=BR_INVALID;
    result.select_result=selection ? selection->raw_result : -1;
    struct br_transaction *t=br_platform_transaction(reader);
    if(!t || t->reader!=reader || !t->state || !t->stop_epoch) {
        br_platform_denied(reader,BR_INVALID);report.primary=result;return report;
    }
    result=ar_auth_checked(t,selection,0x60,4,key,35);
    if(result.code==AR_AUTH_STATUS_ACCEPTED) {
        uint8_t blocks[4][16];
        for(unsigned i=0;i<4;i++) {
            struct ar_result read=ar_read_checked(t,(uint8_t)(4+(i&1)),blocks[i]);
            /* Preserve the original SELECT/AUTH history, including the AUTH
             * snapshot, while advancing only the current operation outcome.
             * ar_read_checked's fresh record must never erase that history. */
            result.stage=read.stage;result.code=read.code;
            result.transport=read.transport;
            result.transport_status_valid=read.transport_status_valid;
            if(result.code!=AR_BLOCK_READ) break;
        }
        if(result.code==AR_BLOCK_READ) {
            for(unsigned i=0;i<32;i++) if(((uint8_t *)blocks)[i]!=((uint8_t *)blocks)[i+32]) {
                result.code=AR_READ_INVALID;break;
            }
        }
    }
    report.primary=result;
    report.cleanup=abort_registers(t);
    report.cleanup_status_valid=1; /* A real typed abort result was returned. */
    /* All outcomes still lack whole-operation cleanup/identity proof. Record
     * operation refusal without inventing wrong-key AUTH_NEGATIVE or handing
     * the observation to the preload sticky-failure source. */
    enum br_result primary=result.transport_status_valid && result.transport!=BR_OK ? result.transport : BR_OK;
    br_platform_denied(reader,primary!=BR_OK ? primary :
        report.cleanup!=BR_OK ? report.cleanup : BR_PROTOCOL);
    return report;
}
