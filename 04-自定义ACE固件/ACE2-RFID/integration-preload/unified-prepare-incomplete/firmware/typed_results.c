#include "typed_results.h"

static struct ar_result fresh(enum ar_stage stage) {
    struct ar_result r;
    volatile uint8_t *bytes=(volatile uint8_t *)&r;
    for(unsigned i=0;i<sizeof(r);++i) bytes[i]=0;
    r.stage=stage; r.code=AR_INVALID;
    r.transport=BR_INVALID; r.select_result=-1; return r;
}
static enum ar_code classify(enum br_result e) {
    switch(e) {
    case BR_TIMEOUT: return AR_TRANSPORT_TIMEOUT;
    case BR_CANCELLED: return AR_CANCELLED;
    case BR_NO_REPLY: return AR_NO_REPLY;
    case BR_READER_ERROR: return AR_READER_ERROR;
    case BR_CAPACITY: case BR_PROTOCOL: return AR_READ_INVALID;
    case BR_INVALID: return AR_INVALID;
    default: return AR_TRANSPORT_UNKNOWN;
    }
}
static int failed(struct ar_result *r,enum br_result e) {
    if(e==BR_OK) return 0;
    r->transport=e; r->transport_status_valid=1; r->code=classify(e); return 1;
}
static enum br_result current(struct br_transaction *t) {
    if(!t || !t->state || !t->reader || !t->stop_epoch) return BR_INVALID;
    uint32_t k=br_platform_enter();
    enum br_result e=BR_OK;
    if(t->state->quarantined) e=BR_QUARANTINED;
    else if(*t->stop_epoch!=t->expected_epoch || !br_platform_commit_valid(t)) e=BR_CANCELLED;
    br_platform_leave(k); return e;
}
static enum br_result read_reg(struct br_transaction *t,uint8_t reg,uint8_t *out) {
    enum br_result e=current(t); return e==BR_OK ? br_read(t,reg,out) : e;
}
static enum br_result write_reg(struct br_transaction *t,uint8_t reg,uint8_t value) {
    enum br_result e=current(t); return e==BR_OK ? br_write(t,reg,value) : e;
}

struct ar_result ar_auth_checked(struct br_transaction *t,const struct ar_selection *s,
    uint8_t mode,uint8_t block,const uint8_t key[6],uint32_t max_polls) {
    struct ar_result r=fresh(AR_STAGE_SELECT);
    if(!s || !key || (mode!=0x60 && mode!=0x61) || !max_polls || max_polls>4096) return r;
    r.select_result=s->raw_result;
    if(s->raw_result!=0) {r.code=AR_SELECT_NOT_OK;return r;}
    for(unsigned i=0;i<5;++i) if(s->expected_uid[i]!=s->selected_uid[i]) {
        r.code=AR_UID_MISMATCH; return r;
    }
    const uint8_t *uid=s->selected_uid;
    if(uid[0]==0x88 || !(uid[0]|uid[1]|uid[2]|uid[3]) ||
       (uint8_t)(uid[0]^uid[1]^uid[2]^uid[3])!=uid[4]) return r;
    if(failed(&r,current(t))) return r;
    r.stage=AR_STAGE_SETUP;
    uint8_t s2=0xff,irq=0xff,error=0xff;
#define WRITE(reg,value) do {if(failed(&r,write_reg(t,(reg),(value)))) return r;} while(0)
#define READ(reg,out) do {if(failed(&r,read_reg(t,(reg),&(out)))) return r;} while(0)
    WRITE(0x01,0); READ(0x08,s2);
    if(s2==0xff) {r.transport=BR_OK;r.transport_status_valid=1;r.code=AR_INVALID_SNAPSHOT;return r;}
    WRITE(0x08,(uint8_t)(s2&~0x08u));
    WRITE(0x04,0x7f); WRITE(0x0a,0x80);
    WRITE(0x12,0); WRITE(0x13,0); WRITE(0x0d,0);
    READ(0x08,s2); READ(0x04,irq);
    /* Do not accept stale Crypto1On or completion flags from a prior command. */
    if(s2!=0 || irq!=0) {
        r.transport=BR_OK;r.transport_status_valid=1;r.code=AR_INVALID_SNAPSHOT;return r;
    }
    WRITE(0x09,mode); WRITE(0x09,block);
    for(unsigned i=0;i<6;++i) WRITE(0x09,key[i]);
    for(unsigned i=0;i<4;++i) WRITE(0x09,uid[i]);
    r.stage=AR_STAGE_AUTH;
    r.auth_command_attempted=1;
    WRITE(0x01,0x0e); r.auth_command_issued=1;
    for(uint32_t i=0;i<max_polls;++i) {
        r.status_polls=i+1; r.snapshot_valid=0;
        READ(0x08,s2); READ(0x04,irq); READ(0x06,error);
        if(failed(&r,current(t))) return r;
        r.status2=s2;r.irq=irq;r.error=error;r.snapshot_valid=1;r.transport=BR_OK;r.transport_status_valid=1;
        /* Deliberately narrow offline observations, not a complete silicon
         * state decoder: unrecognized combinations are UNKNOWN, never success. */
        if((s2!=0 && s2!=8) || (irq&~0x11u)) {r.code=AR_INVALID_SNAPSHOT;return r;}
        /* Timer/no-reply and ErrorReg are never an AUTH_NEGATIVE observation. */
        if(irq&1u) {r.code=AR_NO_REPLY;return r;}
        if(error) {r.code=AR_READER_ERROR;return r;}
        if(irq&0x10u) {
            r.code=(s2&8u) ? AR_AUTH_STATUS_ACCEPTED : AR_AUTH_NOT_ESTABLISHED;
            return r;
        }
    }
    r.transport=BR_OK;r.transport_status_valid=1;r.code=AR_AUTH_POLL_LIMIT;return r;
#undef READ
#undef WRITE
}

struct ar_result ar_read_checked(struct br_transaction *t,uint8_t block,uint8_t out[16]) {
    struct ar_result r=fresh(AR_STAGE_READ);
    if(!out || failed(&r,current(t))) return r;
    uint8_t s2=0xff,tx_mode=0xff,rx_mode=0xff,error=0xff,staged[16];
    uint32_t bits=0;const uint8_t command[2]={0x30,block};
    if(failed(&r,read_reg(t,0x08,&s2))) return r;
    if(s2!=0 && s2!=8) {r.transport=BR_OK;r.transport_status_valid=1;r.code=AR_INVALID_SNAPSHOT;return r;}
    if(!s2) {r.transport=BR_OK;r.transport_status_valid=1;r.code=AR_AUTH_NOT_ESTABLISHED;return r;}
    if(failed(&r,read_reg(t,0x12,&tx_mode)) || failed(&r,read_reg(t,0x13,&rx_mode))) return r;
    if(tx_mode==0xff || rx_mode==0xff) {
        r.transport=BR_OK;r.transport_status_valid=1;r.code=AR_INVALID_SNAPSHOT;return r;
    }
    if(failed(&r,write_reg(t,0x12,(uint8_t)(tx_mode|0x80u))) ||
       failed(&r,write_reg(t,0x13,(uint8_t)(rx_mode|0x80u)))) return r;
    if(failed(&r,current(t))) return r;
    if(failed(&r,br_transceive_exact(t,command,2,staged,16,&bits,128))) return r;
    if(bits!=128) {r.transport=BR_OK;r.transport_status_valid=1;r.code=AR_READ_INVALID;return r;}
    if(failed(&r,read_reg(t,0x06,&error))) return r;
    r.error=error;
    if(error) {r.transport=BR_OK;r.transport_status_valid=1;r.code=AR_READER_ERROR;return r;}
    if(failed(&r,br_publish(t,out,staged,16,0,0))) return r;
    r.transport=BR_OK;r.transport_status_valid=1;r.code=AR_BLOCK_READ;return r;
}
