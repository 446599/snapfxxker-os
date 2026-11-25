#include "bounded_rc522.h"
#if defined(__arm__) || defined(__thumb__)
_Static_assert(offsetof(struct br_reader, cs_port)==4, "stock CS offset");
_Static_assert(offsetof(struct br_reader, aux_port)==12, "stock auxiliary offset");
_Static_assert(offsetof(struct br_reader, power_port)==44, "stock power offset");
_Static_assert(sizeof(struct br_reader)==52, "stock reader size");
#endif
#define SPI_CR1 0u
#define SPI_SR 8u
#define SPI_DR 12u
#define GPIO_ODR 12u
#define GPIO_SET 16u
#define GPIO_CLEAR 20u
#define SR_RXNE 1u
#define SR_TXE 2u
#define SR_ERRORS 0x70u /* CRCERR, MODF, OVR. Never fabricate a byte on these. */
#define SR_BSY 0x80u
#define CR1_REQUIRED 0x44u /* SPE + MSTR, preserves stock configuration. */
#ifdef BR_HOST_TEST
extern uint32_t br_test_load(uintptr_t address);
extern void br_test_store(uintptr_t address, uint32_t value);
extern void br_test_settle(void);
#define load(a) br_test_load(a)
#define store(a,v) br_test_store(a,v)
#define settle() br_test_settle()
#else
static uint32_t load(uintptr_t a) { return *(volatile uint32_t *)a; }
static void store(uintptr_t a, uint32_t v) { *(volatile uint32_t *)a=v; }
static void settle(void) {
    /* Mirrors stock 120-count setup delay, NOT a measured time guarantee. */
    for (volatile uint32_t i=120; i; --i) __asm__ volatile("nop");
}
#endif
static enum br_result validate(struct br_transaction *t) {
    if (!t || !t->state || !t->reader || !t->reader->spi ||
        !t->reader->cs_port || !t->reader->aux_port ||
        !t->reader->cs_mask || (t->reader->cs_mask & ~0xffffu) ||
        !t->reader->aux_mask || (t->reader->aux_mask & ~0xffffu)) return BR_INVALID;
    if (t->state->quarantined) return BR_QUARANTINED;
    /* Whole-operation ownership is checked before EACH register exchange, not
     * just final output publication. The lock covers metadata only; no MMIO,
     * polling or scheduling is performed while it is held. */
    uint32_t key=br_platform_enter();
    int active=br_platform_commit_valid(t);
    int stopped=t->stop_epoch && *t->stop_epoch!=t->expected_epoch;
    br_platform_leave(key);
    if (!active || stopped) {
        t->state->cause=BR_CANCELLED; t->state->quarantined=1;
        return BR_CANCELLED;
    }
    return BR_OK;
}
static enum br_result fault(struct br_transaction *t, enum br_result error) {
    /* Always persistent, even when CS restores: FIFO/crypto may be unknown. */
    if (!t->state->quarantined) t->state->cause=(uint32_t)error;
    t->state->quarantined=1;
    return error;
}
static enum br_result poll(struct br_transaction *t, uint32_t mask,
                           uint32_t expected, uint32_t forbidden) {
    while (t->polls_left) {
        enum br_result live=validate(t);
        if(live!=BR_OK) return live;
        --t->polls_left;
        if (t->stop_epoch && *t->stop_epoch != t->expected_epoch) return BR_CANCELLED;
        uint32_t sr=load(t->reader->spi+SPI_SR);
        live=validate(t);
        if(live!=BR_OK) return live;
        /* Observe cancellation that arrived during the MMIO read before
         * permitting the next DR write. An IRQ after this check can still
         * race an in-flight byte: no physical STOP atomicity is claimed. */
        if(t->state->quarantined) return BR_QUARANTINED;
        if(t->stop_epoch && *t->stop_epoch!=t->expected_epoch) return BR_CANCELLED;
        if (sr & (SR_ERRORS | forbidden)) return BR_TRANSPORT;
        if ((sr & mask)==expected) return BR_OK;
    }
    return BR_TIMEOUT;
}
static enum br_result restore_lines(const struct br_reader *r) {
    /* Reproduce BOTH stock exit writes, including auxiliary line. */
    store(r->aux_port+GPIO_CLEAR,r->aux_mask);
    store(r->cs_port+GPIO_SET,r->cs_mask);
    if ((load(r->cs_port+GPIO_ODR)&r->cs_mask)!=r->cs_mask ||
        (load(r->aux_port+GPIO_ODR)&r->aux_mask)) return BR_TRANSPORT;
    /* GPIO ODR proves only the output latch, not physical voltage or reader idle. */
    return BR_OK;
}
static enum br_result exchange(struct br_transaction *t, uint8_t reg,
    uint8_t value, uint8_t *out, int is_read) {
    enum br_result result=validate(t);
    uint8_t received=0;
    if (result!=BR_OK) return result;
    if (reg>0x3f || !out) return BR_INVALID;
#ifdef ACE_EXPERIMENTAL_OFFLINE
    if(!br_platform_exchange_claim(t)) return fault(t,BR_CANCELLED);
#endif
    const struct br_reader *r=t->reader;
    if ((load(r->spi+SPI_CR1)&CR1_REQUIRED)!=CR1_REQUIRED) {
        result=BR_TRANSPORT; goto cleanup;
    }
    /* No stale RX byte or active transfer may be mistaken for this response. */
    result=poll(t,SR_BSY,0,SR_RXNE); if (result!=BR_OK) goto cleanup;
    store(r->cs_port+GPIO_CLEAR,r->cs_mask);
    if (load(r->cs_port+GPIO_ODR)&r->cs_mask) { result=BR_TRANSPORT; goto cleanup; }
    settle();
    result=poll(t,SR_TXE,SR_TXE,SR_RXNE); if (result!=BR_OK) goto cleanup;
    store(r->spi+SPI_DR,((uint32_t)reg<<1)|(is_read?0x80u:0u));
    result=poll(t,SR_RXNE,SR_RXNE,0); if (result!=BR_OK) goto cleanup;
    (void)load(r->spi+SPI_DR);
    result=poll(t,SR_TXE,SR_TXE,SR_RXNE); if (result!=BR_OK) goto cleanup;
    store(r->spi+SPI_DR,is_read?0xffu:value);
    result=poll(t,SR_RXNE,SR_RXNE,0); if (result!=BR_OK) goto cleanup;
    received=(uint8_t)load(r->spi+SPI_DR);
    result=poll(t,SR_BSY,0,SR_RXNE); if (result!=BR_OK) goto cleanup;
    if (t->stop_epoch && *t->stop_epoch != t->expected_epoch) result=BR_CANCELLED;
cleanup:
    if (restore_lines(r)!=BR_OK) result=BR_TRANSPORT;
    if (result==BR_OK && t->stop_epoch && *t->stop_epoch!=t->expected_epoch) result=BR_CANCELLED;
    #ifdef ACE_EXPERIMENTAL_OFFLINE
    /* One metadata critical section is the publication linearization point:
     * final validity, busy release and the output byte cannot race STOP. No
     * MMIO/poll/scheduling occurs here; nested finish restores prior PRIMASK. */
    uint32_t key=br_platform_enter();
    if(!br_platform_commit_valid(t)) result=BR_CANCELLED;
    br_platform_exchange_finish(t);
    if(result==BR_OK) *out=received;
    else result=fault(t,result);
    br_platform_leave(key);
    return result;
#else
    if (result!=BR_OK) return fault(t,result);
    *out=received;
    return BR_OK;
#endif
}
enum br_result br_read(struct br_transaction *t,uint8_t reg,uint8_t *out) {
    return exchange(t,reg,0xff,out,1);
}
enum br_result br_write(struct br_transaction *t,uint8_t reg,uint8_t value) {
    uint8_t ignored;
    return exchange(t,reg,value,&ignored,0);
}
static enum br_result cleanup_reader(struct br_transaction *t) {
    uint8_t x; enum br_result e;
    /* Cleanup uses the SAME finite budget; never call a blocking stock helper. */
    if ((e=br_write(t,0x01,0))!=BR_OK) return e;
    if ((e=br_read(t,0x0d,&x))!=BR_OK) return e;
    if ((e=br_write(t,0x0d,(uint8_t)(x&0x7f)))!=BR_OK) return e;
    if ((e=br_write(t,0x0a,0x80))!=BR_OK) return e;
    /* Verify only these known cleanup register bits. Does NOT certify crypto,
     * analog state, hardware pin levels, or a complete recovered reader. */
    if ((e=br_read(t,0x01,&x))!=BR_OK) return e;
    if ((x&0x0f)!=0) return fault(t,BR_TRANSPORT);
    if ((e=br_read(t,0x0d,&x))!=BR_OK) return e;
    if (x&0x80) return fault(t,BR_TRANSPORT);
    if ((e=br_read(t,0x0a,&x))!=BR_OK) return e;
    if (x&0x7f) return fault(t,BR_TRANSPORT);
    return BR_OK;
}
enum br_result br_publish(struct br_transaction *t,uint8_t *dst,
    const uint8_t *src,uint32_t n,uint32_t *bits,uint32_t value) {
    if (!dst || !src || n>64) return BR_INVALID;
    uint32_t key=br_platform_enter();
    enum br_result e=validate(t);
    if (e==BR_OK && !br_platform_commit_valid(t)) e=BR_CANCELLED;
    if (e==BR_OK) {
        for (uint32_t i=0;i<n;i++) dst[i]=src[i];
        if (bits) *bits=value;
    } else if (t && t->state && e!=BR_INVALID) e=fault(t,e);
    br_platform_leave(key);
    return e;
}
enum br_result br_transceive_exact(struct br_transaction *t,const uint8_t *tx,
    uint32_t tx_len,uint8_t *rx,uint32_t rx_capacity,uint32_t *rx_bits,
    uint32_t required_bits) {
    enum br_result e=validate(t); uint8_t v,irq,count,last;
    uint8_t staged[64]; uint32_t bits;
    if (e!=BR_OK) return e;
    if (!tx || !rx || !rx_bits || !tx_len || tx_len>64 || !rx_capacity) return BR_INVALID;
#define WRITE(reg,val) do { e=br_write(t,(reg),(val)); if(e!=BR_OK) return e; } while(0)
#define READ(reg,val) do { e=br_read(t,(reg),&(val)); if(e!=BR_OK) return e; } while(0)
    WRITE(0x04,0x7f); WRITE(0x2a,0x80); WRITE(0x02,0xf7);
    WRITE(0x01,0); WRITE(0x0a,0x80);
    for (uint32_t i=0;i<tx_len;i++) WRITE(0x09,tx[i]);
    WRITE(0x01,0x0c); READ(0x0d,v); WRITE(0x0d,(uint8_t)(v|0x80));
    /* No SysTick dependence. Each read consumes >=6 of the common budget. */
    do { READ(0x04,irq); } while (!(irq&0x31));
    if (irq&1) { e=BR_NO_REPLY; goto protocol_fail; }
    READ(0x06,v);
    /* Include CRC bit omitted by the stock error mask. Fail closed on collision. */
    if (v) { e=BR_READER_ERROR; goto protocol_fail; }
    READ(0x0a,count); READ(0x0c,last); last&=7;
    if (!count || count>64 || count>rx_capacity) { e=BR_CAPACITY; goto protocol_fail; }
    bits=last?((uint32_t)count-1)*8+last:(uint32_t)count*8;
    if (required_bits && bits!=required_bits) { e=BR_PROTOCOL; goto protocol_fail; }
    for (uint32_t i=0;i<count;i++) READ(0x09,staged[i]);
    e=cleanup_reader(t); if(e!=BR_OK) return e;
    return br_publish(t,rx,staged,count,rx_bits,bits);
protocol_fail:
    { enum br_result cleaned=cleanup_reader(t); if(cleaned!=BR_OK) return cleaned; }
    return e;
#undef READ
#undef WRITE
}

enum br_result br_transceive(struct br_transaction *t,const uint8_t *tx,
    uint32_t tx_len,uint8_t *rx,uint32_t rx_capacity,uint32_t *rx_bits) {
    return br_transceive_exact(t,tx,tx_len,rx,rx_capacity,rx_bits,0);
}
